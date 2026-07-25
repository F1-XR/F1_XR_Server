from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from catalog_service import get_sessions, get_tracks, get_years
from chunk_service import create_dataset, download_dataset_chunks, prefetch_chunks, prepare_chunk
from config import CORS_ORIGINS
from career_service import get_career
from f1_data_service import RESOURCE_FETCHERS, OpenF1Unavailable, get_resource, search_sessions
from models import CreateDatasetRequest
from storage import load_chunk, load_manifest, load_json, raw_path


app = FastAPI(
    title="F1 OpenF1 Chunk Replay API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
    }


# ── AI 에이전트용 세션 데이터 게이트웨이 (OpenF1 shape, 캐시 우선) ──

@app.get("/f1/sessions")
def f1_sessions(
    year: int,
    country: str | None = None,
    circuit: str | None = None,
    session_name: str | None = None,
) -> list[dict]:
    try:
        return search_sessions(year, country, circuit, session_name)
    except OpenF1Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/f1/{session_key}/career/{driver_number}")
def f1_career(session_key: int, driver_number: int) -> dict:
    try:
        return get_career(session_key, driver_number)
    except OpenF1Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/f1/{session_key}/{resource}")
def f1_resource(
    session_key: int,
    resource: str,
    driver_number: int | None = None,
) -> list[dict]:
    if resource not in RESOURCE_FETCHERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown resource '{resource}'. "
                   f"allowed: {sorted(RESOURCE_FETCHERS)}",
        )
    try:
        return get_resource(session_key, resource, driver_number)
    except OpenF1Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/catalog/years")
def catalog_years() -> dict:
    return get_years()


@app.get("/catalog/tracks")
def catalog_tracks(year: int) -> dict:
    try:
        return get_tracks(year)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/catalog/sessions")
def catalog_sessions(year: int, circuit_key: int) -> dict:
    try:
        return get_sessions(year, circuit_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/datasets")
def datasets_create(
    request: CreateDatasetRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        manifest = create_dataset(request)
        dataset_id = manifest["datasetId"]

        background_tasks.add_task(download_dataset_chunks, dataset_id, int(manifest.get("playbackStartChunkIndex", 0)))

        return manifest

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/datasets/{dataset_id}/manifest")
def datasets_manifest(dataset_id: str) -> dict:
    try:
        return load_manifest(dataset_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/datasets/{dataset_id}/chunks/{chunk_index}/prepare")
def chunks_prepare(
    dataset_id: str,
    chunk_index: int,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        background_tasks.add_task(prepare_chunk, dataset_id, chunk_index)

        return {
            "datasetId": dataset_id,
            "chunkIndex": chunk_index,
            "status": "queued",
        }

    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/datasets/{dataset_id}/chunks/{chunk_index}")
def chunks_get(dataset_id: str, chunk_index: int) -> dict:
    try:
        return load_chunk(dataset_id, chunk_index)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/datasets/{dataset_id}/prefetch")
def chunks_prefetch(
    dataset_id: str,
    background_tasks: BackgroundTasks,
    start_index: int,
    count: int = 2,
) -> dict:
    try:
        background_tasks.add_task(prefetch_chunks, dataset_id, start_index, count)

        return {
            "datasetId": dataset_id,
            "startIndex": start_index,
            "count": count,
            "status": "queued",
        }

    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/datasets/{dataset_id}/drivers")
def datasets_drivers(dataset_id: str) -> dict:
    try:
        return {
            "datasetId": dataset_id,
            "drivers": load_json(raw_path(dataset_id, "drivers")),
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/datasets/{dataset_id}/laps")
def datasets_laps(dataset_id: str) -> dict:
    try:
        return {
            "datasetId": dataset_id,
            "laps": load_json(raw_path(dataset_id, "laps")),
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/datasets/{dataset_id}/transform")
def datasets_transform(dataset_id: str) -> dict:
    try:
        return load_json(raw_path(dataset_id, "transform"))
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
