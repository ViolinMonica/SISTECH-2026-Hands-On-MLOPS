"""REST API Risk Score - Checkpoint 3.

Menjalankan:
    python -m uvicorn app:app --reload --port 8000

Dokumentasi interaktif (otomatis dari FastAPI):
    http://127.0.0.1:8000/docs

Endpoint utama mengikuti contoh skema pada soal:
    GET /risk-score?lat=41.8819&lon=-87.6278&datetime=2024-11-18T21:30:00

Kenapa FastAPI: validasi tipe query param datang gratis dari anotasi, dan
OpenAPI/Swagger ter-generate sendiri - itu yang dipakai tim konsumen sebagai
kontrak, jadi tidak perlu menulis dokumen skema terpisah yang gampang basi.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import monitoring as mon
from predict import InputError, get_scorer

METRICS_PERSIST_EVERY = 10  # tulis metrics.json tiap N prediksi (bukan tiap request)

state: dict = {"scorer": None, "metrics": None, "logger": None, "since_persist": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Model dimuat SEKALI saat startup. Memuat per request akan menambah ~1 detik
    # dan membuat latensi didominasi I/O, bukan inferensi.
    scorer = get_scorer()
    state["scorer"] = scorer
    state["logger"] = mon.PredictionLogger()
    state["metrics"] = mon.RuntimeMetrics(reference=scorer.reference)
    print(f"[startup] champion {scorer.model_version} dimuat | "
          f"{len(scorer.feature_names)} fitur | MAE={scorer.champion['metrics']['MAE']:.3f}")

    # Warm-up. Prediksi pertama menanggung inisialisasi malas sklearn/pandas:
    # terukur ~1.500 ms, sedangkan panggilan berikutnya ~10 ms. Tanpa ini, beban
    # tersebut jatuh ke request user pertama dan mengotori statistik latensi.
    t0 = time.perf_counter()
    scorer.predict(41.8819, -87.6278, "2024-01-01T12:00:00")
    print(f"[startup] warm-up selesai dalam {(time.perf_counter() - t0) * 1000:.0f} ms")
    try:
        yield
    finally:
        state["metrics"].persist(scorer)
        print("[shutdown] snapshot metrik ditulis ke monitoring/metrics.json")


app = FastAPI(
    title="Crime Risk Score API",
    description=(
        "Estimasi Risk Score (0-100) untuk sebuah lokasi dan waktu di Chicago, "
        "dilayani dari model champion hasil Checkpoint 2 (HistGradientBoosting, "
        "continual learning + model registry)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Izinkan API ini dipanggil langsung dari browser oleh tim Front-End (origin
# berbeda, mis. localhost:3000 -> localhost:8000). Tanpa ini, request dari
# JavaScript di browser akan diblokir oleh browser sendiri (CORS policy),
# walau API-nya sendiri berjalan normal. "*" cukup untuk skala tugas ini;
# ganti ke origin spesifik punya FE kalau mau lebih ketat.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------- error handling
@app.exception_handler(InputError)
async def _input_error_handler(request: Request, exc: InputError):
    if state["metrics"]:
        state["metrics"].record_error("InputError")
    if state["logger"]:
        state["logger"].write({
            "timestamp": mon._utcnow(), "request_id": mon.new_request_id(),
            "endpoint": str(request.url.path), "status": "error",
            "error_type": "InputError", "error": str(exc),
            "query": dict(request.query_params),
        })
    return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """Validasi bawaan FastAPI (param hilang / tipe salah).

    Tanpa handler ini, error tersebut dikembalikan 422 oleh FastAPI tetapi TIDAK
    pernah lewat metrik kita - error rate jadi terlihat lebih baik dari kenyataan.
    Terbukti saat simulasi trafik: 500 request dikirim, hanya 494 tercatat.
    """
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg', '')}"
        for e in exc.errors()
    ) or "parameter request tidak valid"
    if state["metrics"]:
        state["metrics"].record_error("RequestValidationError")
    if state["logger"]:
        state["logger"].write({
            "timestamp": mon._utcnow(), "request_id": mon.new_request_id(),
            "endpoint": str(request.url.path), "status": "error",
            "error_type": "RequestValidationError", "error": detail,
            "query": dict(request.query_params),
        })
    return JSONResponse(status_code=422, content={"error": "invalid_input", "detail": detail})


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    if state["metrics"]:
        state["metrics"].record_error(type(exc).__name__)
    if state["logger"]:
        state["logger"].write({
            "timestamp": mon._utcnow(), "request_id": mon.new_request_id(),
            "endpoint": str(request.url.path), "status": "error",
            "error_type": type(exc).__name__, "error": str(exc),
            "query": dict(request.query_params),
        })
    return JSONResponse(status_code=500,
                        content={"error": "internal_error", "detail": type(exc).__name__})


# ------------------------------------------------------------------- endpoints
@app.get("/", tags=["meta"], summary="Ringkasan layanan")
def root():
    s = state["scorer"]
    return {
        "service": "Crime Risk Score API",
        "status": "running",
        "model_version": s.model_version,
        "docs": "/docs",
        "endpoints": {
            "GET /risk-score": "estimasi risk score dari lat, lon, datetime",
            "POST /risk-score/batch": "estimasi banyak titik sekaligus",
            "GET /health": "liveness + readiness",
            "GET /model-info": "metadata & metrik model champion",
            "GET /versions": "riwayat versi model dari registry",
            "GET /metrics": "metrik operasional & drift output",
            "GET /logs/recent": "log prediksi terakhir",
        },
    }


@app.get("/health", tags=["meta"], summary="Health check")
def health():
    s = state["scorer"]
    ready = s is not None and s.model is not None
    return {
        "status": "healthy" if ready else "unhealthy",
        "model_loaded": ready,
        "model_version": s.model_version if ready else None,
        "integrity_verified": s.integrity_ok if ready else None,
        "uptime_seconds": round(
            (mon.datetime.now(mon.timezone.utc) - state["metrics"].started_at).total_seconds(), 1),
    }


@app.get("/risk-score", tags=["prediksi"], summary="Estimasi Risk Score satu titik")
def risk_score(
    lat: float = Query(..., description="Latitude, contoh 41.8819", examples=[41.8819]),
    lon: float = Query(..., description="Longitude, contoh -87.6278", examples=[-87.6278]),
    datetime_str: str = Query(
        ..., alias="datetime",
        description="Waktu ISO 8601, contoh 2024-11-18T21:30:00",
        examples=["2024-11-18T21:30:00"]),
):
    """Mengembalikan estimasi Risk Score beserta level risikonya.

    `risk_score` dibulatkan ke bilangan bulat agar cocok dengan contoh skema di
    soal; `risk_score_raw` menyimpan nilai presisinya untuk konsumen yang butuh.
    """
    t0 = time.perf_counter()
    scorer = state["scorer"]
    result = scorer.predict(lat, lon, datetime_str)
    latency_ms = (time.perf_counter() - t0) * 1000

    request_id = mon.new_request_id()
    result["request_id"] = request_id
    result["latency_ms"] = round(latency_ms, 2)

    state["metrics"].record_prediction(result, latency_ms)
    state["logger"].write({
        "timestamp": mon._utcnow(),
        "request_id": request_id,
        "endpoint": "/risk-score",
        "status": "ok",
        "input": result["input"],
        "cell_id": result["cell_id"],
        "risk_score": result["risk_score"],
        "risk_score_raw": result["risk_score_raw"],
        "level": result["level"],
        "model_version": result["model_version"],
        "feature_source": result["feature_source"],
        "warnings": result["warnings"],
        "latency_ms": round(latency_ms, 2),
    })

    _maybe_persist()
    return result


class BatchItem(BaseModel):
    lat: float = Field(..., examples=[41.8819])
    lon: float = Field(..., examples=[-87.6278])
    datetime: str = Field(..., examples=["2024-11-18T21:30:00"])


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(..., max_length=500,
                                   description="Maksimum 500 titik per request.")


@app.post("/risk-score/batch", tags=["prediksi"], summary="Estimasi banyak titik")
def risk_score_batch(payload: BatchRequest):
    """Untuk konsumen yang butuh mewarnai peta - hemat round-trip HTTP.

    Item yang gagal tidak menggagalkan seluruh batch; tiap elemen membawa
    statusnya sendiri supaya kegagalan parsial tetap terlihat jelas.
    """
    t0 = time.perf_counter()
    scorer, results, n_ok = state["scorer"], [], 0

    for i, item in enumerate(payload.items):
        ti = time.perf_counter()
        try:
            r = scorer.predict(item.lat, item.lon, item.datetime)
            lat_ms = (time.perf_counter() - ti) * 1000
            r["status"] = "ok"
            r["index"] = i
            state["metrics"].record_prediction(r, lat_ms)
            results.append(r)
            n_ok += 1
        except InputError as exc:
            state["metrics"].record_error("InputError")
            results.append({"index": i, "status": "error",
                            "error": "invalid_input", "detail": str(exc)})

    total_ms = (time.perf_counter() - t0) * 1000
    state["logger"].write({
        "timestamp": mon._utcnow(),
        "request_id": mon.new_request_id(),
        "endpoint": "/risk-score/batch",
        "status": "ok",
        "n_items": len(payload.items),
        "n_ok": n_ok,
        "n_failed": len(payload.items) - n_ok,
        "model_version": scorer.model_version,
        "latency_ms": round(total_ms, 2),
    })
    _maybe_persist()
    return {
        "count": len(results),
        "succeeded": n_ok,
        "failed": len(results) - n_ok,
        "model_version": scorer.model_version,
        "latency_ms": round(total_ms, 2),
        "results": results,
    }


@app.get("/model-info", tags=["model"], summary="Metadata model champion")
def model_info():
    return state["scorer"].model_info()


@app.get("/versions", tags=["model"], summary="Riwayat versi model")
def versions():
    scorer = state["scorer"]
    return {
        "champion": scorer.model_version,
        "history": mon.model_history(scorer),
    }


@app.get("/metrics", tags=["monitoring"], summary="Metrik operasional & drift")
def metrics(persist: bool = Query(False, description="Tulis juga ke monitoring/metrics.json")):
    scorer = state["scorer"]
    if persist:
        return state["metrics"].persist(scorer)
    return state["metrics"].snapshot(scorer)


@app.get("/logs/recent", tags=["monitoring"], summary="Log prediksi terakhir")
def logs_recent(n: int = Query(20, ge=1, le=500)):
    records = state["logger"].tail(n)
    return {"count": len(records), "log_file": state["logger"].path, "records": records}


def _maybe_persist():
    state["since_persist"] += 1
    if state["since_persist"] >= METRICS_PERSIST_EVERY:
        state["since_persist"] = 0
        try:
            state["metrics"].persist(state["scorer"])
        except OSError:
            pass  # monitoring tidak boleh menjatuhkan jalur prediksi


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
