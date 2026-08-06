"""Uji kontrak API - dijalankan tanpa perlu menyalakan server terpisah.

    python test_api.py

Memakai TestClient FastAPI supaya hasilnya deterministik dan bisa dijalankan di
mana saja. Fokusnya kontrak yang dipakai tim konsumen: bentuk response, tipe
data, penanganan input salah, dan efek samping (log + metrik) benar-benar terjadi.
"""

import json
import sys

from fastapi.testclient import TestClient

from app import app

PASS, FAIL = "  OK  ", " GAGAL"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    return cond


def main():
    with TestClient(app) as c:
        r = c.get("/health")
        check("GET /health -> 200", r.status_code == 200, r.text[:200])
        check("health: model termuat", r.json().get("model_loaded") is True)
        check("health: integritas checkpoint terverifikasi",
              r.json().get("integrity_verified") is True)
        r = c.get("/risk-score",
                  params={"lat": 41.8819, "lon": -87.6278, "datetime": "2024-11-18T21:30:00"})
        check("GET /risk-score -> 200", r.status_code == 200, r.text[:300])
        body = r.json()
        print("\n    contoh response:")
        print("    " + json.dumps(
            {k: body[k] for k in ["risk_score", "level", "model_version", "last_updated"]},
            indent=4).replace("\n", "\n    "))
        print()

        for field in ["risk_score", "level", "model_version", "last_updated"]:
            check(f"response memuat '{field}' (sesuai contoh skema soal)", field in body)
        check("risk_score bertipe int", isinstance(body["risk_score"], int),
              type(body["risk_score"]).__name__)
        check("risk_score dalam [0,100]", 0 <= body["risk_score"] <= 100, body["risk_score"])
        check("level nilainya sah",
              body["level"] in {"Low", "Medium", "High", "Critical"}, body["level"])
        check("model_version berformat vN", body["model_version"].startswith("v"))
        check("last_updated berformat YYYY-MM-DD",
              len(body["last_updated"]) == 10 and body["last_updated"][4] == "-")
        check("ada request_id & latency_ms",
              "request_id" in body and "latency_ms" in body)
        check("ada jejak sumber fitur", "feature_source" in body)

        # ----------------------------------------- konsistensi & sensitivitas
        r2 = c.get("/risk-score",
                   params={"lat": 41.8819, "lon": -87.6278, "datetime": "2024-11-18T21:30:00"})
        check("request identik -> skor identik (deterministik)",
              r2.json()["risk_score"] == body["risk_score"])

        r3 = c.get("/risk-score",
                   params={"lat": 41.8819, "lon": -87.6278, "datetime": "2024-11-18T05:00:00"})
        check("waktu berbeda -> skor berbeda (fitur temporal berpengaruh)",
              r3.json()["risk_score"] != body["risk_score"],
              f"{r3.json()['risk_score']} vs {body['risk_score']}")

        r4 = c.get("/risk-score",
                   params={"lat": 41.7000, "lon": -87.6000, "datetime": "2024-11-18T21:30:00"})
        check("lokasi berbeda -> cell_id berbeda",
              r4.json()["cell_id"] != body["cell_id"])

        # --------------------------------------------------- input tidak valid
        bad_cases = [
            ({"lat": 999, "lon": -87.6, "datetime": "2024-11-18T21:30:00"}, "lat di luar rentang"),
            ({"lat": 41.88, "lon": -87.6, "datetime": "kemarin sore"}, "datetime ngawur"),
            ({"lat": 41.88, "lon": -87.6}, "datetime hilang"),
            ({"lat": "abc", "lon": -87.6, "datetime": "2024-11-18T21:30:00"}, "lat bukan angka"),
        ]
        for params, label in bad_cases:
            rb = c.get("/risk-score", params=params)
            check(f"input salah ditolak 422 ({label})", rb.status_code == 422, rb.status_code)

        # -------------------------------------------------- di luar cakupan
        r5 = c.get("/risk-score",
                   params={"lat": -6.2, "lon": 106.8166, "datetime": "2024-11-18T21:30:00"})
        check("luar cakupan tetap 200 tapi diberi peringatan",
              r5.status_code == 200 and len(r5.json()["warnings"]) > 0)
        check("luar cakupan ditandai in_coverage_area=False",
              r5.json()["feature_source"]["in_coverage_area"] is False)

        # --------------------------------------------------------- batch
        rb = c.post("/risk-score/batch", json={"items": [
            {"lat": 41.8819, "lon": -87.6278, "datetime": "2024-11-18T21:30:00"},
            {"lat": 41.7943, "lon": -87.5907, "datetime": "2024-11-16T23:00:00"},
            {"lat": 41.88, "lon": -87.6, "datetime": "bukan tanggal"},
        ]})
        check("POST /risk-score/batch -> 200", rb.status_code == 200, rb.text[:200])
        bj = rb.json()
        check("batch: 2 sukses, 1 gagal (kegagalan parsial tidak menjatuhkan batch)",
              bj["succeeded"] == 2 and bj["failed"] == 1, f"{bj['succeeded']}/{bj['failed']}")

        # ------------------------------------------------------ model & versi
        r = c.get("/model-info")
        mi = r.json()
        check("GET /model-info -> 200", r.status_code == 200)
        check("model-info: 14 fitur", mi["n_features"] == 14, mi["n_features"])
        check("model-info: algoritma HistGradientBoostingRegressor",
              mi["algorithm"] == "HistGradientBoostingRegressor", mi["algorithm"])

        r = c.get("/versions")
        vj = r.json()
        check("GET /versions -> 200", r.status_code == 200)
        check("riwayat berisi 5 versi (v0..v4)",
              vj["history"]["n_versions"] == 5, vj["history"]["n_versions"])
        check("MAE membaik dari v0 ke champion",
              vj["history"]["improvement"]["MAE"]["delta"] < 0,
              vj["history"]["improvement"]["MAE"]["delta"])

        # -------------------------------------------------------- monitoring
        r = c.get("/metrics", params={"persist": "true"})
        mj = r.json()
        check("GET /metrics -> 200", r.status_code == 200)
        check("metrics: jumlah prediksi tercatat", mj["traffic"]["total_predictions"] > 0,
              mj["traffic"]["total_predictions"])
        check("metrics: error tercatat", mj["traffic"]["total_errors"] > 0,
              mj["traffic"]["total_errors"])
        check("metrics: latensi p95 terisi", mj["latency_ms"]["p95"] is not None)
        check("metrics: distribusi level terisi", len(mj["predictions"]["level_distribution"]) > 0)
        check("metrics: blok drift output ada", "output_drift" in mj)
        check("metrics: riwayat model ikut terlampir", "model_history" in mj)

        r = c.get("/logs/recent", params={"n": 5})
        lj = r.json()
        check("GET /logs/recent -> 200", r.status_code == 200)
        check("log prediksi benar-benar tertulis", lj["count"] > 0, lj["count"])

    # ------------------------------------------------------------- ringkasan
    ok = sum(1 for _, c_, _ in results if c_)
    total = len(results)
    print("\n" + "=" * 68)
    print(f"HASIL: {ok}/{total} pemeriksaan lulus")
    print("=" * 68)
    if ok != total:
        print("\nYang gagal:")
        for name, c_, detail in results:
            if not c_:
                print(f"  - {name}  ({detail})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
