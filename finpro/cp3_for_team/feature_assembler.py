"""Definisi FeatureAssembler untuk keperluan serving.

PENTING - kenapa file ini ada:
    Checkpoint di `models/model_v*.joblib` adalah BUNDLE {model, assembler, ...}.
    Objek `assembler` di dalamnya di-pickle dari notebook Checkpoint 2, sehingga
    pickle mencatat lokasi kelasnya sebagai `__main__.FeatureAssembler`.

    Pickle TIDAK menyimpan kode kelas, hanya referensi nama + isi __dict__ instance.
    Artinya saat bundle dimuat di proses lain (mis. uvicorn), Python harus bisa
    menemukan kelas bernama `FeatureAssembler` di modul `__main__`, kalau tidak
    akan gagal dengan:
        AttributeError: Can't get attribute 'FeatureAssembler' on <module '__main__'>

    Karena itu kelas di bawah disalin PERSIS dari notebook, lalu `predict.py`
    mendaftarkannya ke `sys.modules["__main__"]` sebelum memanggil joblib.load().

Konsekuensi: JANGAN ubah nama kelas, nama atribut, atau signature __init__ di sini
tanpa melatih ulang dan menyimpan ulang checkpoint dari notebook.
"""

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold

TARGET_COL = "risk_score"

BASE_COLS = ["lat_r", "lon_r", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "crime_count"]
VOLUME_HO1 = ["n_crimes", "density_ratio"]
VOLUME_REFIT = ["n_crimes_fit", "density_ratio_fit"]
SEVERITY_AGG = ["mean_severity", "max_severity", "pct_violent", "n_distinct_types"]


class FeatureAssembler:
    """Perakit feature vector yang aman dari kebocoran.

    Kontrak:
      * `fit`/`fit_transform` HANYA boleh diberi data training.
      * `transform` tidak menghitung statistik apa pun , hanya memetakan hasil fit,
        sehingga representasi saat training == saat serving.
      * objek ini disimpan bersama model di setiap checkpoint.

    Parameter penting
      agg_source : "refit"  -> agregat per-sel dihitung ulang dari fit-set (leak-safe)
                   "ho1_raw"-> pakai kolom HO1 apa adanya (BOCOR; hanya untuk pembanding)
      use_te     : target encoding cell_id, out-of-fold pada fit-set (leak-safe)
    """

    def __init__(self, use_is_weekend=True, use_crime_count=True,
                 use_volume_agg=True, use_severity_agg=True,
                 agg_source="refit", use_te=False,
                 te_m=20, te_folds=5, random_state=42):
        assert agg_source in ("refit", "ho1_raw")
        self.use_is_weekend = use_is_weekend
        self.use_crime_count = use_crime_count
        self.use_volume_agg = use_volume_agg
        self.use_severity_agg = use_severity_agg
        self.agg_source = agg_source
        self.use_te = use_te
        self.te_m, self.te_folds, self.random_state = te_m, te_folds, random_state

    # ---------- daftar kolom ----------
    def _row_cols(self):
        cols = [c for c in BASE_COLS if c != "crime_count" or self.use_crime_count]
        return cols + (["is_weekend"] if self.use_is_weekend else [])

    def _volume_cols(self):
        if not self.use_volume_agg:
            return []
        return VOLUME_HO1 if self.agg_source == "ho1_raw" else VOLUME_REFIT

    def _cell_cols(self):
        return self._volume_cols() + (SEVERITY_AGG if self.use_severity_agg else [])

    def fit(self, data):
        """Semua statistik lintas-baris lahir di sini , dan hanya dari `data`."""
        self.global_target_ = float(data[TARGET_COL].mean())
        g = data.groupby("cell_id")
        n_crimes_fit = g["crime_count"].sum()
        stats = pd.DataFrame(index=n_crimes_fit.index)
        stats["n_crimes_fit"] = n_crimes_fit
        stats["density_ratio_fit"] = n_crimes_fit / n_crimes_fit.mean()
        stats["rows_per_cell"] = g.size()
        for c in SEVERITY_AGG:
            stats[c] = g[c].mean()

        self.cell_stats_ = stats
        self.fallback_ = stats.median()
        self.n_fit_rows_, self.n_fit_cells_ = len(data), len(stats)
        if self.use_te:
            agg = g[TARGET_COL].agg(["mean", "count"])
            self.te_map_ = ((agg["mean"] * agg["count"] + self.global_target_ * self.te_m)
                            / (agg["count"] + self.te_m))

        self.feature_names_ = self._row_cols() + self._cell_cols() + (["te_cell"] if self.use_te else [])
        return self

    def _assemble(self, data):
        X = data[self._row_cols()].copy()
        want = self._cell_cols()
        if want:
            if self.agg_source == "ho1_raw":
                X[want] = data[want].values
            else:
                m = self.cell_stats_.reindex(data["cell_id"].values)[want]
                m.index = X.index
                X[want] = m.fillna(self.fallback_[want])
        return X

    def transform(self, data):
        self._check_fitted()
        X = self._assemble(data)
        if self.use_te:
            X["te_cell"] = data["cell_id"].map(self.te_map_).fillna(self.global_target_).values
        return X[self.feature_names_]

    def fit_transform(self, data):
        """Untuk data fit, te_cell dihitung OUT-OF-FOLD agar baris tidak melihat labelnya sendiri."""
        self.fit(data)
        X = self._assemble(data)
        if self.use_te:
            X["te_cell"] = self._oof_te(data)
        return X[self.feature_names_]

    def _oof_te(self, data):
        y, cells = data[TARGET_COL].values, data["cell_id"].values
        oof = np.empty(len(data), dtype=float)
        kf = KFold(n_splits=self.te_folds, shuffle=True, random_state=self.random_state)
        for tr, va in kf.split(data):
            gm = y[tr].mean()
            s = pd.DataFrame({"c": cells[tr], "y": y[tr]}).groupby("c")["y"].agg(["mean", "count"])
            m = (s["mean"] * s["count"] + gm * self.te_m) / (s["count"] + self.te_m)
            oof[va] = pd.Series(cells[va]).map(m).fillna(gm).values
        return oof

    def _check_fitted(self):
        if not hasattr(self, "feature_names_"):
            raise RuntimeError("FeatureAssembler belum di-fit.")

    def config(self):
        return {"use_is_weekend": self.use_is_weekend, "use_crime_count": self.use_crime_count,
                "use_volume_agg": self.use_volume_agg, "use_severity_agg": self.use_severity_agg,
                "agg_source": self.agg_source, "use_te": self.use_te,
                "te_m": self.te_m, "te_folds": self.te_folds}


def register_for_unpickle():
    """Daftarkan kelas ini sebagai `__main__.FeatureAssembler`.

    Wajib dipanggil SEBELUM joblib.load() pada bundle checkpoint. Idempotent, dan
    tidak menimpa kalau `__main__` memang sudah punya kelas tersebut (mis. saat
    kode ini dijalankan dari dalam notebook itu sendiri).
    """
    import sys

    main = sys.modules.get("__main__")
    if main is not None and not hasattr(main, "FeatureAssembler"):
        main.FeatureAssembler = FeatureAssembler
        for _n in ("BASE_COLS", "VOLUME_HO1", "VOLUME_REFIT", "SEVERITY_AGG", "TARGET_COL"):
            if not hasattr(main, _n):
                setattr(main, _n, globals()[_n])
