

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import Counter, deque
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
MONITOR_DIR = os.path.join(HERE, "monitoring")
LOG_PATH = os.path.join(LOG_DIR, "prediction.log")
METRICS_PATH = os.path.join(MONITOR_DIR, "metrics.json")

MAX_LOG_BYTES = 5 * 1024 * 1024  # rotasi sederhana; cukup untuk skala program ini
KEEP_LAST_SCORES = 5000          # jendela geser untuk PSI & sebaran skor


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PredictionLogger:
    """Append-only JSONL, aman dipanggil dari banyak thread."""

    def __init__(self, path: str = LOG_PATH, max_bytes: int = MAX_LOG_BYTES):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _rotate_if_needed(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                os.replace(self.path, f"{self.path}.{stamp}")
        except OSError:
            pass  # logging tidak boleh menjatuhkan request

    def write(self, record: dict):
        with self._lock:
            self._rotate_if_needed()
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass

    def tail(self, n: int = 50) -> list:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            lines = deque(f, maxlen=n)
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out


def population_stability_index(expected_freq, actual_counts, eps=1e-6) -> float:
    """PSI antara distribusi referensi (training) dan aktual (live).

    Konvensi umum: <0.10 stabil, 0.10-0.25 perlu diperhatikan, >0.25 drift nyata.
    """
    actual = np.asarray(actual_counts, dtype=float)
    total = actual.sum()
    if total == 0:
        return 0.0
    a = np.clip(actual / total, eps, None)
    e = np.clip(np.asarray(expected_freq, dtype=float), eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


class RuntimeMetrics:
    """Agregat in-memory untuk endpoint /metrics."""

    def __init__(self, reference: dict | None = None):
        self._lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc)
        self.total_requests = 0
        self.total_predictions = 0
        self.total_errors = 0
        self.errors_by_type = Counter()
        self.levels = Counter()
        self.cc_sources = Counter()
        self.out_of_coverage = 0
        self.unknown_cell = 0
        self.latencies_ms = deque(maxlen=KEEP_LAST_SCORES)
        self.scores = deque(maxlen=KEEP_LAST_SCORES)
        self.last_prediction_at = None
        self.reference = reference or {}

    def record_prediction(self, result: dict, latency_ms: float):
        with self._lock:
            self.total_requests += 1
            self.total_predictions += 1
            self.levels[result["level"]] += 1
            fs = result.get("feature_source", {})
            self.cc_sources[fs.get("crime_count", "unknown")] += 1
            if not fs.get("in_coverage_area", True):
                self.out_of_coverage += 1
            elif not fs.get("cell_known", True):
                self.unknown_cell += 1
            self.latencies_ms.append(latency_ms)
            self.scores.append(float(result["risk_score_raw"]))
            self.last_prediction_at = _utcnow()

    def record_error(self, err_type: str):
        with self._lock:
            self.total_requests += 1
            self.total_errors += 1
            self.errors_by_type[err_type] += 1

    def _score_drift(self):
        # Baseline = distribusi PREDIKSI champion pada data latih, bukan distribusi
        # label. Model menyusut ke rata-rata, jadi membandingkan prediksi produksi
        # ke label asli akan selalu tampak "drift" walau sistem sehat.
        ref = (self.reference or {}).get("risk_score_pred") or {}
        edges, freq = ref.get("hist_edges"), ref.get("hist_freq")
        if not edges or not freq or len(self.scores) < 30:
            return {
                "psi": None,
                "status": "insufficient_data",
                "n_scores": len(self.scores),
                "note": "PSI dihitung setelah minimal 30 prediksi terkumpul.",
            }
        counts, _ = np.histogram(np.asarray(self.scores), bins=edges)
        psi = population_stability_index(freq, counts)
        status = "stable" if psi < 0.10 else ("watch" if psi < 0.25 else "drift")
        return {
            "psi": round(psi, 4),
            "status": status,
            "n_scores": len(self.scores),
            "thresholds": {"stable": "<0.10", "watch": "0.10-0.25", "drift": ">0.25"},
            "live_mean": round(float(np.mean(self.scores)), 2),
            "baseline_mean": round(float(ref.get("mean", 0.0)), 2),
            "baseline": ref.get("source", "prediksi champion pada data latih"),
        }

    def snapshot(self, scorer=None) -> dict:
        with self._lock:
            lat = np.asarray(self.latencies_ms) if self.latencies_ms else np.array([])
            uptime = (datetime.now(timezone.utc) - self.started_at).total_seconds()
            snap = {
                "generated_at": _utcnow(),
                "service": {
                    "started_at": self.started_at.isoformat(),
                    "uptime_seconds": round(uptime, 1),
                    "last_prediction_at": self.last_prediction_at,
                },
                "traffic": {
                    "total_requests": self.total_requests,
                    "total_predictions": self.total_predictions,
                    "total_errors": self.total_errors,
                    "error_rate": round(self.total_errors / self.total_requests, 4)
                    if self.total_requests else 0.0,
                    "errors_by_type": dict(self.errors_by_type),
                    "requests_per_minute": round(self.total_requests / (uptime / 60), 2)
                    if uptime > 5 else None,
                },
                "latency_ms": {
                    "count": int(lat.size),
                    "mean": round(float(lat.mean()), 2) if lat.size else None,
                    "p50": round(float(np.percentile(lat, 50)), 2) if lat.size else None,
                    "p95": round(float(np.percentile(lat, 95)), 2) if lat.size else None,
                    "max": round(float(lat.max()), 2) if lat.size else None,
                },
                "predictions": {
                    "level_distribution": dict(self.levels),
                    "score_mean": round(float(np.mean(self.scores)), 2) if self.scores else None,
                    "score_min": round(float(np.min(self.scores)), 2) if self.scores else None,
                    "score_max": round(float(np.max(self.scores)), 2) if self.scores else None,
                },
                "data_quality": {
                    "crime_count_source": dict(self.cc_sources),
                    "out_of_coverage_requests": self.out_of_coverage,
                    "unknown_cell_requests": self.unknown_cell,
                    "fallback_rate": round(
                        1 - self.cc_sources.get("exact", 0) / max(self.total_predictions, 1), 4),
                },
                "output_drift": self._score_drift(),
            }

        if scorer is not None:
            snap["model"] = {
                "champion_version": scorer.model_version,
                "last_updated": scorer.last_updated,
                "integrity_verified": scorer.integrity_ok,
                "holdout_metrics": {
                    k: (round(v, 4) if isinstance(v, (int, float)) else v)
                    for k, v in (scorer.champion.get("metrics") or {}).items()
                    if k != "MAE_per_band"
                },
            }
            snap["model_history"] = model_history(scorer)
        return snap

    def persist(self, scorer=None, path: str = METRICS_PATH) -> dict:
        snap = self.snapshot(scorer)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)
        return snap


def model_history(scorer) -> dict:
    """Perkembangan performa antar versi model, dibaca dari registry CP2."""
    rows = [v for v in scorer.versions() if v["MAE"] is not None]
    if not rows:
        return {"versions": [], "note": "registry belum berisi metrik."}
    first, last = rows[0], rows[-1]
    return {
        "versions": rows,
        "n_versions": len(rows),
        "champion": scorer.model_version,
        "improvement": {
            "MAE": {"from": round(first["MAE"], 4), "to": round(last["MAE"], 4),
                    "delta": round(last["MAE"] - first["MAE"], 4),
                    "pct": round(100 * (last["MAE"] - first["MAE"]) / first["MAE"], 2)},
            "R2": {"from": round(first["R2"], 4), "to": round(last["R2"], 4),
                   "delta": round(last["R2"] - first["R2"], 4)},
        },
        "retrain_events": sum(1 for v in scorer.versions() if v["drift_detected"]),
        "skipped_no_drift": sum(1 for v in scorer.versions()
                                if v["decision"] == "skip_retrain_no_drift"),
    }


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def summarize_log(path: str = LOG_PATH, limit: int | None = None) -> dict:
    """Agregasi dari file log - berguna setelah restart, saat metrik in-memory kosong.

    Dipakai oleh `python monitoring.py` untuk laporan CLI.
    """
    if not os.path.exists(path):
        return {"error": f"log tidak ditemukan: {path}"}

    n, errors = 0, 0
    levels, sources, versions = Counter(), Counter(), Counter()
    lats, scores, first_ts, last_ts = [], [], None, None

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            first_ts = first_ts or r.get("timestamp")
            last_ts = r.get("timestamp")
            if r.get("status") != "ok":
                errors += 1
                continue
            versions[r.get("model_version")] += 1
            # Record ringkasan /risk-score/batch tidak membawa level atau
            # feature_source; kalau ikut dihitung akan muncul kunci null palsu.
            if r.get("level") is None:
                continue
            levels[r["level"]] += 1
            sources[(r.get("feature_source") or {}).get("crime_count")] += 1
            if r.get("latency_ms") is not None:
                lats.append(r["latency_ms"])
            if r.get("risk_score_raw") is not None:
                scores.append(r["risk_score_raw"])
            if limit and n >= limit:
                break

    lat = np.asarray(lats) if lats else np.array([])
    return {
        "log_path": path,
        "total_records": n,
        "errors": errors,
        "error_rate": round(errors / n, 4) if n else 0.0,
        "window": {"first": first_ts, "last": last_ts},
        "level_distribution": dict(levels),
        "crime_count_source": dict(sources),
        "model_versions_served": dict(versions),
        "latency_ms": {
            "mean": round(float(lat.mean()), 2) if lat.size else None,
            "p50": round(float(np.percentile(lat, 50)), 2) if lat.size else None,
            "p95": round(float(np.percentile(lat, 95)), 2) if lat.size else None,
        },
        "score_mean": round(float(np.mean(scores)), 2) if scores else None,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Laporan monitoring dari log prediksi.")
    ap.add_argument("--tail", type=int, default=0, help="tampilkan N log terakhir")
    args = ap.parse_args()

    print("=" * 68)
    print("RINGKASAN LOG PREDIKSI")
    print("=" * 68)
    print(json.dumps(summarize_log(), indent=2, ensure_ascii=False))

    try:
        from predict import get_scorer
        print("\n" + "=" * 68)
        print("PERFORMA ANTAR VERSI MODEL (dari registry)")
        print("=" * 68)
        h = model_history(get_scorer())
        print(f"{'versi':>6} {'batch':>6} {'n_train':>9} {'MAE':>8} {'RMSE':>8} "
              f"{'R2':>7}  keputusan")
        for v in h["versions"]:
            star = " *" if v["is_champion"] else "  "
            print(f"{v['version']:>6} {v['batch_index']:>6} {v['train_size']:>9,} "
                  f"{v['MAE']:>8.3f} {v['RMSE']:>8.3f} {v['R2']:>7.3f}  "
                  f"{v['decision']}{star}")
        imp = h["improvement"]
        print(f"\nMAE {imp['MAE']['from']:.3f} -> {imp['MAE']['to']:.3f} "
              f"({imp['MAE']['pct']:+.1f}%) | R2 {imp['R2']['from']:.3f} -> {imp['R2']['to']:.3f}")
        print("* = champion aktif")
    except Exception as exc:
        print(f"\n(riwayat model dilewati: {type(exc).__name__}: {exc})")

    if args.tail:
        print("\n" + "=" * 68)
        print(f"{args.tail} LOG TERAKHIR")
        print("=" * 68)
        for r in PredictionLogger().tail(args.tail):
            print(json.dumps(r, ensure_ascii=False))
