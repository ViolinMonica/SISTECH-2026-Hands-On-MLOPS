"""Inti prediksi Risk Score - lepas dari framework web.

Alur satu request:

    lat, lon, datetime
        |
        |  1. spasial : lat/lon -> indeks grid (gx, gy) -> cell_id
        |  2. temporal: datetime -> dow, hour -> encoding siklik sin/cos + is_weekend
        |  3. volume  : (cell_id, dow, hour) -> crime_count lewat lookup berjenjang
        |  4. agregat : cell_id -> 6 fitur per-sel, diambil dari assembler di checkpoint
        v
    vektor 14 fitur  ->  HistGradientBoostingRegressor  ->  risk_score (0-100)  ->  level

Dipisah dari app.py supaya logika model bisa diuji dan dipakai ulang tanpa
menyalakan server (lihat blok __main__ di bawah untuk smoke test cepat).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd

from feature_assembler import register_for_unpickle

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(HERE, "models")

# Ambang level. Dipilih 25/50/75 pada skala 0-100 supaya konsisten dengan contoh
# skema di soal (risk_score 74 -> "High") sekaligus mudah dijelaskan ke konsumen
# API. Perubahan ambang = perubahan kontrak, jadi dikoordinasikan dengan tim.
LEVEL_BANDS = [(25.0, "Low"), (50.0, "Medium"), (75.0, "High"), (float("inf"), "Critical")]


def score_to_level(score: float) -> str:
    for upper, name in LEVEL_BANDS:
        if score < upper:
            return name
    return LEVEL_BANDS[-1][1]


class InputError(ValueError):
    """Input request tidak valid - dipetakan ke HTTP 422 oleh app.py."""


def parse_datetime(value: str) -> datetime:
    """Terima ISO 8601. Naive dianggap waktu lokal kejadian (tanpa konversi zona).

    Alasan tidak memaksa UTC: fitur temporal model adalah jam & hari LOKAL kota
    (pola kriminalitas mengikuti ritme lokal), jadi menggeser ke UTC justru salah.
    Kalau konsumen mengirim offset eksplisit, offset dihormati apa adanya.
    """
    if not isinstance(value, str) or not value.strip():
        raise InputError("Parameter 'datetime' wajib diisi (format ISO 8601).")
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise InputError(
            f"Format datetime tidak dikenali: {value!r}. "
            "Gunakan ISO 8601, contoh: 2024-11-18T21:30:00"
        )
    return dt


def validate_latlon(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise InputError("Parameter 'lat' dan 'lon' harus berupa angka.")
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise InputError("Parameter 'lat'/'lon' tidak boleh NaN atau tak hingga.")
    if not -90.0 <= lat <= 90.0:
        raise InputError(f"lat={lat} di luar rentang valid [-90, 90].")
    if not -180.0 <= lon <= 180.0:
        raise InputError(f"lon={lon} di luar rentang valid [-180, 180].")
    return lat, lon


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class RiskScorer:
    """Memuat checkpoint champion + lookup serving, lalu melayani prediksi."""

    def __init__(self, models_dir: str = MODELS_DIR, verify_hash: bool = True):
        self.models_dir = models_dir
        register_for_unpickle()  # WAJIB sebelum joblib.load (lihat feature_assembler.py)

        with open(os.path.join(models_dir, "champion.json"), encoding="utf-8") as f:
            self.champion = json.load(f)

        # champion.json menyimpan path bergaya Windows dari notebook; ambil nama
        # filenya saja supaya bundle tetap ketemu di mana pun cp3/ ditaruh.
        ckpt_name = os.path.basename(self.champion["checkpoint_path"].replace("\\", "/"))
        self.checkpoint_path = os.path.join(models_dir, ckpt_name)
        if not os.path.exists(self.checkpoint_path):
            raise FileNotFoundError(
                f"Checkpoint champion tidak ada: {self.checkpoint_path}. "
                "Jalankan build_serving_artifacts.py lebih dulu."
            )

        self.integrity_ok = None
        if verify_hash and self.champion.get("checkpoint_sha256"):
            self.integrity_ok = _sha256(self.checkpoint_path) == self.champion["checkpoint_sha256"]
            if not self.integrity_ok:
                raise RuntimeError(
                    f"sha256 {ckpt_name} tidak cocok dengan champion.json - "
                    "artefak kemungkinan rusak atau tertimpa."
                )

        bundle = joblib.load(self.checkpoint_path)
        self.model = bundle["model"]
        self.assembler = bundle["assembler"]
        self.feature_names = list(bundle["feature_names"])
        self.version = int(bundle["version"])

        lut = joblib.load(os.path.join(models_dir, "serving_lookup.joblib"))
        self.lat_step = lut["grid"]["lat_step"]
        self.lon_step = lut["grid"]["lon_step"]
        self.bbox = lut["grid"]["bbox"]
        self._cc_exact = lut["crime_count"]["exact"]
        self._cc_cell_hour = lut["crime_count"]["cell_hour"]
        self._cc_cell_mean = lut["crime_count"]["cell_mean"]
        self._cc_global = lut["crime_count"]["global"]
        self._cell_info = lut["cell_info"]
        self.reference = lut["reference"]
        self.replay_sample = lut.get("replay_sample")

        with open(os.path.join(models_dir, "registry.json"), encoding="utf-8") as f:
            self.registry = json.load(f)

        self.loaded_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ meta
    @property
    def model_version(self) -> str:
        return f"v{self.version}"

    @property
    def last_updated(self) -> str:
        """Tanggal champion terakhir dipromosikan (bukan waktu server start)."""
        ts = self.champion.get("updated_utc")
        if ts:
            try:
                return datetime.fromisoformat(ts).date().isoformat()
            except ValueError:
                pass
        return datetime.fromtimestamp(
            os.path.getmtime(self.checkpoint_path), timezone.utc).date().isoformat()

    def model_info(self) -> dict:
        return {
            "model_version": self.model_version,
            "algorithm": type(self.model).__name__,
            "target": "risk_score (0-100)",
            "n_features": len(self.feature_names),
            "feature_names": self.feature_names,
            "assembler_config": self.assembler.config(),
            "trained_on_rows": int(getattr(self.assembler, "n_fit_rows_", 0)),
            "known_cells": int(getattr(self.assembler, "n_fit_cells_", 0)),
            "holdout_metrics": self.champion.get("metrics", {}),
            "checkpoint": os.path.basename(self.checkpoint_path),
            "checkpoint_sha256": self.champion.get("checkpoint_sha256"),
            "integrity_verified": self.integrity_ok,
            "last_updated": self.last_updated,
            "loaded_at": self.loaded_at.isoformat(),
            "coverage_area": self.bbox,
            "level_bands": {
                "Low": "< 25", "Medium": "25 - <50", "High": "50 - <75", "Critical": ">= 75"},
        }

    def versions(self) -> list:
        """Riwayat versi dari registry - dipakai endpoint /versions & monitoring."""
        out = []
        for e in self.registry:
            m = e.get("metrics") or {}
            out.append({
                "version": (None if e.get("version") is None else f"v{e['version']}"),
                "batch_index": e.get("batch_index"),
                "train_size": e.get("train_size"),
                "decision": e.get("decision"),
                "drift_detected": e.get("drift_detected"),
                "MAE": m.get("MAE"), "RMSE": m.get("RMSE"), "R2": m.get("R2"),
                "is_champion": e.get("version") == self.champion.get("version"),
            })
        return out

    # -------------------------------------------------------------- features
    def to_cell(self, lat: float, lon: float) -> dict:
        """lat/lon -> sel grid. Formula diverifikasi eksak terhadap data HO1."""
        gy = int(math.floor(lat / self.lat_step))
        gx = int(math.floor(lon / self.lon_step))
        return {
            "cell_id": f"{gx}_{gy}",
            "gx": gx, "gy": gy,
            "lat_r": self.lat_step * (gy + 0.5),
            "lon_r": self.lon_step * (gx + 0.5),
        }

    def lookup_crime_count(self, cell_id: str, dow: int, hour: int):
        """Estimasi crime_count berjenjang; kembalikan (nilai, sumber)."""
        v = self._cc_exact.get((cell_id, dow, hour))
        if v is not None:
            return float(v), "exact"

        arr = self._cc_cell_hour.get(cell_id)
        if arr is not None:
            hv = arr[hour]
            if not np.isnan(hv):
                return float(hv), "cell_hour"

        v = self._cc_cell_mean.get(cell_id)
        if v is not None:
            return float(v), "cell_mean"

        return float(self._cc_global), "global"

    def build_features(self, lat: float, lon: float, dt: datetime) -> tuple:
        cell = self.to_cell(lat, lon)
        dow, hour = dt.weekday(), dt.hour  # Senin=0 .. Minggu=6, sesuai HO1
        crime_count, cc_source = self.lookup_crime_count(cell["cell_id"], dow, hour)

        row = pd.DataFrame([{
            "cell_id": cell["cell_id"],
            "lat_r": cell["lat_r"],
            "lon_r": cell["lon_r"],
            "hour_sin": math.sin(2 * math.pi * hour / 24),
            "hour_cos": math.cos(2 * math.pi * hour / 24),
            "dow_sin": math.sin(2 * math.pi * dow / 7),
            "dow_cos": math.cos(2 * math.pi * dow / 7),
            "is_weekend": int(dow >= 5),
            "crime_count": crime_count,
        }])

        # Assembler mengisi 6 fitur agregat per-sel dari cell_stats_ hasil training,
        # dan otomatis fallback ke median kalau selnya tak dikenal.
        X = self.assembler.transform(row)
        meta = {**cell, "dow": dow, "hour": hour,
                "crime_count": crime_count, "crime_count_source": cc_source}
        return X, meta

    def in_coverage(self, lat, lon) -> bool:
        b = self.bbox
        pad_lat, pad_lon = self.lat_step, self.lon_step
        return (b["lat_min"] - pad_lat <= lat <= b["lat_max"] + pad_lat
                and b["lon_min"] - pad_lon <= lon <= b["lon_max"] + pad_lon)

    # --------------------------------------------------------------- predict
    def predict(self, lat, lon, datetime_str) -> dict:
        lat, lon = validate_latlon(lat, lon)
        dt = parse_datetime(datetime_str)

        X, meta = self.build_features(lat, lon, dt)
        raw = float(self.model.predict(X)[0])
        score = float(np.clip(raw, 0.0, 100.0))  # target HO1 terdefinisi di [0,100]

        warnings = []
        known_cell = meta["cell_id"] in self._cell_info
        if not self.in_coverage(lat, lon):
            warnings.append(
                "Koordinat di luar area cakupan model (Chicago). "
                "Prediksi memakai nilai fallback dan tidak dapat diandalkan.")
        elif not known_cell:
            warnings.append(
                "Sel ini tidak pernah muncul di data latih; fitur agregat memakai "
                "median global sehingga ketidakpastian lebih tinggi.")
        if raw != score:
            warnings.append(f"Prediksi mentah {raw:.2f} dipotong ke rentang [0, 100].")

        return {
            # --- kontrak inti, sesuai contoh skema di soal ---
            "risk_score": int(round(score)),
            "level": score_to_level(score),
            "model_version": self.model_version,
            "last_updated": self.last_updated,
            # --- tambahan: presisi & konteks untuk konsumen ---
            "risk_score_raw": round(score, 2),
            "input": {"lat": lat, "lon": lon,
                      "datetime": dt.isoformat(), "dow": meta["dow"], "hour": meta["hour"]},
            "cell_id": meta["cell_id"],
            "feature_source": {
                "crime_count": meta["crime_count_source"],
                "crime_count_value": round(meta["crime_count"], 3),
                "cell_known": known_cell,
                "in_coverage_area": self.in_coverage(lat, lon),
            },
            "warnings": warnings,
        }


_scorer: RiskScorer | None = None


def get_scorer() -> RiskScorer:
    """Singleton - model dimuat sekali per proses, bukan per request."""
    global _scorer
    if _scorer is None:
        _scorer = RiskScorer()
    return _scorer


if __name__ == "__main__":
    s = get_scorer()
    print(f"Champion {s.model_version} | {len(s.feature_names)} fitur | "
          f"MAE holdout={s.champion['metrics']['MAE']:.3f}")
    print(f"Integritas sha256: {'OK' if s.integrity_ok else 'TIDAK DIPERIKSA'}\n")

    samples = [
        ("Loop / downtown", 41.8819, -87.6278, "2024-11-18T21:30:00"),
        ("Loop / downtown", 41.8819, -87.6278, "2024-11-18T05:00:00"),
        ("Hyde Park", 41.7943, -87.5907, "2024-11-16T23:00:00"),
        ("Ujung barat laut", 41.9800, -87.9000, "2024-11-20T08:00:00"),
        ("Luar cakupan (Jakarta)", -6.2000, 106.8166, "2024-11-18T21:30:00"),
    ]
    for name, la, lo, ts in samples:
        r = s.predict(la, lo, ts)
        print(f"{name:24s} -> {r['risk_score']:>3d} ({r['level']:<8s}) "
              f"cell={r['cell_id']:<14s} cc={r['feature_source']['crime_count']}")
        for w in r["warnings"]:
            print(f"{'':26s}! {w}")
