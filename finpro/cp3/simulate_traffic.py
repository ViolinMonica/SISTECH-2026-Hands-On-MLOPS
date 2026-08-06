"""Bangkitkan trafik sintetis supaya monitoring punya data nyata untuk dilihat.

    python simulate_traffic.py --n 500

Kenapa perlu: metrics.json yang kosong tidak membuktikan apa-apa. Dengan trafik
tiruan yang komposisinya sengaja beragam - titik ramai, titik pinggiran, sel tak
dikenal, koordinat luar kota, dan sebagian request cacat - kita bisa menunjukkan
bahwa monitoring benar-benar menangkap: error rate, sebaran level, tingkat
fallback fitur, latensi, dan PSI drift output.

Ini alat demonstrasi/uji beban ringan, bukan bagian dari jalur produksi.
"""

import argparse
import random
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import app

# Titik acuan di Chicago (perkiraan kasar, cukup untuk membangkitkan trafik)
HOTSPOTS = [
    ("Loop", 41.8819, -87.6278),
    ("Near North", 41.9000, -87.6340),
    ("Austin", 41.8900, -87.7600),
    ("Englewood", 41.7750, -87.6440),
    ("Hyde Park", 41.7943, -87.5907),
    ("Rogers Park", 42.0100, -87.6700),
    ("Midway", 41.7860, -87.7520),
    ("Pilsen", 41.8560, -87.6560),
]
OUT_OF_AREA = [("Jakarta", -6.2000, 106.8166), ("New York", 40.7128, -74.0060)]


# 2024-11-04 adalah hari Senin, sehingga weekday()==0 sesuai konvensi dow di HO1.
MONDAY_BASE = datetime(2024, 11, 4)


def dt_for(dow: int, hour: int) -> str:
    """Susun timestamp yang weekday & jamnya persis seperti baris data latih."""
    return (MONDAY_BASE + timedelta(days=dow, hours=hour)).isoformat()


def random_dt(rng):
    base = datetime(2024, 11, 1)
    return (base + timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 23),
                             minutes=rng.choice([0, 15, 30, 45]))).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="jumlah request")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--profile", choices=["matched", "scattered"], default="scattered",
                    help=("matched  = titik diambil dari sel yang dikenal model "
                          "(kontrol: PSI seharusnya rendah); "
                          "scattered = sebaran acak di sekitar hotspot, banyak "
                          "mengenai sel asing (PSI seharusnya naik)"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    counts = {"ok": 0, "invalid": 0, "out_of_area": 0}
    print(f"Profil trafik: {args.profile}\n")

    with TestClient(app) as c:
        # Untuk profil "matched" ambil koordinat sel yang benar-benar ada di data
        # latih. Ini kontrol kalibrasi: kalau detektor drift tetap berbunyi pada
        # trafik yang mirip data latih, berarti detektornya yang bermasalah.
        replay = None
        if args.profile == "matched":
            from predict import get_scorer
            replay = get_scorer().replay_sample
            if replay is None:
                raise SystemExit("replay_sample tidak ada - jalankan build_serving_artifacts.py")
            print(f"  mereplay {len(replay['lat']):,} baris data latih "
                  f"(berbobot baris, bukan seragam per sel)\n")

        for i in range(args.n):
            roll = rng.random()

            if roll < 0.04:  # request cacat -> menguji error rate
                bad = rng.choice([
                    {"lat": 999, "lon": -87.6, "datetime": random_dt(rng)},
                    {"lat": 41.88, "lon": -87.6, "datetime": "besok malam"},
                    {"lat": 41.88, "lon": -87.6},
                ])
                c.get("/risk-score", params=bad)
                counts["invalid"] += 1
                continue

            if args.profile == "scattered" and roll < 0.07:
                # luar cakupan -> menguji peringatan & fallback
                _, la, lo = rng.choice(OUT_OF_AREA)
                c.get("/risk-score", params={"lat": la, "lon": lo, "datetime": random_dt(rng)})
                counts["out_of_area"] += 1
                continue

            if replay is not None:
                k = rng.randrange(len(replay["lat"]))
                la, lo = float(replay["lat"][k]), float(replay["lon"][k])
                ts = dt_for(int(replay["dow"][k]), int(replay["hour"][k]))
            else:
                # hotspot + jitter: sengaja banyak jatuh di sel yang tak dikenal
                _, la, lo = rng.choice(HOTSPOTS)
                la += rng.gauss(0, 0.012)
                lo += rng.gauss(0, 0.015)
                ts = random_dt(rng)

            c.get("/risk-score", params={"lat": round(la, 6), "lon": round(lo, 6),
                                         "datetime": ts})
            counts["ok"] += 1

            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{args.n} request terkirim")

        snap = c.get("/metrics", params={"persist": "true"}).json()

    print(f"\nKomposisi trafik: {counts}")
    print("=" * 60)
    t, l, p, q, d = (snap["traffic"], snap["latency_ms"], snap["predictions"],
                     snap["data_quality"], snap["output_drift"])
    print(f"total request      : {t['total_requests']}")
    print(f"prediksi berhasil  : {t['total_predictions']}")
    print(f"error              : {t['total_errors']}  (rate {t['error_rate']:.1%})")
    print(f"latensi p50/p95    : {l['p50']} / {l['p95']} ms")
    print(f"sebaran level      : {p['level_distribution']}")
    print(f"rata-rata skor     : {p['score_mean']}")
    print(f"sumber crime_count : {q['crime_count_source']}")
    print(f"fallback rate      : {q['fallback_rate']:.1%}")
    print(f"drift output (PSI) : {d['psi']} -> {d['status']}")
    print("=" * 60)
    print("Snapshot ditulis ke monitoring/metrics.json")


if __name__ == "__main__":
    main()
