"""Siapkan artefak yang dibutuhkan API dari hasil Checkpoint 2.

Dijalankan sekali (atau tiap kali notebook CP2 dilatih ulang):

    python build_serving_artifacts.py

Yang dikerjakan:
  1. Salin checkpoint + registry + champion dari ../models  -> ./models
  2. Bangun ./models/serving_lookup.joblib

KENAPA butuh lookup terpisah?
    Model dilatih pada baris beresolusi (cell_id, dow, hour) dan memakai 14 fitur.
    Dari `assembler` bawaan checkpoint kita sudah dapat 6 fitur agregat per-sel
    (n_crimes_fit, density_ratio_fit, mean_severity, max_severity, pct_violent,
    n_distinct_types) - jadi itu TIDAK perlu disimpan ulang.

    Yang tidak ada di assembler adalah `crime_count`, karena itu fitur per-baris
    (jumlah kejadian di sel tsb pada dow-jam tsb), bukan per-sel. Saat serving,
    request hanya membawa lat/lon/datetime, sehingga crime_count harus diestimasi
    dari histori. Lookup ini menyediakan estimasi berjenjang:

        exact     : crime_count historis persis untuk (cell_id, dow, jam)
        cell_hour : rata-rata crime_count sel tsb pada jam tsb (lintas hari)
        cell_mean : rata-rata crime_count sel tsb (lintas waktu)
        global    : median global, dipakai kalau selnya belum pernah terlihat

    Tingkat fallback yang terpakai ikut dilaporkan API lewat field `feature_source`
    supaya konsumen tahu seberapa didukung data sebuah prediksi.
"""

import json
import os
import shutil
import sys

import joblib
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_MODELS = os.path.abspath(os.path.join(HERE, "..", "models"))
DST_MODELS = os.path.join(HERE, "models")
DATA_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "..", "features_labels.csv")),
    os.path.abspath(os.path.join(HERE, "..", "features_labels.csv")),
    r"C:\Users\violi\OneDrive\Dokumen\Datsci\SISTECH\features_labels.csv",
]

# Konstanta grid, diturunkan dari data HO1 (regresi linear gx/gy -> lon_r/lat_r,
# residual maksimum ~1e-12 sehingga praktis eksak):
#     lat_r = LAT_STEP * (gy + 0.5)   ->   gy = floor(lat / LAT_STEP)
#     lon_r = LON_STEP * (gx + 0.5)   ->   gx = floor(lon / LON_STEP)
LAT_STEP = 0.001347466762486524
LON_STEP = 0.0018088460673608235


def find_data():
    for p in DATA_CANDIDATES:
        if os.path.exists(p):
            return p
    sys.exit(f"features_labels.csv tidak ditemukan. Dicari di: {DATA_CANDIDATES}")


def copy_model_artifacts():
    os.makedirs(DST_MODELS, exist_ok=True)
    if not os.path.isdir(SRC_MODELS):
        sys.exit(f"Folder sumber tidak ada: {SRC_MODELS}\n"
                 "Jalankan dulu notebook finpro_cp2.ipynb sampai selesai.")

    copied = []
    for name in sorted(os.listdir(SRC_MODELS)):
        if name.endswith((".joblib", ".json", ".csv")) and not name.endswith(".tmp"):
            shutil.copy2(os.path.join(SRC_MODELS, name), os.path.join(DST_MODELS, name))
            copied.append(name)
    print(f"[1/2] Disalin {len(copied)} artefak -> models/")
    for n in copied:
        print(f"      - {n} ({os.path.getsize(os.path.join(DST_MODELS, n)):,} bytes)")

    # Placeholder kosong dari scaffolding; membingungkan kalau dibiarkan
    stub = os.path.join(DST_MODELS, "model_v3.json")
    if os.path.exists(stub) and os.path.getsize(stub) == 0:
        os.remove(stub)
        print("      - dihapus placeholder kosong model_v3.json")
    return copied


def _dist(series) -> dict:
    s = np.asarray(series, dtype=float)
    return {
        "mean": float(s.mean()), "std": float(s.std()),
        "p05": float(np.percentile(s, 5)), "p25": float(np.percentile(s, 25)),
        "p50": float(np.percentile(s, 50)), "p75": float(np.percentile(s, 75)),
        "p95": float(np.percentile(s, 95)),
        "hist_edges": np.histogram_bin_edges(s, bins=10, range=(0, 100)).tolist(),
        "hist_freq": (np.histogram(s, bins=10, range=(0, 100))[0] / len(s)).tolist(),
    }


def _prediction_reference(df) -> dict:
    """Distribusi PREDIKSI champion pada data latih - baseline yang benar untuk PSI.

    Kenapa bukan distribusi label saja: model regresi menyusut ke rata-rata
    (R2 champion ~0.31), sehingga sebaran prediksi memang jauh lebih sempit
    daripada sebaran label sebenarnya. Kalau PSI live dibandingkan ke label,
    nilainya akan tinggi terus bahkan saat sistem sehat - alarm palsu permanen.
    Baseline yang sahih adalah "seperti apa keluaran model ini pada data yang
    sudah dikenalnya", lalu drift = keluaran produksi menyimpang dari itu.
    """
    from feature_assembler import register_for_unpickle

    register_for_unpickle()
    champ = json.load(open(os.path.join(DST_MODELS, "champion.json"), encoding="utf-8"))
    ckpt = os.path.join(DST_MODELS,
                        os.path.basename(champ["checkpoint_path"].replace("\\", "/")))
    bundle = joblib.load(ckpt)

    X = bundle["assembler"].transform(df)
    pred = np.clip(bundle["model"].predict(X), 0.0, 100.0)
    out = _dist(pred)
    out["source"] = f"prediksi champion v{bundle['version']} pada {len(df):,} baris data latih"
    print(f"      referensi PSI: prediksi v{bundle['version']} "
          f"mean={out['mean']:.2f} std={out['std']:.2f} "
          f"(label asli mean={df.risk_score.mean():.2f} std={df.risk_score.std():.2f})")
    return out


def build_lookup(data_path):
    df = pd.read_csv(data_path)
    print(f"[2/2] Data dimuat: {df.shape[0]:,} baris / {df.cell_id.nunique():,} sel")

    # Verifikasi ulang formula grid terhadap data (jangan percaya konstanta buta)
    gy_hat = np.floor(df.lat_r.values / LAT_STEP).astype(np.int64)
    gx_hat = np.floor(df.lon_r.values / LON_STEP).astype(np.int64)
    bad = int((gy_hat != df.gy.values).sum() + (gx_hat != df.gx.values).sum())
    if bad:
        sys.exit(f"Formula grid tidak konsisten pada {bad} baris - hentikan.")
    print("      formula grid terverifikasi: 0 ketidakcocokan dari "
          f"{2 * len(df):,} pemeriksaan")

    # --- estimasi crime_count berjenjang ---
    exact = {
        (c, int(d), int(h)): int(v)
        for c, d, h, v in zip(df.cell_id.values, df.dow.values,
                              df.hour.values, df.crime_count.values)
    }

    ch = (df.groupby(["cell_id", "hour"])["crime_count"].mean()
            .unstack("hour").reindex(columns=range(24)))
    cell_hour = {c: row.to_numpy(dtype=np.float32) for c, row in ch.iterrows()}

    cell_mean = df.groupby("cell_id")["crime_count"].mean().astype(np.float32).to_dict()
    global_cc = float(df.crime_count.median())

    # --- info sel untuk pelaporan coverage ---
    cells = df.drop_duplicates("cell_id").set_index("cell_id")
    cell_info = {
        c: (float(r.lat_r), float(r.lon_r), int(r.n_crimes))
        for c, r in cells[["lat_r", "lon_r", "n_crimes"]].iterrows()
    }

    # --- referensi distribusi, dipakai monitoring untuk deteksi drift output ---
    reference = {
        "risk_score_actual": _dist(df.risk_score),
        "risk_score_pred": _prediction_reference(df),
        "n_rows": int(len(df)),
        "n_cells": int(df.cell_id.nunique()),
    }

    bbox = {
        "lat_min": float(df.lat_r.min()), "lat_max": float(df.lat_r.max()),
        "lon_min": float(df.lon_r.min()), "lon_max": float(df.lon_r.max()),
    }

    # --- replay sample: kontrol kalibrasi untuk detektor drift ---
    # Sampel BARIS (bukan sel) agar bobotnya sama dengan distribusi data latih:
    # sel padat memang muncul lebih sering. Menyampel sel secara seragam akan
    # over-represent sel jarang yang skornya rendah, dan membuat detektor drift
    # tampak berbunyi padahal yang salah adalah cara menyampelnya.
    rs = df.sample(n=min(20000, len(df)), random_state=42)
    replay_sample = {
        "lat": rs.lat_r.to_numpy(dtype=np.float64),
        "lon": rs.lon_r.to_numpy(dtype=np.float64),
        "dow": rs.dow.to_numpy(dtype=np.int8),
        "hour": rs.hour.to_numpy(dtype=np.int8),
        "note": "sampel baris acak dari data latih; dipakai simulate_traffic.py --profile matched",
    }
    print(f"      replay sample: {len(rs):,} baris untuk kontrol kalibrasi drift")

    payload = {
        "grid": {"lat_step": LAT_STEP, "lon_step": LON_STEP, "bbox": bbox},
        "crime_count": {
            "exact": exact,
            "cell_hour": cell_hour,
            "cell_mean": cell_mean,
            "global": global_cc,
        },
        "cell_info": cell_info,
        "reference": reference,
        "replay_sample": replay_sample,
        "source_csv": os.path.basename(data_path),
    }

    out = os.path.join(DST_MODELS, "serving_lookup.joblib")
    joblib.dump(payload, out, compress=3)
    print(f"      serving_lookup.joblib -> {os.path.getsize(out):,} bytes")
    print(f"      exact keys={len(exact):,} | cell_hour={len(cell_hour):,} | "
          f"cell_mean={len(cell_mean):,} | global={global_cc}")

    with open(os.path.join(DST_MODELS, "reference.json"), "w", encoding="utf-8") as f:
        json.dump({"grid": payload["grid"], "reference": reference}, f, indent=2)
    return payload


if __name__ == "__main__":
    print("=" * 68)
    print("Build artefak serving CP3")
    print("=" * 68)
    copy_model_artifacts()
    data_path = find_data()
    build_lookup(data_path)

    champ = json.load(open(os.path.join(DST_MODELS, "champion.json"), encoding="utf-8"))
    print("\nSelesai. Champion aktif: v{} | MAE={:.3f} R2={:.3f}".format(
        champ["version"], champ["metrics"]["MAE"], champ["metrics"]["R2"]))
