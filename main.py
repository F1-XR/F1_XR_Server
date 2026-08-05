from __future__ import annotations

from datetime import datetime
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from catalog_service import get_sessions, get_tracks, get_years
from chunk_service import create_dataset, download_dataset_chunks, prefetch_chunks, prepare_chunk
from config import CORS_ORIGINS
from career_service import get_career
from f1_data_service import RESOURCE_FETCHERS, OpenF1Unavailable, get_resource, search_sessions
from openf1_client import fetch_car_data_window, fetch_location_window
from models import CreateDatasetRequest
from storage import load_chunk, load_manifest, load_json, raw_path


class HealthCheckLogFilter(logging.Filter):
    """Keep automatic local health checks out of Uvicorn's access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return " /health " not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HealthCheckLogFilter())


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


# 주의: 이 라우트는 아래 generic '/{resource}' 보다 먼저 선언해야 매칭된다(FastAPI는 선언 순서).
@app.get("/f1/{session_key}/car_data")
def f1_car_data(
    session_key: int,
    driver_number: int,
    start: str,
    end: str,
) -> list[dict]:
    """시점 근처 car_data(speed·drs) 창 — 추월 예측 피처용. start/end는 ISO 시각.
    세션 전체 car_data는 초대용량이라 반드시 드라이버 1명 + 짧은 시간창으로만 조회한다.
    (캐시 안 함: 창이 작아 가볍고, 시점마다 달라 캐시 이득이 적다.)"""
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad start/end ISO: {exc}")
    try:
        return fetch_car_data_window(session_key, driver_number, s, e)
    except Exception as exc:   # OpenF1 오류 시 빈 목록 대신 503(호출부가 폴백)
        raise HTTPException(status_code=503, detail=f"car_data unavailable: {exc}")


@app.get("/f1/{session_key}/location")
def f1_location(
    session_key: int,
    driver_number: int,
    start: str,
    end: str,
) -> list[dict]:
    """위치 좌표(x·y) 창 — track_progress 피처용. start/end는 ISO 시각.
    트랙 기준선(깨끗한 한 바퀴)·시점 좌표 모두 이 라우트로 창 조회한다."""
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad start/end ISO: {exc}")
    try:
        return fetch_location_window(session_key, driver_number, s, e)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"location unavailable: {exc}")


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

        if manifest.get("status") != "complete":
            background_tasks.add_task(
                download_dataset_chunks,
                dataset_id,
                int(manifest.get("playbackStartChunkIndex", 0)),
            )

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
