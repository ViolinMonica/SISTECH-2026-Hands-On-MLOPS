# Laporan Hands-On 2: Mesin Prediksi & Continual Learning untuk Risk Score

Laporan ini melanjutkan langsung Hands-On 1. Objek yang sama — `features_labels.csv`
(374.790 baris × 20 kolom; 19 fitur + 1 label `risk_score` 0–100) hasil *pseudo-labeling* HO1 —
kini dipakai untuk membangun mesin prediksi dan siklus continual learning yang menjaga model
tetap relevan saat data baru berdatangan. Seluruh angka pada laporan ini bersumber dari notebook
`Hands_on_2.ipynb` yang menyertai laporan ini (split evaluasi: 80% train / 20% holdout,
`random_state=42`).

Pendekatan yang saya ambil berbeda dari versi baseline tutorial pada setiap komponen inti, dan setiap
perbedaan dijustifikasi berdasarkan bukti dari data, bukan intuisi kompleksitas.

---

## 1. Ringkasan Hasil Evaluasi: Baseline vs Model

### 1.1 Baseline non-ML sebagai patokan

Sebelum melatih model, lima baseline dibangun sebagai patokan minimal. Tiga pertama mengikuti
tutorial; dua terakhir adalah kombinasi ber-*fallback* yang saya tambahkan justru untuk menguji
apakah "lebih granular" berarti "lebih baik".

| Baseline | MAE | RMSE | R² |
| :-- | --: | --: | --: |
| Global Mean | 13,896 | 16,903 | −0,000 |
| per-Sel (spasial) | 12,190 | 15,139 | 0,198 |
| per-(hari, jam) (temporal) | 13,691 | 16,674 | 0,027 |
| per-(sel, jam) + fallback | 13,713 | 17,261 | −0,043 |
| per-(sel, hari, jam) + fallback | 13,713 | 17,261 | −0,043 |

Analisis. Baseline per-Sel adalah yang terkuat, konsisten dengan temuan HO1 bahwa kejahatan
terkonsentrasi secara spasial (38% sel terpadat menampung 80% kejadian). Sinyal "seberapa rawan lokasi
ini secara historis" jauh lebih kuat daripada sinyal temporal murni: baseline per-(hari, jam) hanya
mencapai R² 0,03 — juga konsisten dengan HO1, yang menunjukkan distribusi antar hari nyaris seragam
dan efek jam baru bermakna ketika berinteraksi dengan lokasi.

Temuan paling instruktif justru kegagalan kombinasi: per-(sel, jam) dan per-(sel, hari, jam)
memburuk hingga R² negatif (−0,043), lebih buruk daripada menebak rata-rata global. Penyebabnya
adalah kelangkaan data. Median hanya 12 baris per sel (HO1), tersebar pada 7×24 = 168 slot
(hari, jam), sehingga sebagian besar kombinasi hanya memiliki kurang dari satu baris di data training.
"Rata-rata" pada kombinasi selangka itu tidak lagi menaksir apa pun — ia hanya menghafal nilai satu
observasi berikut noise-nya, lalu gagal menggeneralisasi ke holdout. Fallback berjenjang praktis selalu
jatuh kembali ke level per-Sel, sehingga tidak memberi perbaikan. Pelajaran: granularitas sebuah
*lookup table* dibatasi oleh kepadatan data, bukan oleh imajinasi kita — dan inilah justifikasi empiris
mengapa dibutuhkan model yang menggeneralisasi lewat fitur, bukan menghafal kombinasi. per-Sel
karenanya ditetapkan sebagai baseline pembanding utama.

### 1.2 Model: memisahkan efek fitur dari efek kelas model

Model dilatih bertahap agar efek feature assembly dapat dipisahkan dari efek kelas model.

| Model | Fitur | MAE | RMSE | R² |
| :-- | :-- | --: | --: | --: |
| LinearRegression | 7 (baseline tutorial) | 13,138 | 15,999 | 0,104 |
| LinearRegression | 14 (lengkap) | 12,247 | 15,024 | 0,210 |
| LinearRegression | 14 + `te_cell` | 11,677 | 14,439 | 0,270 |
| RandomForest | 14 | 11,503 | 14,296 | 0,285 |
| HistGradientBoosting | 14 | 11,315 | 13,992 | 0,315 |
| HistGradientBoosting | 14 + `te_cell` | 11,463 | 14,258 | 0,288 |

Analisis.

1. Feature assembly adalah pengungkit terbesar. Model linear dengan 7 fitur baseline tutorial
   (R² 0,104) bahkan kalah dari baseline per-Sel (0,198) — persis fenomena "model kalah baseline"
   yang tutorial minta kita amati. Namun begitu 6 fitur agregat spasial HO1 dikembalikan
   (`n_crimes, mean_severity, max_severity, pct_violent, n_distinct_types, density_ratio`), R² model
   linear melonjak ke 0,210, menyamai baseline. Ini bukti kuantitatif bahwa fitur yang dibuang
   tutorial justru yang paling informatif: korelasinya terhadap `risk_score` (0,18–0,35) jauh melampaui
   koordinat mentah (`lon_r` 0,12).

2. Kelas model non-linear memberi lapisan perbaikan berikutnya. HistGradientBoosting mencapai R²
   0,315 (MAE 11,315), mengungguli Random Forest (0,285) dan seluruh baseline. Interaksi
   lokasi×waktu — yang secara eksplisit ditemukan pada EDA HO1 (efek hari bersifat interaktif terhadap
   jam) — memang lebih tertangkap oleh model pohon daripada oleh regresi linear.

3. "Lebih kompleks" tidak selalu lebih baik — diputuskan dengan bukti. *Target encoding* `cell_id`
   menaikkan model linear (0,210 → 0,270), tetapi justru menurunkan model pohon (0,315 → 0,288).
   Masuk akal: pohon sudah menangkap identitas sel lewat `lat_r/lon_r` + agregat spasial, sehingga
   `te_cell` hanya menambah redundansi dan sedikit overfit. Keputusan final: te_cell dipakai untuk
   model linear, tidak untuk model pohon.

4. Analisis residual. Residual model terbaik tidak bias (rata-rata ≈ 0), tetapi errornya
   berbentuk-U terhadap pita nilai risiko:

   | Band Risk Score | MAE |
   | :-- | --: |
   | 0–20 | 18,42 |
   | 20–40 | 7,57 |
   | 40–60 | 9,81 |
   | 60–80 | 19,01 |
   | 80–100 | 20,09 |

   Ini adalah regression to the mean: model menarik prediksi ke pusat distribusi, sehingga hotspot
   ekstrem *under-predicted* dan sel sangat aman *over-predicted*. Implikasinya penting untuk konteks
   sistem ini: karena keputusan operasional menyasar hotspot (band atas), MAE global yang bagus bisa
   menyesatkan. Saya menilai MAE lebih relevan daripada RMSE di sini (RMSE terlalu didominasi ekor
   yang justru paling *noisy*), namun keduanya wajib dilengkapi error per-band agar performa di
   hotspot terpantau eksplisit.

### 1.3 Kesimpulan evaluasi

Model terbaik mengalahkan semua baseline secara serentak pada MAE, RMSE, dan R². Namun
keunggulannya nyata tanpa dramatis (R² 0,315 vs 0,198), dan plateau di ~0,31 merupakan temuan yang
jujur, bukan kegagalan: `risk_score` HO1 memuat kontribusi *spatial decay* dari sel tetangga dan
*temporal decay* per kejadian yang tidak dapat direkonstruksi dari agregat per-sel yang tersedia di
file ini. Sebagian variansi label memang ireduksibel dari fitur yang ada.

---

## 2. Narasi Continual Learning: Perjalanan Model

### 2.1 Keterbatasan data & pemilihan skema batch

`features_labels.csv` sudah teragregasi per (sel × hari × jam); kolom `Datetime` per-kejadian sudah
melebur dan tidak tersedia. Karena itu batch berdasarkan urutan waktu asli tidak dapat dibentuk
dari file ini — keterbatasan yang sama yang dicatat tutorial.

Tutorial mengatasinya dengan mengacak data lalu menyuntik drift buatan (mengalikan `crime_count`
dan `risk_score`). Saya menilai pendekatan itu kurang jujur karena drift-nya fiktif. Sebagai gantinya,
saya membandingkan tiga skema *batching* dan memilih yang menghasilkan drift nyata dari struktur data
itu sendiri:

| Skema | Cara | Drift (batch-0 vs batch-akhir) |
| :-- | :-- | :-- |
| random | acak (seperti tutorial, tanpa injeksi) | Tidak — batch i.i.d. |
| spatial | rollout geografis barat→timur (`gx`) | Tidak signifikan (moderat) |
| coverage-expansion | `density_ratio` menaik: pilot area sepi → perluasan ke area padat | Ya — kovariat & label |

Skema coverage-expansion yang dipilih mensimulasikan skenario deployment MLOps yang realistis:
sebuah sistem risk-score biasanya dirilis sebagai *pilot* pada sebagian wilayah lalu diperluas secara
bertahap ke wilayah yang lebih padat. Ini menghasilkan covariate drift (distribusi kepadatan &
keragaman kejahatan bergeser naik) sekaligus label drift (rata-rata `risk_score` per batch naik dari
±34 ke ±46) tanpa memalsukan satu nilai pun. Holdout (20% acak, seluruh kota) dibekukan dan dipakai
konsisten untuk mengevaluasi semua versi, agar perbandingan antar-model adil. Batch dipisah dari data
training saja; holdout tidak pernah ikut dilatih.

### 2.2 Perjalanan model dari v0 hingga versi akhir

Model yang dipakai konsisten dengan Bagian 1: HistGradientBoosting, 14 fitur. Registry lengkap
tersimpan di `models/registry.json`.

| Batch | Drift? | Sinyal drift utama (PSI) | MAE kandidat | R² | Keputusan | Champion sesudahnya |
| :-: | :-: | :-- | --: | --: | :-- | --: |
| 0 (v0) | — | (latih awal, area paling sepi) | 12,283 | 0,209 | initial_champion | 12,283 |
| 1 | Ya | density_ratio 7,82 · n_distinct 2,35 | 11,731 | 0,272 | promoted (Δ 0,55) | 11,731 |
| 2 | Ya | + mean_severity 0,20 | 11,635 | 0,283 | kept_champion (Δ 0,10) | 11,731 |
| 3 | Ya | + risk_score 0,22 (label mulai geser) | 11,434 | 0,304 | promoted (Δ 0,30) | 11,434 |
| 4 | Ya | density 8,25 · risk_score 0,36 · crime_count 0,56 | 11,346 | 0,312 | kept_champion (Δ 0,09) | 11,434 |

Champion akhir: MAE 11,434, R² 0,304.

Keputusan di setiap checkpoint, beserta alasannya:

- v0 → Batch 1 (promoted). Champion awal hanya melihat 20% data paling sepi, sehingga performanya
  di holdout se-kota lemah (MAE 12,283 — bahkan sedikit di bawah baseline per-Sel). Batch 1 membawa area
  lebih padat: `detect_drift` menyalakan drift karena PSI `density_ratio` (7,82) dan `n_distinct_types`
  (2,35) jauh melampaui ambang. Retrain kumulatif memangkas MAE ke 11,731 — perbaikan 0,55, jauh di atas
  margin — sehingga dipromosikan.

- Batch 2 (kept_champion). Drift kembali terdeteksi, retrain menghasilkan kandidat 11,635. Namun
  perbaikannya terhadap champion (11,731) hanya 0,096, di bawah *promotion margin* 0,10. Kandidat
  tidak dipromosikan: perbaikan sekecil itu tidak sepadan dengan risiko mengganti model produksi.
  Ini contoh champion/challenger yang bekerja sebagaimana mestinya — menahan *churn*.

- Batch 3 (promoted). Untuk pertama kalinya label ikut bergeser (PSI `risk_score` 0,22 > 0,20),
  bukan hanya fitur — sinyal bahwa hubungan yang dipelajari model betul-betul berubah, bukan sekadar
  komposisi input. Kandidat mencapai 11,434, unggul 0,30 atas champion → dipromosikan. R² naik ke
  0,304.

- Batch 4 (kept_champion). Batch inti terpadat memicu drift terkuat (PSI `risk_score` 0,36,
  `density_ratio` 8,25). Kandidat mencapai MAE terbaik sepanjang perjalanan (11,346), tetapi tidak
  dipromosikan karena unggul hanya 0,088 atas champion — di bawah margin. Ini trade-off yang saya
  catat jujur: aturan margin menolak sebuah perbaikan yang nyata meski kecil (lihat Bagian 4).

Narasi ini menunjukkan siklus yang sehat: drift terdeteksi dari data nyata, retrain memang membantu
ketika data melebar, namun keputusan promosi tetap dijaga disiplin — dua kandidat dipromosikan, dua
ditahan. Berbeda dari baseline tutorial (yang di data acak tidak pernah mendeteksi drift dan retrain-nya
selalu gagal memperbaiki), di sini setiap keputusan dapat ditelusuri ke bukti yang tercatat di registry.

---

## 3. Justifikasi Threshold Deteksi Perubahan Data & Retrain

### 3.1 Mengapa KS saja tidak cukup

Baseline tutorial mendeteksi drift dengan uji Kolmogorov–Smirnov (KS) dua-sampel dan satu ambang
p-value. Persoalannya, tiap batch berukuran ±60.000 baris. Pada sampel sebesar itu KS menjadi terlalu
sensitif: perbedaan distribusi sekecil apa pun menjadi "signifikan secara statistik" (p → 0). Bukti
langsung dari eksperimen saya — pada skema spatial, KS menghasilkan p ≈ 0 di hampir semua kolom
untuk setiap batch. Jika hanya KS yang dipakai, sistem akan retrain terus-menerus hanya karena
ukuran sampel besar, boros komputasi, dan kehilangan makna "drift".

### 3.2 Kriteria gabungan: signifikan DAN bermagnitudo

Saya menambahkan Population Stability Index (PSI) yang mengukur besar pergeseran distribusi
(bukan sekadar ada/tidaknya), lalu mendefinisikan drift hanya jika kedua syarat terpenuhi:

```
drift(kolom)  ⇔  p_value(KS) < 0,01   DAN   PSI > 0,20
```

- α = 0,01 (bukan 0,05 yang lazim) sengaja dibuat lebih ketat justru untuk mengimbangi kepekaan
  KS pada sampel besar.
- PSI > 0,20 mengikuti konvensi industri yang mapan: PSI < 0,1 = stabil, 0,1–0,2 = pergeseran
  moderat, > 0,2 = pergeseran signifikan yang layak ditindaklanjuti.
- Menggabungkan keduanya berarti drift harus signifikan secara statistik SEKALIGUS cukup besar untuk
  penting secara praktis — tepat yang dibutuhkan sebelum menghabiskan komputasi untuk retrain.

### 3.3 Validasi ambang lewat tiga skema

Ambang ini tervalidasi karena berperilaku benar pada ketiga skema (PSI batch-0 vs batch-akhir):

| Kolom | random | spatial | coverage-expansion |
| :-- | --: | --: | --: |
| density_ratio | ~0,00 | ~0,06 | 8,25 |
| n_distinct_types | ~0,00 | ~0,03 | 2,75 |
| risk_score (label) | ~0,00 | ~0,02 | 0,36 |
| Kesimpulan | tidak retrain | tidak retrain | retrain |

- random → PSI ≈ 0: benar, batch i.i.d. tidak membawa apa pun untuk dideteksi. (Inilah sebabnya
  tutorial *terpaksa* menyuntik drift buatan.)
- spatial → KS menyala tapi PSI < 0,2: kriteria gabungan menilainya "moderat, belum layak retrain",
  menghindari retrain sia-sia.
- coverage-expansion → PSI besar pada fitur dan, pada batch akhir, label: drift dinyatakan, retrain
  dijalankan.

### 3.4 Kriteria keputusan retrain → promosi

Deteksi drift hanya memicu retrain; apakah model baru dipakai ditentukan lapisan kedua —
champion/challenger dengan promotion margin:

```
promote(kandidat)  ⇔  MAE_kandidat  ≤  MAE_champion − 0,10
```

Margin 0,10 (poin MAE, ±1% dari skala kesalahan) mencegah *model churn*: mengganti model produksi
membawa biaya dan risiko operasional, sehingga hanya perbaikan yang cukup meyakinkan yang layak
dipromosikan. Perbaikan di bawah margin diperlakukan sebagai kebisingan evaluasi. Trade-off ambang
dirangkum: ambang terlalu longgar → retrain/promosi terlalu sering, boros dan tidak stabil;
ambang terlalu ketat → model usang tak tergantikan. Kombinasi (KS ∧ PSI) untuk *memicu* dan margin
untuk *mempromosikan* menyeimbangkan kedua sisi tersebut.

---

## 4. Refleksi: Kendala dan Solusi

1. Hilangnya sumbu waktu. File HO1 sudah teragregasi sehingga `Datetime` per-kejadian lenyap, dan
   batch berbasis urutan waktu asli mustahil dibentuk. *Solusi:* memilih skema coverage-expansion
   yang memunculkan drift nyata dari struktur data (tanpa memalsukan nilai seperti injeksi tutorial),
   sambil mendokumentasikan bahwa ini adalah simulasi deployment, bukan kronologi asli. *Perbaikan
   ke depan:* kembali ke dataframe per-kejadian HO1 (yang masih memuat `Datetime` sebelum agregasi),
   menyusun batch per bulan/minggu, lalu mengagregasi ulang — menghasilkan drift temporal yang
   sesungguhnya.

2. KS terlalu sensitif pada sampel besar. Pada ±60 rb baris/batch, KS menyalakan "drift" untuk
   perbedaan sekecil apa pun. *Solusi:* menambahkan PSI sebagai penyaring magnitudo dan mensyaratkan
   drift signifikan DAN besar (KS ∧ PSI), serta mengetatkan α ke 0,01.

3. "Lebih kompleks" ternyata lebih buruk. Baseline kombinasi per-(sel, jam) memburuk akibat
   kelangkaan data, dan *target encoding* menurunkan model pohon. *Solusi:* setiap keputusan
   (fitur, encoding, kelas model) diambil berdasarkan bukti holdout, bukan asumsi bahwa yang lebih
   canggih pasti lebih baik. Kesederhanaan yang terjustifikasi dipertahankan (mis. te_cell dibuang dari
   model pohon).

4. Margin promosi menolak perbaikan nyata (trade-off jujur). Kandidat batch-4 (MAE 11,346)
   sesungguhnya sedikit lebih baik dari champion akhir (11,434), namun ditolak karena selisihnya
   (0,088) di bawah margin 0,10. Margin melindungi dari *churn*, tetapi berpotensi menahan perbaikan
   kecil yang valid. *Solusi/alternatif yang dipertimbangkan:* menurunkan margin, atau menilai kandidat
   pada *validation window* bergulir yang lebih stabil sebelum menetapkan keputusan promosi. Dicatat
   sebagai keterbatasan sadar, bukan disembunyikan.

5. Plateau performa (R² ~0,31). Model tidak dapat menembus batas ini karena `risk_score` memuat
   komponen *spatial/temporal decay* yang tak terekonstruksi dari agregat per-sel. *Solusi/temuan:*
   dilaporkan sebagai batas ireduksibel dari representasi fitur yang tersedia — konsisten dengan
   keputusan HO1 yang sengaja tidak menyertakan kolom antara (`risk_raw`, `risk_log`, dst.) demi
   menghindari kebocoran target. Menembus plateau menuntut fitur baru (mis. ringkasan kontribusi
   tetangga), bukan sekadar model yang lebih besar.

---
