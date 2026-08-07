# Crime Risk Score API 

Model aktif: **champion v4**, HistGradientBoostingRegressor, 14 fitur,
MAE holdout **11,37** · RMSE **14,04** · R² **0,310**.

---

## 1. Menjalankan

```bash
pip install -r requirements.txt
python build_serving_artifacts.py
python -m uvicorn app:app --reload --port 8000
```

Buka **http://127.0.0.1:8000/docs** untuk Swagger UI, dokumentasi interaktif yang
ter-generate otomatis dan selalu sinkron dengan kode.

Cek cepat:

```bash
curl "http://127.0.0.1:8000/risk-score?lat=41.8819&lon=-87.6278&datetime=2024-11-18T21:30:00"
```

---

## 2. Kontrak API (for fe)

### CORS — bisa langsung dipanggil dari browser

API ini sudah mengaktifkan **CORS** (`allow_origins=["*"]`), jadi tim FE bisa
`fetch()`/`axios` langsung dari JavaScript di browser ke `http://127.0.0.1:8000`
tanpa perlu proxy backend sendiri, walau origin FE-nya beda (mis. FE jalan di
`localhost:3000`, API di `localhost:8000`). Tanpa ini, browser bakal nge-block
request-nya sendiri sebelum sempat sampai ke server — bukan error dari kode FE.

Kalau nanti API ini di-deploy ke luar (bukan cuma lokal) dan mau lebih ketat,
`allow_origins=["*"]` di `app.py` bisa diganti ke origin spesifik punya FE
(contoh: `["https://app-fe-kalian.com"]`) — koordinasikan dulu sebelum diubah,
karena itu juga bagian dari kontrak.

### Endpoint utama

```
GET /risk-score?lat={float}&lon={float}&datetime={ISO8601}
```

| Parameter  | Tipe   | Wajib | Contoh                | Keterangan |
|------------|--------|-------|-----------------------|------------|
| `lat`      | float  | ya    | `41.8819`             | Latitude, rentang −90…90 |
| `lon`      | float  | ya    | `-87.6278`            | Longitude, rentang −180…180 |
| `datetime` | string | ya    | `2024-11-18T21:30:00` | ISO 8601. Sufiks `Z` / offset didukung |

**Zona waktu:** waktu tanpa offset diperlakukan sebagai **waktu lokal kejadian**,
tidak dikonversi. 
### Response `200 OK`

```json
{
  "risk_score": 57,
  "level": "High",
  "model_version": "v4",
  "last_updated": "2026-08-06",

  "risk_score_raw": 57.03,
  "input": {
    "lat": 41.8819, "lon": -87.6278,
    "datetime": "2024-11-18T21:30:00", "dow": 0, "hour": 21
  },
  "cell_id": "-48445_31081",
  "feature_source": {
    "crime_count": "exact",
    "crime_count_value": 1.0,
    "cell_known": true,
    "in_coverage_area": true
  },
  "warnings": [],
  "request_id": "7850284a6cc1",
  "latency_ms": 12.13
}
```

Empat field pertama adalah **kontrak inti** dan mengikuti persis contoh skema pada
soal. Sisanya bersifat tambahan, aman diabaikan konsumen yang tidak membutuhkan.

| Field | Tipe | Keterangan |
|---|---|---|
| `risk_score` | int | 0–100, dibulatkan. **Gunakan ini untuk tampilan.** |
| `level` | string | `Low` / `Medium` / `High` / `Critical` |
| `model_version` | string | Versi champion, mis. `v4` |
| `last_updated` | string | `YYYY-MM-DD`, tanggal champion dipromosikan |
| `risk_score_raw` | float | Nilai presisi 2 desimal, untuk kebutuhan analitis |
| `cell_id` | string | ID sel grid, berguna untuk caching di sisi klien |
| `feature_source` | object | Seberapa didukung data prediksi ini — lihat §3 |
| `warnings` | array | Kosong kalau tidak ada catatan |
| `request_id` | string | Untuk korelasi dengan `logs/prediction.log` |

### Ambang level

| Level | Rentang skor |
|---|---|
| `Low` | < 25 |
| `Medium` | 25 – <50 |
| `High` | 50 – <75 |
| `Critical` | ≥ 75 |

Dipilih 25/50/75 agar konsisten dengan contoh di soal (skor 74 → `High`) dan mudah
dijelaskan. **Mengubah ambang = mengubah kontrak**, jadi harus disepakati bersama.

### Error `422 Unprocessable Entity`

```json
{ "error": "invalid_input", "detail": "lat=999.0 di luar rentang valid [-90, 90]." }
```

Dipicu oleh: lat/lon di luar rentang atau bukan angka, `datetime` hilang atau tidak
terbaca. Kegagalan tak terduga menghasilkan `500` dengan bentuk yang sama.

### Endpoint lain

| Endpoint | Kegunaan |
|---|---|
| `POST /risk-score/batch` | Sampai 500 titik sekaligus — hemat round-trip untuk pewarnaan peta |
| `GET /health` | Liveness + readiness (model termuat, integritas checkpoint) |
| `GET /model-info` | Metadata model, daftar fitur, metrik holdout, area cakupan |
| `GET /versions` | Riwayat versi v0…v4 dari registry + ringkasan perbaikan |
| `GET /metrics` | Metrik operasional & drift; `?persist=true` menulis snapshot |
| `GET /logs/recent?n=20` | N log prediksi terakhir |

`POST /risk-score/batch` menerima `{"items": [{"lat":…, "lon":…, "datetime":…}, …]}`.
Item yang gagal **tidak** menggagalkan seluruh batch — tiap elemen membawa
`status`-nya sendiri, dan response memuat `succeeded` / `failed`.

---

## 3. Cakupan & kejujuran prediksi

Model dilatih pada data kriminalitas **Chicago** dalam grid ~150 m × 150 m
(19.863 sel). Request di luar itu tetap dijawab `200`, tetapi ditandai jelas:

Field `feature_source.crime_count` menyatakan dari mana fitur volume berasal —
berurut dari paling didukung data ke paling lemah:

| Nilai | Arti |
|---|---|
| `exact` | Ada data historis persis untuk sel + hari + jam tersebut |
| `cell_hour` | Rata-rata sel tersebut pada jam tersebut, lintas hari |
| `cell_mean` | Rata-rata sel tersebut, lintas waktu |
| `global` | Sel belum pernah terlihat — median global. **Paling tidak andal** |

Ditambah `cell_known` dan `in_coverage_area`, plus `warnings` yang terisi kalau
koordinat di luar Chicago atau sel tidak dikenal. Rasio fallback ini juga
diagregasi di `/metrics` sebagai indikator kesehatan.

---

## 4. Monitoring & logging

**Tiga lapis**, dipisah karena umur datanya berbeda:

| Lapis | Lokasi | Isi |
|---|---|---|
| Log mentah | `logs/prediction.log` | JSONL, satu baris per request. Rotasi otomatis di 5 MB |
| Agregat in-memory | `GET /metrics` | Dihitung saat request lewat, murah |
| Snapshot | `monitoring/metrics.json` | Ditulis tiap 10 prediksi + saat shutdown |

Yang dipantau dan alasannya:

- **Traffic & error rate** - sistem hidup dan tidak gagal diam-diam.
- **Latensi p50/p95** - layak dipakai realtime atau tidak. Terukur p50 ≈ 11 ms.
- **Distribusi level** - output yang tiba-tiba `Critical` semua adalah gejala rusak.
- **Rasio fallback fitur** - makin tinggi, makin banyak request menyasar wilayah
  yang datanya tipis.
- **PSI drift output** - kelanjutan dari drift detection CP2. Di sana yang diperiksa
  input; di sini distribusi **hasil prediksi produksi**.
- **Performa antar versi** - MAE/RMSE/R² v0…v4 dibaca dari registry.

Laporan CLI dari file log (berguna setelah restart, saat metrik in-memory kosong):

```bash
python monitoring.py            # ringkasan + tabel performa antar versi
python monitoring.py --tail 20  # plus 20 log terakhir
```

### Kalibrasi detektor drift

Baseline PSI adalah **distribusi prediksi champion pada data latih**, bukan
distribusi label. Alasannya: model regresi menyusut ke rata-rata (prediksi
std ≈ 9,1 vs label std ≈ 16,9), jadi membandingkan output produksi ke label akan
menghasilkan alarm palsu permanen — versi pertama memang menunjukkan PSI 1,60
"drift" pada trafik yang sehat.

Detektor diuji dua arah lewat `simulate_traffic.py`:

| Profil | Rasio fallback | PSI | Status |
|---|---|---|---|
| `--profile matched` (replay baris data latih) | 0,0 % | 0,015 | `stable` |
| `--profile scattered` (banyak sel asing) | 89,4 % | 0,259 | `drift` |

Detektor diam saat trafik menyerupai data latih dan berbunyi saat tidak — itu yang
membuat sinyalnya bisa dipercaya.

```bash
python simulate_traffic.py --n 800 --profile matched
python simulate_traffic.py --n 800 --profile scattered
```

---

## 5. Isi folder

```
cp3/
├── app.py                       REST API (FastAPI) — endpoint, error handling, logging
├── predict.py                   Inti prediksi: lat/lon/waktu -> 14 fitur -> skor -> level
├── feature_assembler.py         Definisi FeatureAssembler untuk unpickle checkpoint
├── monitoring.py                Logger JSONL, agregat metrik, PSI, laporan CLI
├── build_serving_artifacts.py   Menyiapkan artefak serving dari hasil CP2
├── simulate_traffic.py          Trafik sintetis untuk demonstrasi & kalibrasi monitoring
├── test_api.py                  41 pemeriksaan kontrak API
├── requirements.txt
├── models/
│   ├── champion.json            Penunjuk versi aktif + sha256 + metrik
│   ├── registry.json / .csv     Riwayat lengkap v0..v4 (keputusan, drift, metrik)
│   ├── model_v0..v4.joblib      Bundle {model, assembler, feature_names, meta}
│   ├── serving_lookup.joblib    Lookup crime_count, grid, referensi PSI, replay sample
│   └── reference.json           Ringkasan grid & distribusi referensi (mudah dibaca)
├── logs/prediction.log          JSONL per request
└── monitoring/metrics.json      Snapshot metrik terakhir
```

### Bagaimana fitur dirakit saat serving

```
lat, lon, datetime
   │
   ├─ spasial   lat/lon ──► gx = floor(lon/0,00180885), gy = floor(lat/0,00134747)
   │                        cell_id = "{gx}_{gy}"
   ├─ temporal  datetime ─► dow, hour ──► sin/cos siklik + is_weekend
   ├─ volume    (cell_id, dow, hour) ──► crime_count via lookup berjenjang
   └─ agregat   cell_id ──► 6 fitur per-sel dari assembler di dalam checkpoint
                            (n_crimes_fit, density_ratio_fit, mean_severity,
                             max_severity, pct_violent, n_distinct_types)
   │
   └──-> 14 fitur ──-> HistGradientBoosting ──-> clip[0,100] ──-> level
```

Formula grid diturunkan dari data HO1 dan **diverifikasi ulang setiap kali
`build_serving_artifacts.py` dijalankan** — 749.580 pemeriksaan, 0 ketidakcocokan.
Kalau formula meleset, build sengaja dihentikan daripada melayani sel yang salah.

Enam fitur agregat per-sel **tidak** disimpan ulang: keduanya sudah ada di dalam
objek `assembler` yang ikut dibundel pada tiap checkpoint. Ini yang menjamin
representasi saat training identik dengan saat serving.

---

## 6. Melatih ulang & mengganti versi

1. Jalankan `finpro_cp2.ipynb` sampai selesai → menulis `../models/`.
2. `python build_serving_artifacts.py` → menyalin ke `cp3/models/` dan membangun
   ulang lookup + referensi PSI.
3. Restart server.

API selalu melayani versi yang ditunjuk `champion.json`. Saat startup, sha256
checkpoint dicocokkan dengan yang tercatat; kalau tidak cocok, server menolak
menyala daripada melayani artefak yang mungkin rusak atau tertimpa.

---

## 7. Batasan yang diketahui

- **R² 0,310.** Model menjelaskan sebagian variasi saja; berguna untuk peringkat
  relatif antarlokasi, bukan angka absolut yang presisi. MAE per pita menunjukkan
  error terbesar di ujung skala (≈18,5 pada 0–20 dan ≈20,3 pada 80–100) — skor
  ekstrem cenderung ditarik ke tengah.
- **Hanya Chicago.** Di luar bounding box, prediksi memakai fallback dan tidak
  bermakna. Ditandai lewat `warnings` dan `in_coverage_area`.
- **`crime_count` diestimasi saat serving.** Untuk waktu di masa depan nilai
  sebenarnya belum ada, jadi dipakai histori sel tersebut. Ini diakui terbuka lewat
  `feature_source`, bukan disembunyikan.
- **Belum ada autentikasi / rate limit.** Sesuai skala tugas; wajib ditambahkan
  sebelum dipakai publik.
- **Metrik in-memory hilang saat restart.** `monitoring/metrics.json` dan file log
  tetap ada, dan `python monitoring.py` bisa merekonstruksi agregat dari log.
