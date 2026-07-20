# **Laporan Hands-On 1: Membangun Fondasi Sistem Prediksi Risk Score** 

## **1. Penjelasan Singkat Dataset** 

Dataset yang digunakan adalah Chicago Crimes (2001 hingga sekarang), bersumber dari sistem CLEAR (Citizen Law Enforcement Analysis and Reporting) milik Chicago Police Department dan dipublikasikan melalui Chicago Data Portal. Dataset ini mencatat insiden kejahatan yang dilaporkan, mencakup sekitar 8,5 juta baris pada rentang penuhnya. 

Kolom yang digunakan dalam analisis ini: <mark>`Date` ,</mark> <mark>`Primary Type` ,</mark> <mark>`Description` ,</mark> <mark>`Latitude` ,</mark> <mark>`Longitude` ,</mark> dan <mark>`Year` .</mark> Kolom lain seperti <mark>`Block` ,</mark> <mark>`IUCR` ,</mark> <mark>`Beat` ,</mark> <mark>`District` ,</mark> dan <mark>`FBI Code`</mark> tidak dimuat karena tidak diperlukan untuk membentuk Risk Score, sekaligus untuk menghemat memori mengingat ukuran file mencapai 2,37 GB. 

### **Karakteristik data yang memengaruhi seluruh keputusan desain** 

Dua karakteristik penting dinyatakan secara eksplisit dalam dokumentasi resmi dataset: 

1. Lokasi dianonimkan ke level blok. Dokumentasi menyatakan bahwa "addresses are shown at the block level only and specific locations are not identified" demi melindungi privasi korban. Koordinat lintang dan bujur karenanya tidak menunjukkan titik presisi, melainkan representasi blok kota (sekitar 100 hingga 150 

meter di Chicago). 

2. Waktu kejadian sering merupakan estimasi. Field <mark>`Date`</mark> dideskripsikan sebagai "sometimes a best estimate", yang berarti waktu yang tercatat tidak selalu merupakan waktu pasti terjadinya kejahatan. 

Kedua karakteristik ini menyiratkan bahwa data memiliki resolusi yang terbatas, baik secara spasial maupun temporal. Konsekuensinya, seluruh pendekatan agregasi yang diambil dalam pekerjaan ini (grid berukuran blok, granularitas pada level jam) bukan merupakan penyederhanaan yang mengorbankan informasi, melainkan penyesuaian resolusi analisis terhadap resolusi data yang sebenarnya tersedia. Analisis pada resolusi yang lebih halus tidak akan menangkap informasi tambahan, melainkan hanya noise dari proses anonimisasi dan pembulatan waktu. 

Dokumentasi juga menyatakan bahwa data merupakan insiden yang _dilaporkan_ , bahwa klasifikasi bersifat preliminer dan dapat berubah, serta bahwa data "should not be used for comparison purposes over time". Ketiganya dicatat sebagai limitasi pada bagian akhir laporan ini. 

Sumber: https://data.cityofchicago.org/Public-Safety/Crimes-2001-toPresent/ijzp-q8t2/about_data 

### **Subset yang digunakan** 

Subset mencakup tiga tahun terakhir yang tersedia, yaitu 2024 hingga April 2026, berjumlah 553.919 baris sebelum pembersihan dan 550.896 baris setelahnya. 

Distribusi per tahun: 

|**Tahun**|**Jumlah kejadian**|
|---|---|
|2024|257.531|
|2025|235.620|
|2026|57.745 (hanya Januari hingga April)|



## **Rentang Waktu** 

Subset dapat dilakukan berdasarkan berbagai kriteria, seperti waktu, lokasi, maupun karakteristik data lainnya, bergantung pada tujuan analisis. Pada pekerjaan ini, subset dipilih berdasarkan rentang waktu. 

Mengapa bukan berdasarkan lokasi. Tujuan sistem adalah menghasilkan Risk Score untuk setiap kombinasi lokasi dan waktu di Chicago. Membatasi subset pada satu distrik atau bounding box tertentu akan menghilangkan sebagian wilayah dari analisis, sehingga hasil tidak lagi merepresentasikan kondisi kriminalitas di seluruh kota. Lebih jauh, inti dari sistem ini terletak pada fitur spasial (grid aggregation dan spatial decay), yang justru memerlukan variasi lokasi yang luas agar bermakna. Membatasi wilayah akan membuat fitur-fitur tersebut kurang teruji karena seluruh sel berasal dari area dengan karakter yang serupa. 

Mengapa bukan berdasarkan karakteristik data lainnya. Subset berdasarkan jenis kejahatan atau status penangkapan akan membatasi cakupan analisis pada kelompok kejadian tertentu, menghasilkan gambaran risiko yang parsial dan tidak mencerminkan kondisi kriminalitas secara menyeluruh pada setiap wilayah. 

Mengapa rentang waktu, dan mengapa tiga tahun. Pola kriminalitas bersifat dinamis dan berubah seiring waktu akibat perubahan kondisi ekonomi, kebijakan penegakan hukum, pembangunan wilayah, pandemi, serta pergeseran demografi. Data dari tahun-tahun yang jauh lebih lama, misalnya 2002, belum tentu mencerminkan kondisi kriminalitas saat ini. Karena tujuan sistem adalah mengestimasi risiko pada kondisi terkini, data lama memiliki relevansi yang menurun. 

Argumen ini diperkuat oleh dokumentasi resmi CPD yang secara eksplisit menyatakan bahwa data "should not be used for comparison purposes over time", dengan alasan bahwa klasifikasi dan praktik pencatatan dapat berubah antar periode. Rentang waktu yang lebih sempit karenanya juga mengurangi risiko inkonsistensi definisi antar periode. 

Selain itu, pembatasan rentang waktu bersifat konsisten dengan skema temporal decay yang diterapkan (lihat bagian 4.2). Skema tersebut secara implisit sudah meredam kontribusi data lama. Menyertakan data yang jauh lebih tua karenanya memberikan manfaat marjinal namun menambah beban komputasi secara signifikan. 

Dengan demikian, subset berdasarkan waktu dipilih karena mampu mengurangi ukuran dataset tanpa menghilangkan representasi seluruh wilayah maupun keragaman jenis kejahatan yang menjadi objek analisis. 

## **3. Insight dari EDA** 

### **3.1 Pola waktu bersifat siklikal namun tidak seragam antar hari** 

Kejahatan mencapai titik terendah pada dini hari (jam 4 hingga 6) dan memuncak pada sore hingga malam (jam 15 hingga 19). Variasinya substansial: sekitar 10.000 kejadian pada jam tersepi dibanding 30.000 pada jam tersibuk, atau selisih tiga kali lipat. 

Namun, pola ini tidak identik di seluruh hari. Pada akhir pekan, aktivitas dini hari (jam 1 hingga 4) meningkat secara nyata dibanding hari kerja, sementara puncak sore justru lebih tajam pada hari kerja. Hal ini konsisten dengan perbedaan pola aktivitas sosial antara weekday dan weekend. 

Temuan yang paling penting secara metodologis: kedua efek tersebut saling meniadakan ketika diagregasi ke total harian. Distribusi kejahatan antar hari tampak nyaris seragam, dengan selisih tertinggi ke terendah hanya sekitar 6 persen. Apabila hanya melihat distribusi univariat, kesimpulan yang diambil adalah bahwa hari tidak informatif dan tidak perlu di-encode. Kesimpulan tersebut keliru. Analisis bivariat mengungkap bahwa pengaruh hari bersifat interaktif terhadap jam, bukan aditif. Hal ini menegaskan bahwa analisis univariat saja tidak 

### **3.2 Setiap jenis kejahatan memiliki pola waktu yang berbeda** 

Meskipun seluruh jenis kejahatan berbagi ritme dasar yang sama, yaitu titik terendah pada jam 4 hingga 6 dan peningkatan sepanjang siang, bentuk kurva masing-masing jenis berbeda secara substantif. 

Kontras paling tajam terlihat antara THEFT dan MOTOR VEHICLE THEFT: 

|**Jenis**|**Puncak**|**Nilai terendah**|
|---|---|---|
|THEFT|Jam 12 (sekitar 7%)|Jam 23 (sekitar<br>2,7%)|
|MOTOR VEHICLE<br>THEFT|Jam 22 (sekitar<br>6,7%)|Dini hari|



Keduanya menunjukkan pola yang berlawanan. THEFT memerlukan keramaian dan aktivitas komersial, sehingga memuncak pada tengah hari. MOTOR VEHICLE THEFT memerlukan kondisi gelap dan sepi, sehingga memuncak pada malam hari. BATTERY menunjukkan pola yang lebih datar dengan aktivitas yang tetap tinggi hingga larut malam. 

Implikasi desain: temuan ini membuktikan bahwa komposisi jenis kejahatan bervariasi secara sistematis menurut jam. Konsekuensinya, pembobotan severity bukan sekadar transformasi linier dari jumlah kejadian, melainkan berpotensi mengubah peringkat risiko antar unit waktu. Sebuah jam dengan lebih sedikit kejadian namun didominasi kejahatan berat dapat memperoleh Risk Score lebih tinggi dibanding jam dengan banyak kejadian ringan. Ini merupakan justifikasi empiris bagi penerapan severity scoring. 

### **3.3 Waktu kejadian sering dibulatkan dan diestimasi** 

Distribusi menit kejadian sangat tidak merata. Menit ke-0 muncul 179.214 kali dan menit ke-30 sebanyak 67.766 kali, jauh melampaui frekuensi yang diharapkan bila waktu tercatat secara presisi (sekitar 

9.200 per menit). 

Lebih jauh, pemeriksaan proporsi menit-00 pada setiap jam mengungkap bahwa jam 00 merupakan outlier tunggal: sekitar 52 persen kejadian pada jam tersebut tercatat tepat pada menit ke-00, jauh di atas median jam lainnya (30,7 persen). Hal ini mengindikasikan bahwa <mark>`00:00:00`</mark> tidak hanya terkena pembulatan umum, tetapi juga berfungsi sebagai nilai default ketika waktu kejadian sama sekali tidak diketahui. 

mendeskripsikan field <mark>`Date`</mark> sebagai "sometimes a best estimate". 

Implikasi desain: granularitas temporal dibatasi pada level jam, karena resolusi yang lebih halus tidak didukung oleh kualitas data. Lonjakan kejadian pada jam 00 dalam berbagai visualisasi tidak diinterpretasikan sebagai pola kriminal aktual, melainkan artefak pencatatan. 

### **3.4 Kejahatan terkonsentrasi secara spasial namun tidak ekstrem** 

Dengan grid metrik 150 meter, wilayah Chicago terbagi menjadi 20.125 sel. Kurva konsentrasi kumulatif menunjukkan bahwa sekitar 38 persen sel terpadat menampung 80 persen dari seluruh kejadian. Kejahatan karenanya terkonsentrasi secara nyata, namun tidak ekstrem: pola sebarannya moderat, dengan hotspot yang jelas sekaligus penyebaran yang cukup luas. 

Peta kepadatan hexbin memperlihatkan bahwa konsentrasi tertinggi berada di koridor tengah-utara, yaitu wilayah pusat kota dan sekitarnya, dengan jalur kepadatan tinggi yang memanjang ke selatan. Wilayah tepi kota menunjukkan kepadatan jauh lebih rendah. Peta tersebut menggunakan skala warna logaritmik, bukan sebagai pilihan estetis melainkan sebagai konsekuensi dari sebaran yang sangat timpang: pada skala linear, seluruh kota akan tampak gelap seragam kecuali beberapa bin hotspot. 

Namun, median kejadian per sel hanya 15, dan 19,0 persen sel memiliki kurang dari 5 kejadian. Kepadatan yang tipis ini berarti Risk Score yang 

dihitung semata dari kejadian di dalam sel itu sendiri akan sangat sensitif terhadap fluktuasi kecil. Temuan ini menjadi justifikasi utama penerapan spatial decay. 

Perlu dicatat bahwa setelah pengecualian kejahatan non-spasial (lihat 4.1), jumlah sel yang memuat kejadian turun menjadi 19.873 dengan median 13 kejadian per sel dan 21,6 persen sel berdata tipis. 

### **3.5 Tren bulanan tidak dapat diinterpretasikan** 

Distribusi kejahatan per bulan menunjukkan tren menurun dari awal ke akhir tahun. Namun, pola ini merupakan artefak subset: data 2026 hanya mencakup Januari hingga April, sehingga bulan-bulan awal memperoleh kontribusi dari tiga tahun sementara bulan akhir hanya dari dua tahun. Tren tersebut tidak dapat diinterpretasikan sebagai pola musiman tanpa normalisasi terhadap jumlah tahun yang berkontribusi. Konsekuensinya, <mark>`month`</mark> tidak digunakan sebagai fitur. 

##### 

### **4.1 Severity Scoring** 

Dataset tidak menyediakan kolom Risk Score, sehingga tidak terdapat ground truth. Label harus dibentuk sendiri melalui pseudo-labeling, dan langkah pertamanya adalah memberi setiap kejadian sebuah severity score. 

#### **Struktur data yang mendasari keputusan** 

Setelah pengecualian kejahatan non-spasial, terdapat 28 Primary Type dan 270 kombinasi unik Primary Type dan Description dalam subset. Distribusinya sangat timpang: 5 jenis teratas mencakup 79,5 persen seluruh kejadian, 10 teratas mencakup 95,8 persen, dan 15 teratas mencakup 98,8 persen. Konsekuensinya, justifikasi mendalam 

difokuskan pada jenis-jenis dominan. Kesalahan penilaian pada jenis berfrekuensi sangat rendah (misalnya GAMBLING, 0,01 persen data) berdampak marjinal terhadap Risk Score akhir, sementara kesalahan pada THEFT (26,65 persen) berdampak besar. 

Pemeriksaan variasi Description dalam setiap Primary Type menunjukkan bahwa <mark>`Primary Type`</mark> saja tidak memadai: 

- BATTERY (20,62 persen data): 79,5 persen berupa kekerasan tanpa senjata ( <mark>`SIMPLE`</mark> dan <mark>`DOMESTIC BATTERY SIMPLE` )</mark> , namun terdapat pula varian bersenjata seperti <mark>`AGGRAVATED`</mark> `-` <mark>`HANDGUN`</mark> (3,0 persen) dan <mark>`AGGRAVATED`</mark> `-` <mark>`KNIFE`</mark> (1,8 persen). 

- ROBBERY (3,34 persen data): 37,9 persen adalah <mark>`ARMED`</mark> `-` <mark>`HANDGUN` ,</mark> sementara 28,6 persen adalah <mark>`STRONG ARM`</mark> `-` <mark>`NO WEAPON` .</mark> 

- BURGLARY (4,46 persen data): mayoritas berupa <mark>`FORCIBLE ENTRY`</mark> (38,9 persen) dan <mark>`BURGLARY FROM MOTOR VEHICLE`</mark> (29,3 persen), namun terdapat <mark>`HOME INVASION`</mark> (2,5 persen), yaitu masuk paksa ke rumah saat penghuni berada di dalam. 

Menyamakan pemukulan tangan kosong dengan penganiayaan bersenjata api jelas tidak dapat dibenarkan. Severity scoring karenanya harus mempertimbangkan kombinasi Primary Type dan Description. 

#### **Rubrik dampak** 

Empat dimensi menentukan seberapa berbahaya suatu kejahatan bagi orang yang berada di lokasi tersebut: 

|**Dimensi**|**Bobot**|**Alasan**|
|---|---|---|
|Ancaman<br>nyawa|40|Kejahatan berpotensi fatal menimbulkan<br>dampak yang tidak dapat dipulihkan|
|Kekerasan fsik|25|Cedera fsik menimbulkan trauma dan<br>kerugian yang melampaui kerugian materi|
|Korban hadir<br>dan terancam|20|Membedakan kejahatan yang mengancam<br>orang secara langsung dari kejahatan<br>properti tanpa korban di tempat|



Senjata terlibat 

15 Kehadiran senjata meningkatkan potensi eskalasi meskipun belum digunakan 

#### **Titik jangkar hukum** 

dalam Unified Code of Corrections (730 ILCS 5/5-4.5): 

|**Klasifkasi**|**Rentang hukuman**|**Referensi**|
|---|---|---|
|First-degree<br>murder|20 hingga 60 tahun, atau<br>seumur hidup|730 ILCS<br>5/5-4.5-20|
|Class X|6 hingga 30 tahun, tanpa opsi<br>probation|730 ILCS<br>5/5-4.5-25|
|Class 1|4 hingga 15 tahun|730 ILCS<br>5/5-4.5-30|
|Class 2|3 hingga 7 tahun|730 ILCS<br>5/5-4.5-35|
|Class 3|2 hingga 5 tahun|730 ILCS<br>5/5-4.5-40|
|Class 4|1 hingga 3 tahun|730 ILCS<br>5/5-4.5-45|



HOMICIDE diberi skor 100 karena berada dalam kelas tersendiri dengan hukuman tertinggi. Kejahatan Class X (aggravated criminal sexual assault, armed robbery, home invasion, aggravated vehicular hijacking, aggravated battery with a firearm, aggravated kidnapping) ditempatkan pada rentang 85 hingga 95, mencerminkan statusnya sebagai kejahatan non-homicide terberat yang tidak memungkinkan probation. 

#### **Pendekatan berbasis aturan** 

severity dibangun melalui skor dasar per Primary Type yang disesuaikan dengan modifier berbasis kata kunci pada Description. Modifier utama: 

|**Kata kunci**|**Modifer**|**Alasan**|
|---|---|---|
|HANDGUN, FIREARM|+25|Aggravated battery with<br>frearm merupakan Class X|
|KNIFE, CUTTING<br>INSTRUMENT|+18|Senjata tajam, ancaman<br>nyawa lebih rendah dari<br>senjata api|
|DANGEROUS WEAPON|+15|Senjata lain|
|AGGRAVATED|+12|Menaikkan kelas felony<br>menurut hukum Illinois|
|HOME INVASION|+40|Class X (720 ILCS 5/19-6),<br>intrusi saat penghuni<br>berada di dalam|
|HIJACKING|+15|Aggravated vehicular<br>hijacking merupakan Class<br>X (720 ILCS 5/18-4)|
|SERIOUS INJURY|+10|Cedera serius eksplisit|
|POCKET-PICKING,<br>PURSE-SNATCHING,<br>FROM PERSON|+15|Pencurian dengan kontak<br>langsung terhadap korban<br>yang hadir|
|RECKLESS (hanya untuk<br>HOMICIDE)|-30|Reckless homicide<br>merupakan Class 3, bukan<br>frst-degree murder|
|ATTEMPT|-12|Kejahatan tidak terlaksana|
|FROM MOTOR VEHICLE,<br>TO VEHICLE|-5|Kejahatan properti tanpa<br>korban di tempat|



kepemilikan senjata ilegal merupakan esensi dari kejahatan tersebut, bukan faktor pemberat terhadap tindak kekerasan (lihat bagian Refleksi). 

Keunggulan pendekatan ini adalah kemampuannya mencakup seluruh 270 kombinasi tanpa hardcode, sekaligus menjamin konsistensi: dua kejahatan dengan profil dampak yang sama akan memperoleh skor 

yang sama. 

#### **Keputusan mengenai kekerasan domestik** 

Kekerasan domestik diberi severity yang setara dengan kekerasan nondomestik. Dari perspektif keparahan kejahatan, penganiayaan tetap merupakan penganiayaan terlepas dari relasi antara pelaku dan korban. Membedakannya berisiko meremehkan kejahatan yang justru sering berulang dan tereskalasi. 

#### **Keputusan mengecualikan kejahatan non-spasial** 

DECEPTIVE PRACTICE dan OTHER OFFENSE dikecualikan dari perhitungan. Secara gabungan dengan NON-CRIMINAL, sebanyak 71.890 baris dibuang, yaitu 13,05 persen dari data yang telah dibersihkan. Sisa data berjumlah 479.006 baris. 

tertentu. Definisi ini mengandaikan bahwa kejahatan yang tercatat pada suatu koordinat merepresentasikan bahaya yang melekat pada lokasi tersebut. Asumsi ini tidak berlaku untuk kedua kategori tersebut. 

OTHER OFFENSE didominasi oleh <mark>`TELEPHONE THREAT`</mark> (19,4 persen), <mark>`HARASSMENT BY TELEPHONE`</mark> (15,6 persen), dan <mark>`HARASSMENT BY ELECTRONIC MEANS`</mark> (14,9 persen). DECEPTIVE PRACTICE didominasi oleh <mark>`FINANCIAL IDENTITY THEFT`</mark> dan <mark>`CREDIT CARD FRAUD` .</mark> Pada kejahatan-kejahatan ini, koordinat yang tercatat merupakan alamat administratif korban, bukan tempat di mana bahaya fisik terjadi. Seseorang yang kartu kreditnya dibobol dari jarak jauh tidak menjadikan tempat tinggalnya berbahaya bagi orang yang melintas. 

Memasukkannya akan mencemari sinyal spasial, karena area berpenduduk padat akan tampak berisiko semata karena banyak penghuninya menjadi korban penipuan daring. Perlu ditegaskan bahwa keputusan ini bukan penilaian moral mengenai keseriusan kejahatan tersebut, melainkan pertimbangan validitas pengukuran: lokasi yang tercatat tidak informatif bagi tujuan sistem. 

Karena mencakup 13,05 persen data, perlakuan terhadap kedua 

kategori ini berdampak nyata terhadap hasil akhir. 

#### **Transformasi non-linear** 

Skor severity dasar disusun secara linear pada rentang 0 hingga 100. Namun, penggunaan skor linear dalam agregasi mengandung implikasi yang bermasalah. Dengan HOMICIDE bernilai 100 dan THEFT bernilai 25, penjumlahan severity menyiratkan bahwa empat pencurian setara dengan satu pembunuhan. Sebuah blok yang mengalami satu pembunuhan tidak dapat dianggap setara berbahayanya dengan blok yang mengalami empat pencurian barang bernilai rendah. 

Karena itu, severity ditransformasi secara kuadratik sebelum diagregasi: 

```
severity_weighted = (severity / 100)^2 * 100
```

Transformasi ini mempertahankan skala 0 hingga 100 yang intuitif sekaligus memperlebar jarak antar tingkat keparahan secara eksponensial: 

|**Kejahatan**|**Severity**<br>**dasar**|**Setelah**<br>**transformasi**|**Rasio terhadap**<br>**HOMICIDE**|
|---|---|---|---|
|HOMICIDE|100|100,0|1x|
|ROBBERY -<br>ARMED<br>HANDGUN|95|90,2|1,1x|
|BATTERY -<br>SIMPLE|50|25,0|4x|
|THEFT - $500<br>AND UNDER|25|6,2|16x|
|CRIMINAL<br>DAMAGE - TO<br>VEHICLE|10|1,0|100x|



Setelah transformasi, dibutuhkan 16 kejadian THEFT untuk menyetarai satu HOMICIDE. Kejahatan berat mendominasi Risk Score secara 

proporsional dengan tingkat ancamannya dan tidak dapat dikompensasi oleh akumulasi kejahatan ringan. 

Eksponen 2 dipilih sebagai titik tengah. Eksponen 3 menghasilkan rasio 64 banding 1 antara HOMICIDE dan THEFT, yang dinilai terlalu ekstrem karena kejahatan properti nyaris tidak akan berkontribusi terhadap skor akhir. 

#### **Hasil** 

Hierarki severity akhir per Primary Type (rata-rata): HOMICIDE (99,9), CRIMINAL SEXUAL ASSAULT (99,2), HUMAN TRAFFICKING (90,0), KIDNAPPING (86,0), ROBBERY (84,4), SEX OFFENSE (60,7), OFFENSE INVOLVING CHILDREN (56,9), ASSAULT (55,9), BATTERY (54,9), ARSON (54,1), WEAPONS VIOLATION (45,0), hingga GAMBLING (5,0). 

Gradasi dalam Primary Type juga berfungsi. Pada BURGLARY: HOME INVASION (75), FORCIBLE ENTRY (35), BURGLARY FROM MOTOR VEHICLE (30). Pada THEFT: POCKET-PICKING (40), $500 AND UNDER (25). Pada HOMICIDE: FIRST DEGREE MURDER (100), RECKLESS HOMICIDE (70). 

### **4.2 Pemodelan Relevansi Waktu (Temporal Decay)** 

Sebuah kejahatan tidak selamanya relevan secara seragam. Kejadian yang berlangsung kemarin lebih mencerminkan kondisi risiko saat ini dibanding kejadian tiga tahun lalu. 

#### **Bentuk fungsi** 

Peluruhan dimodelkan secara eksponensial: 

Fungsi eksponensial dipilih atas dasar berikut: 

1. Relevansi suatu kejadian menurun secara gradual, bukan mendadak. Sebuah kejahatan tidak kehilangan seluruh relevansinya pada tanggal tertentu, sehingga fungsi step atau linear yang memaksa bobot menjadi nol pada titik tegas kurang sesuai. Hal ini terbukti dalam implementasi: kejadian tertua dalam subset (831 hari) masih memiliki bobot 0,041, sehingga tetap berkontribusi meski secara marjinal. 

2. Fungsi eksponensial dapat diparameterisasi melalui half-life, yaitu durasi hingga bobot suatu kejadian turun menjadi setengahnya. Parameter ini intuitif dan dapat dijustifikasi secara langsung, berbeda dengan konstanta peluruhan yang maknanya kurang transparan. 

#### **Parameter half-life** 

Half-life ditetapkan pada 180 hari (6 bulan). Bobot yang dihasilkan: 

|**Usia kejadian**|**Bobot**|
|---|---|
|0 hari|1,000|
|30 hari|0,891|
|90 hari|0,707|
|180 hari|0,500|
|365 hari (1 tahun)|0,245|
|730 hari (2 tahun)|0,060|



kejahatan bergeser seiring perubahan kondisi sosial dan kebijakan sehingga data lama kurang merepresentasikan kondisi saat ini. Half-life yang terlalu panjang, misalnya 2 tahun dengan bobot 0,71 setelah setahun, akan bertentangan dengan argumen tersebut karena kejadian lama tetap dominan. 

Sebaliknya, half-life yang terlalu pendek, misalnya 3 bulan dengan bobot 0,06 setelah setahun, akan membuat seluruh data 2024 nyaris tidak berkontribusi. Hal ini berarti membuang sebagian besar data yang telah 

dipilih dalam subset, sekaligus memperparah masalah kepadatan pada sel-sel yang sudah tipis (21,6 persen sel memiliki kurang dari 5 kejadian). 

Half-life 180 hari menempatkan bobot kejadian setahun lalu pada 0,245, cukup rendah untuk memprioritaskan kejadian baru namun masih memungkinkan data historis berkontribusi secara bermakna terhadap stabilitas estimasi. Kejadian tertua dalam subset (831 hari) memperoleh bobot 0,041. 

### **4.3 Pemodelan Relevansi Lokasi** 

#### **Grid metrik, bukan pembulatan koordinat** 

Pendekatan baseline membentuk sel dengan membulatkan koordinat. Namun, hal ini menghasilkan sel yang tidak seragam: pada lintang Chicago (sekitar 41,8 derajat), 0,001 derajat latitude setara sekitar 111 meter sementara 0,001 derajat longitude hanya sekitar 83 meter. Sel yang terbentuk berupa persegi panjang (83m kali 111m), sehingga jarak antar sel bergantung pada arah. Tetangga horizontal lebih dekat daripada tetangga vertikal. 

Hal ini problematik untuk spatial decay: memberi bobot yang sama pada seluruh tetangga langsung secara implisit menyamakan pengaruh sejauh 83 meter dengan pengaruh sejauh 111 meter. 

Grid metrik mengatasi hal ini dengan memproyeksikan koordinat ke satuan meter terlebih dahulu, menghasilkan sel yang benar-benar persegi. Konsekuensinya, jarak antar sel menjadi bermakna secara fisik, dan parameter spatial decay dapat dinyatakan dalam satuan yang dapat dijustifikasi (misalnya "pengaruh menurun setengah setiap 150 meter") alih-alih "per langkah grid" yang panjangnya bervariasi menurut arah. 

#### **Ukuran sel 150 meter** 

Dokumentasi resmi CPD menyatakan lokasi dianonimkan ke block level, sehingga koordinat merepresentasikan blok, bukan titik presisi. Blok 

kota Chicago berkisar 100 hingga 150 meter. Resolusi lebih halus hanya menangkap noise anonimisasi, sementara resolusi lebih kasar menghilangkan variasi antar blok. 

#### Perbandingan empiris: 

|**Ukuran**<br>**sel**|**Jumlah**<br>**sel**|**Median kejadian**<br>**per sel**|**Sel sepi (kurang**<br>**dari 5)**|
|---|---|---|---|
|50m|60.309|4|57,4%|
|100m|36.814|6|39,0%|
|150m|19.873|13|21,6%|
|250m|8.148|35|10,4%|
|500m|2.341|138|5,9%|



Pada 50 meter, 57,4 persen sel memiliki kurang dari 5 kejadian dengan median hanya 4. Kepadatan ini terlalu tipis untuk menghasilkan skor yang bermakna, sekaligus berada di bawah resolusi anonimisasi data. 

Transisi dari 100m ke 150m menurunkan proporsi sel sepi dari 39,0 persen menjadi 21,6 persen, sekaligus meningkatkan median kejadian per sel dari 6 menjadi 13. Sebaliknya, transisi dari 150m ke 250m hanya menurunkan sel sepi sekitar 11 poin persentase namun memangkas jumlah sel hingga 59 persen (19.873 menjadi 8.148), sekaligus melampaui ukuran blok kota sehingga satu sel berpotensi mencampur area dengan karakter risiko yang berbeda. 

Konsekuensi dari pilihan ini adalah 21,6 persen sel tetap memiliki kurang dari 5 kejadian. Hal ini tidak diabaikan, melainkan menjadi justifikasi utama diterapkannya spatial decay. 

#### **Spatial decay: fungsi Gaussian** 

Hingga tahap agregasi, nilai setiap sel dihitung semata dari kejadian di dalam sel tersebut. Pendekatan ini mengandung dua kelemahan. 

langsung dengan hotspot akan memperoleh nilai rendah apabila 

kebetulan tidak ada kejadian tercatat di dalamnya, meskipun jaraknya hanya 150 meter dari area berbahaya. Risiko tidak berhenti secara mendadak pada garis yang digambar sendiri. 

Kedua, sel berdata tipis menjadi tidak stabil. Pada sel dengan kurang dari 5 kejadian, satu kejadian tambahan dapat mengubah skor secara drastis. 

Peluruhan spasial karenanya dimodelkan dengan fungsi Gaussian: 

```
w(d) = exp(-d^2 / (2 * sigma^2))
```

dengan d adalah jarak antar pusat sel dalam meter. 

Fungsi Gaussian dipilih atas dasar berikut: 

1. Bobot menurun secara halus dan simetris terhadap jarak, sesuai dengan sifat penyebaran risiko yang tidak memiliki batas tegas. 

- ini penting karena sel diagonal berjarak 212 meter (150 dikali akar 2), bukan 150 meter seperti sel ortogonal. Pendekatan yang memberikan bobot seragam pada seluruh tetangga dalam jendela 3x3, sebagaimana lazim dilakukan, secara implisit menyamakan kedua jarak tersebut. 

3. Kejadian di dalam sel itu sendiri memperoleh bobot penuh (1,0), sementara kontribusi tetangga menurun sesuai jaraknya. Pendekatan rata-rata seragam sebaliknya memperlakukan sel itu sendiri setara dengan tetangganya. 

#### **Parameter sigma** 

Sigma ditetapkan pada 150 meter, setara dengan satu ukuran sel. Bobot yang dihasilkan: 

|**Posisi**|**Jarak**|**Bobot**|
|---|---|---|
|Sel itu sendiri|0 m|1,000|



|Tetangga ortogonal|150 m|0,607|
|---|---|---|
|Tetangga diagonal|212 m|0,368|
|Dua sel ortogonal|300 m|0,135|
|Dua sel diagonal|335 m|0,082|



Sigma sebesar satu ukuran sel memastikan bahwa pengaruh tetangga langsung tetap substansial namun tidak mendominasi kontribusi sel itu sendiri. Pada jarak dua sel, bobot telah turun di bawah 0,15, sehingga radius pencarian dibatasi hingga dua sel: kontribusi di luar itu bernilai kurang dari 0,03 dan tidak sepadan dengan beban komputasinya. 

### **4.4 Representasi Fitur** 

##### 

Waktu bersifat siklikal. Jam 23 berdekatan dengan jam 00, namun apabila direpresentasikan sebagai angka linear, model akan menganggap keduanya berjauhan dengan selisih 23. 

Setiap nilai siklikal karenanya dipetakan ke lingkaran melalui pasangan sinus dan kosinus: 

```
x_sin = sin(2 * pi * v / P)
x_cos = cos(2 * pi * v / P)
```

dengan P adalah panjang siklus (24 untuk jam, 7 untuk hari). 

Bukti empiris bahwa encoding ini benar: jarak Euclidean antara jam 23:00 dan 00:00 pada bidang (sin, cos) adalah 0,261, sedangkan jarak antara 00:00 dan 12:00 adalah 2,000. Dengan angka linear mentah, jarak 23 ke 0 akan tercatat sebagai 23, yaitu jarak terjauh yang mungkin, terbalik dari realitanya. 

Dua nilai (sinus dan kosinus) diperlukan karena satu nilai saja menimbulkan ambiguitas: dua jam yang berbeda dapat memiliki nilai 

sinus yang sama. 

#### **Fitur yang di-encode** 

<mark>`hour`</mark> di-encode secara siklikal dengan periode 24. Fitur ini memiliki daya beda terkuat, dengan variasi hingga tiga kali lipat antara jam tersepi dan tersibuk. 

<mark>`dow`</mark> di-encode secara siklikal dengan periode 7, meskipun distribusi univariatnya nyaris seragam (selisih hanya 6 persen). Keputusan ini didasarkan pada temuan bahwa pengaruh hari bersifat interaktif terhadap jam: akhir pekan menaikkan aktivitas dini hari sekaligus menurunkan puncak sore, dua efek yang saling meniadakan pada agregasi harian. Tanpa <mark>`dow` ,</mark> model tidak dapat membedakan "jam 2 pagi hari Sabtu" dari "jam 2 pagi hari Selasa". 

<mark>`month`</mark> tidak di-encode. Tren bulanan yang teramati tercemar artefak subset, sehingga tidak terdapat bukti empiris dari data yang mendukung relevansinya. Dengan rentang subset hanya tiga tahun, sinyal musiman juga cenderung lemah. 

<mark>`is_weekend`</mark> redundan dengan <mark>`dow_sin`</mark> dan <mark>`dow_cos` .</mark> Alasannya, representasi siklikal memperlakukan transisi Jumat ke Sabtu identik dengan Selasa ke Rabu, yaitu jarak yang sama pada lingkaran. Padahal temuan EDA menunjukkan batas akhir pekan merupakan loncatan kategorikal: pola dini hari Sabtu berbeda tajam dari Jumat, sementara Selasa dan Rabu nyaris identik. Fitur biner ini secara eksplisit menandai batas tersebut, informasi yang tidak tertangkap dengan baik oleh representasi siklikal. 

Granularitas temporal dibatasi pada level jam. Resolusi yang lebih halus (misalnya menit atau bucket 15 menit) tidak akan bermakna, karena temuan EDA menunjukkan waktu sering dibulatkan dan dokumentasi resmi mengonfirmasi bahwa waktu merupakan "best estimate". 

#### **Fitur agregat spasial** 

<mark>`n_crimes` :</mark> jumlah kejadian pada sel. Mengukur frekuensi, berbeda dari <mark>`base_value`</mark> yang menimbang keparahan dan kebaruan. Keduanya membawa informasi yang tidak redundan: sebuah sel dapat memiliki banyak kejadian ringan atau sedikit kejadian berat. 

<mark>`mean_severity` :</mark> rata-rata keparahan kejahatan pada sel. Mencirikan karakter sel, bukan volumenya. Sel dengan 5 perampokan bersenjata memiliki karakter risiko yang berbeda dari sel dengan 50 pencurian barang kecil, meskipun keduanya dapat memiliki nilai agregat yang serupa. 

<mark>`max_severity` :</mark> keparahan tertinggi yang pernah tercatat pada sel. Menangkap keberadaan kejadian ekstrem yang mungkin tersamar dalam rata-rata. Sebuah sel yang pernah mengalami satu pembunuhan di antara puluhan kejahatan ringan memiliki profil risiko yang berbeda dari sel yang tidak pernah mengalaminya. 

<mark>`pct_violent` :</mark> proporsi kejahatan kekerasan terhadap total kejadian pada sel. Didukung langsung oleh temuan EDA yang menunjukkan bahwa komposisi jenis kejahatan bervariasi secara sistematis. 

<mark>`n_distinct_types` :</mark> keragaman jenis kejahatan pada sel. Sel dengan banyak jenis berbeda kemungkinan merupakan area publik atau komersial dengan paparan risiko yang luas. Sebaliknya, sel yang didominasi satu jenis kejahatan mungkin memiliki karakter risiko yang spesifik, misalnya area parkir yang hanya rawan pembongkaran kendaraan. 

<mark>`density_ratio` :</mark> kepadatan kejadian relatif terhadap rata-rata kota. Membuat perbandingan antar sel lebih dapat diinterpretasi dibanding nilai absolut yang bergantung pada ukuran subset. 

<mark>`crime_count` :</mark> jumlah kejadian pada unit analisis (sel, hari, jam), berbeda dari <mark>`n_crimes`</mark> yang dihitung pada level sel secara keseluruhan. 

### **4.5 Normalisasi** 

Nilai <mark>`risk_raw`</mark> tidak memiliki makna intrinsik: rentangnya bergantung 

pada jumlah kejadian, skala severity, dan parameter decay. Nilai tersebut perlu dipetakan ke skala 0 hingga 100. 

#### **Mengapa min-max scaling tidak dipilih** 

Distribusi <mark>`risk_raw`</mark> sangat timpang. Ketimpangan ini merupakan akumulasi dari tiga komponen yang masing-masing sudah timpang: severity setelah transformasi kuadratik, bobot temporal, dan konsentrasi spasial. 

Penerapan min-max scaling secara langsung menghasilkan hasil yang tidak dapat digunakan. Rentang <mark>`risk_raw`</mark> adalah 0,03 hingga 324,23. Dengan nilai maksimum tersebut, sebuah unit dengan <mark>`risk_raw`</mark> sebesar 10 akan memperoleh skor 3,1. Karena mayoritas unit berada pada rentang nilai rendah, lebih dari 160.000 unit akan terkonsentrasi pada skor di bawah 5, sementara rentang 20 hingga 100 nyaris kosong. Skala 0 hingga 100 yang seharusnya informatif akan kolaps ke rentang 0 hingga 20, dan seluruh variasi bermakna antar unit pada rentang bawah menjadi tidak terbaca. 

#### **Pendekatan yang dipilih: transformasi logaritmik** 

Sebelum diskalakan, <mark>`risk_raw`</mark> ditransformasi secara logaritmik: 

```
risk_log = ln(1 + risk_raw)
```

Transformasi ini mengompresi ekor kanan distribusi tanpa mengubah urutan, sekaligus melebarkan rentang bawah sehingga variasi antar unit dengan nilai rendah tetap terbaca. Penggunaan ln(1+x) alih-alih ln(x) memastikan nilai nol tetap terdefinisi. 

Hasilnya kemudian diskalakan ke rentang 0 hingga 100. 

Setelah transformasi, distribusi yang semula menurun secara eksponensial berubah menyerupai distribusi normal dan menyebar wajar sepanjang rentang 0 hingga 100. Statistik Risk Score akhir: rata-rata 36,8, median 36,9, dan simpangan baku 16,9. Persentil ke-25 berada pada 24,3 dan persentil ke-75 pada 49,1, sementara persentil ke-99 mencapai 73,8. Unit dengan skor tinggi kini benar-benar 

merepresentasikan hotspot yang menonjol, bukan sekadar konsekuensi dari skala yang tidak sesuai. Distribusi yang menyerupai normal ini juga menguntungkan untuk tahap pemodelan, karena target yang terdistribusi wajar umumnya lebih mudah dipelajari. 

#### **Alternatif yang dipertimbangkan** 

Pendekatan berbasis persentil turut dipertimbangkan namun tidak dipilih. Persentil menghasilkan distribusi yang seragam, di mana setiap rentang skor dihuni oleh jumlah unit yang sama. Hal ini menghilangkan informasi mengenai magnitudo: dua unit dengan skor 90 dan 95 dapat memiliki selisih risiko absolut yang sangat besar atau sangat kecil, namun persentil tidak membedakan keduanya. 

Untuk Risk Score, magnitudo merupakan informasi yang bermakna. Sebuah lokasi dengan skor 90 sebaiknya benar-benar mencerminkan tingkat risiko yang jauh lebih tinggi dibanding lokasi berskor 45, bukan sekadar menempati peringkat yang lebih tinggi. 

## **5. Dataset Akhir** 

Dataset akhir berisi 376.630 baris pada level unit analisis (sel, hari, jam), dengan 19 fitur dan 1 label. 

<mark>`cell_id` ,</mark> <mark>`gx` ,</mark> <mark>`gy` ,</mark> <mark>`lat_r` ,</mark> <mark>`lon_r` ,</mark> <mark>`dow` ,</mark> <mark>`hour`</mark> 

Fitur temporal: <mark>`hour_sin` ,</mark> <mark>`hour_cos` ,</mark> <mark>`dow_sin` ,</mark> <mark>`dow_cos` ,</mark> <mark>`is_weekend`</mark> 

Fitur spasial per sel: <mark>`n_crimes` ,</mark> <mark>`mean_severity` ,</mark> <mark>`max_severity` ,</mark> <mark>`pct_violent` ,</mark> <mark>`n_distinct_types` ,</mark> <mark>`density_ratio`</mark> 

Fitur per unit: <mark>`crime_count`</mark> 

Label: <mark>`risk_score`</mark> 

Distribusi Risk Score akhir: 

|**Statistik**|**Nilai**|
|---|---|
|Rata-rata|36,8|
|Median|36,9|
|Simpangan baku|16,9|
|Persentil 10|13,7|
|Persentil 25|24,3|
|Persentil 75|49,1|
|Persentil 90|59,0|
|Persentil 99|73,8|
|Maksimum|100,0|



Kolom antara ( <mark>`base_value` ,</mark> <mark>`risk_raw` ,</mark> <mark>`risk_log` ,</mark> <mark>`severity` ,</mark> <mark>`weighted_severity` )</mark> sengaja tidak disertakan dalam dataset akhir. Ketiganya merupakan langkah perhitungan menuju label, bukan fitur yang dapat digunakan model. Menyertakannya akan menyebabkan kebocoran target, karena <mark>`risk_score`</mark> merupakan transformasi deterministik dari nilai-nilai tersebut: model dapat memprediksi label dengan akurasi sempurna hanya dengan menerapkan transformasi logaritmik dan penskalaan, tanpa mempelajari relasi apa pun antara fitur dan risiko. 

Output disimpan dalam dua format: <mark>`features_labels.csv`</mark> (78,85 MB) dan <mark>`features_labels.parquet`</mark> (4,97 MB). 

##### 

### **kategori** 

Severity table dibangun dengan pendekatan berbasis aturan, yaitu skor dasar per Primary Type yang disesuaikan dengan modifier berbasis kata 

kunci pada Description. Pendekatan ini dipilih karena mampu mencakup 353 kombinasi Type dan Description tanpa perlu mendefinisikan setiap baris secara manual. 

Namun, pendekatan ini memiliki risiko yang tidak langsung terlihat: kata kunci yang sama dapat muncul pada Primary Type yang tidak 

RECKLESS HOMICIDE (Class 3 felony) dari FIRST DEGREE MURDER. Namun modifier ini juga terkena pada RECKLESS CONDUCT dalam kategori PUBLIC PEACE VIOLATION, menurunkan skornya dari 12 menjadi nilai negatif yang kemudian ter-clip ke batas bawah. 

menaikkan WEAPONS VIOLATION hingga skor rata-rata 69,5, menempatkannya di atas ASSAULT dan SEX OFFENSE. Hal ini keliru secara konseptual karena kepemilikan senjata ilegal merupakan esensi dari kejahatan tersebut, bukan faktor pemberat terhadap tindak kekerasan. 

tertentu. Modifier RECKLESS hanya berlaku untuk HOMICIDE, dan modifier senjata dikecualikan untuk WEAPONS VIOLATION. Setelah perbaikan, WEAPONS VIOLATION turun ke skor rata-rata 38,5 (peringkat 13 dari 28), dan tidak ada lagi skor yang menyentuh batas bawah. 

konsistensi, namun memerlukan verifikasi eksplisit terhadap hasilnya. Tanpa pemeriksaan skor per kategori, kedua bug di atas tidak akan terdeteksi karena kode tetap berjalan tanpa error. 

### **Kendala: analisis univariat dapat menyesatkan** 

Pada tahap EDA, distribusi kejahatan antar hari tampak nyaris seragam (selisih hanya 6 persen). Kesimpulan awal yang hampir diambil adalah bahwa <mark>`dow`</mark> tidak informatif dan tidak perlu di-encode. 

Kesimpulan tersebut keliru. Analisis bivariat melalui heatmap jam kali hari mengungkap bahwa pengaruh hari bersifat interaktif terhadap jam, dan kedua efeknya saling meniadakan pada agregasi harian. 

Pelajaran. Fitur yang tampak lemah secara marginal dapat membawa informasi signifikan melalui interaksinya dengan fitur lain. Penilaian relevansi fitur tidak dapat didasarkan pada analisis univariat semata. 

### **Kendala: interpretasi pola yang ternyata merupakan artefak** 

Dua pola menonjol pada EDA awal ternyata bukan merupakan fenomena kriminal, melainkan artefak. 

Lonjakan kejadian pada jam 00 pada awalnya tampak sebagai pola waktu. Verifikasi terhadap distribusi menit mengungkap bahwa ini merupakan konsekuensi pembulatan dan penggunaan nilai default, yang kemudian dikonfirmasi oleh dokumentasi resmi. 

Tren menurun pada distribusi bulanan pada awalnya tampak sebagai pola musiman. Pemeriksaan lebih lanjut mengungkap bahwa ini merupakan konsekuensi subset yang tidak seimbang, di mana data 2026 hanya mencakup empat bulan. 

Pelajaran. Pola yang terlihat dalam data belum tentu mencerminkan fenomena yang sedang dipelajari. Memeriksa dokumentasi sumber data dan memahami cara data dikumpulkan sama pentingnya dengan menganalisis datanya itu sendiri. 

## **7. Limitasi** 

1. Data merupakan kejahatan yang dilaporkan, bukan kejahatan yang terjadi. Dokumentasi resmi menyatakan dataset ini mencatat "reported incidents of crime". Area dengan intensitas patroli lebih tinggi atau warga yang lebih sering melapor akan tampak lebih berisiko, meskipun tingkat kejahatan sebenarnya mungkin serupa 

dengan area lain. Risk Score yang dihasilkan karenanya merefleksikan pola pelaporan dan pencatatan, bukan semata kejahatan aktual. 

2. bahwa "preliminary crime classifications may be changed at a later date based upon additional investigation". Karena severity scoring didasarkan pada <mark>`Primary Type`</mark> dan <mark>`Description` ,</mark> ketidakpastian ini terwariskan ke label yang dihasilkan. 

3. Waktu kejadian sering merupakan estimasi. Fitur <mark>`hour`</mark> mengandung noise pembulatan bawaan. Granularitas di bawah level jam tidak dimungkinkan. 

4. Dataset akhir hanya mencakup kombinasi yang pernah mengalami kejahatan. Tabel unit dibentuk melalui agregasi dari data kejadian, sehingga kombinasi (sel, hari, jam) tanpa kejadian tidak memperoleh baris. Kombinasi yang secara konseptual memiliki risiko namun tidak pernah tercatat kejahatannya tidak muncul dalam dataset. Dataset akhir berjumlah 376.630 baris. Membangun grid lengkap (19.873 sel kali 7 hari kali 24 jam, atau sekitar 3,3 juta baris) akan mengatasi hal ini, namun dengan beban komputasi hampir sembilan kali lipat yang tidak sepadan pada tahap ini. Model pada Hands-On 2 diharapkan dapat melakukan generalisasi ke kombinasi yang tidak teramati melalui fitur lokasi dan waktu yang telah dibangun. 

5. Severity scoring merupakan pilihan desain, bukan kebenaran objektif. Tidak terdapat ground truth mengenai keparahan relatif antar jenis kejahatan. Skor yang digunakan dijangkarkan pada klasifikasi hukum Illinois dan rubrik dampak yang eksplisit, namun bobot dimensi dalam rubrik tersebut tetap merupakan keputusan subjektif. Yang dapat dijamin adalah konsistensi (dua kejahatan dengan profil dampak sama memperoleh skor sama) dan transparansi (setiap skor dapat ditelusuri ke prinsip yang dinyatakan), bukan objektivitas absolut. 

6. Faktor sosioekonomi tidak dimasukkan. Penambahan indikator sosioekonomi sebagai fitur risiko sempat dipertimbangkan, namun tidak dilakukan karena berisiko memperkuat bias penegakan 

hukum historis: kepadatan kejahatan yang tercatat berkorelasi dengan intensitas patroli, bukan semata dengan tingkat kejahatan yang mendasarinya. Menggabungkan data sosioekonomi dengan target yang diturunkan dari catatan kejahatan berpotensi menciptakan umpan balik yang secara tidak proporsional menandai area kurang mampu sebagai berisiko tinggi. Hal ini memerlukan audit keadilan yang berada di luar cakupan pekerjaan ini 

