from __future__ import annotations

import re
from datetime import timedelta
from math import ceil

from models import CreateDatasetRequest, DatasetManifest, ReplayChunk
from openf1_client import (
    fetch_car_data_range,
    fetch_drivers,
    fetch_laps,
    fetch_location_range,
    fetch_position_range, 
    fetch_session_by_key,
    fetch_stints,
    parse_iso,
)
from storage import (
    DATA_ROOT,
    chunk_exists,
    load_chunk,
    load_manifest,
    save_chunk,
    save_manifest,
    save_raw,
)


MAX_TELEMETRY_GAP_SECONDS = 1.0
ENGINE_SAMPLE_FIELDS = ("rpm", "throttle", "speed", "nGear", "n_gear", "brake", "drs")


def normalize_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def make_dataset_id(session: dict, request: CreateDatasetRequest) -> str:
    year = int(session["year"])
    circuit = normalize_slug(str(session.get("circuit_short_name", "unknown")))
    session_name = normalize_slug(str(session.get("session_name", "session")))
    session_key = int(session["session_key"])

    return (
        f"{year}_{circuit}_{session_name}_{session_key}"
        f"_c{request.chunkMinutes}"
        f"_o{request.overlapSeconds}"
        f"_m{request.requestedMinutes}"
    )


def build_transform_config(session: dict) -> dict:
    return {
        "circuit": str(session.get("circuit_short_name", "")),
        "scale": 0.01,
        "heightScale": 0.01,
        "rotationY": 0.0,
        "translation": [0.0, 0.0, 0.0],
        "unityX": "x",
        "unityY": "z",
        "unityZ": "y",
    }


def build_driver_infos(drivers: list[dict]) -> list[dict]:
    result = []

    for driver in drivers:
        result.append({
            "driverNumber": int(driver["driver_number"]),
            "nameAcronym": str(driver.get("name_acronym", "")),
            "fullName": str(driver.get("full_name", "")),
            "teamName": str(driver.get("team_name", "")),
            "teamColour": driver.get("team_colour"),
        })

    return result


def build_replay_chunk(
    dataset_id: str,
    chunk_index: int,
    chunk_start_t: float,
    chunk_end_t: float,
    overlap_seconds: int,
    replay_start_time,
    locations: list[dict],
    car_data: list[dict],
    positions: list[dict],
    tires: list[dict],
) -> dict:
    samples = []
    car_data_by_driver = build_car_data_index(replay_start_time, car_data)

    seen = set()

    for row in locations:
        driver_number = int(row["driver_number"])
        key = (
            row.get("session_key"),
            driver_number,
            row.get("date"),
            row.get("x"),
            row.get("y"),
            row.get("z"),
        )

        if key in seen:
            continue

        seen.add(key)

        sample_time = parse_iso(row["date"])
        t = (sample_time - replay_start_time).total_seconds()
        telemetry = find_nearest_car_data(car_data_by_driver.get(driver_number, []), t)

        samples.append({
            "t": round(t, 3),
            "driverNumber": driver_number,
            "x": float(row["x"]),
            "y": float(row["y"]),
            "z": float(row["z"]),
            "rpm": float(telemetry.get("rpm", 0.0)),
            "throttle": float(telemetry.get("throttle", 0.0)),
            "speed": float(telemetry.get("speed", 0.0)),
            "nGear": int(telemetry.get("n_gear", 0)),
            "n_gear": int(telemetry.get("n_gear", 0)),
            "brake": int(telemetry.get("brake", 0)),
            "drs": int(telemetry.get("drs", 0)),
        })

    samples.sort(key=lambda item: (item["driverNumber"], item["t"]))

    return ReplayChunk(
        datasetId=dataset_id,
        chunkIndex=chunk_index,
        startT=round(chunk_start_t, 3),
        endT=round(chunk_end_t, 3),
        overlapSeconds=overlap_seconds,
        samples=samples,
        positions=build_position_samples(replay_start_time, positions),
        tires=tires,
    ).model_dump()


def create_dataset(request: CreateDatasetRequest) -> dict:
    try:
        return create_dataset_from_openf1(request)
    except Exception as exc:
        cached_manifest = find_cached_manifest(request)

        if cached_manifest is not None:
            print(f"[Cache fallback] dataset session_key={request.sessionKey}: {exc}")
            return cached_manifest

        raise


def find_cached_manifest(request: CreateDatasetRequest) -> dict | None:
    if not DATA_ROOT.exists():
        return None

    exact_matches = []
    session_matches = []

    for dataset_path in DATA_ROOT.iterdir():
        if not dataset_path.is_dir():
            continue

        try:
            manifest = load_manifest(dataset_path.name)
        except Exception as exc:
            print(f"[Cache fallback] skipped invalid manifest {dataset_path.name}: {exc}")
            continue

        if int(manifest.get("sessionKey", -1)) != int(request.sessionKey):
            continue

        session_matches.append(manifest)

        if (
            int(manifest.get("chunkMinutes", -1)) == int(request.chunkMinutes)
            and int(manifest.get("overlapSeconds", -1)) == int(request.overlapSeconds)
            and int(manifest.get("requestedDurationSeconds", 0)) >= int(request.requestedMinutes) * 60
        ):
            exact_matches.append(manifest)

    matches = exact_matches or session_matches

    if not matches:
        return None

    matches.sort(
        key=lambda manifest: (
            manifest.get("status") == "complete",
            float(manifest.get("readyUntilT") or 0.0),
        ),
        reverse=True,
    )

    return matches[0]


def create_dataset_from_openf1(request: CreateDatasetRequest) -> dict:
    session = fetch_session_by_key(request.sessionKey)
    dataset_id = make_dataset_id(session, request)

    session_start = parse_iso(session["date_start"])

    if session.get("date_end"):
        session_end = parse_iso(session["date_end"])
    else:
        session_end = session_start + timedelta(hours=2)

    drivers = fetch_drivers(request.sessionKey)
    laps = fetch_laps(request.sessionKey)
    stints = fetch_stints(request.sessionKey)
    playback_start_t = find_first_lap_start_t(session_start, laps) if request.skipWarmupLap else 0.0
    duration_seconds = max(0.0, (session_end - session_start).total_seconds())
    requested_duration_seconds = min(
        duration_seconds,
        playback_start_t + max(60.0, float(request.requestedMinutes) * 60.0),
    )
    chunk_seconds = request.chunkMinutes * 60
    total_chunks = max(1, ceil(requested_duration_seconds / chunk_seconds))
    playback_start_chunk_index = min(
        total_chunks - 1,
        max(0, int(playback_start_t // chunk_seconds)),
    )

    save_raw(dataset_id, "session", session)
    save_raw(dataset_id, "drivers", drivers)
    save_raw(dataset_id, "laps", laps)
    save_raw(dataset_id, "stints", stints)
    save_raw(dataset_id, "transform", build_transform_config(session))

    chunks = []

    for index in range(total_chunks):
        start_t = index * chunk_seconds
        end_t = min(requested_duration_seconds, (index + 1) * chunk_seconds)

        chunks.append({
            "index": index,
            "startT": round(start_t, 3),
            "endT": round(end_t, 3),
            "status": "pending",
            "sampleCount": 0,
            "error": None,
        })

    manifest = DatasetManifest(
        datasetId=dataset_id,
        status="pending",
        error=None,
        year=int(session["year"]),
        circuit=str(session.get("circuit_short_name", "")),
        sessionKey=int(session["session_key"]),
        meetingKey=int(session["meeting_key"]),
        sessionName=str(session.get("session_name", "")),
        drivers=build_driver_infos(drivers),
        chunkMinutes=request.chunkMinutes,
        overlapSeconds=request.overlapSeconds,
        durationSeconds=round(duration_seconds, 3),
        requestedDurationSeconds=round(requested_duration_seconds, 3),
        readyUntilT=0.0,
        playbackStartChunkIndex=playback_start_chunk_index,
        playbackStartT=round(playback_start_t, 3),
        chunks=chunks,
    ).model_dump()

    sync_existing_chunks(dataset_id, manifest)
    save_manifest(dataset_id, manifest)

    return load_manifest(dataset_id)


def download_dataset_chunks(dataset_id: str, start_index: int = 0) -> dict:
    manifest = load_manifest(dataset_id)
    manifest["status"] = "downloading"
    manifest["error"] = None
    save_manifest(dataset_id, manifest)

    try:
        for index in range(start_index, len(manifest["chunks"])):
            manifest = load_manifest(dataset_id)
            status = manifest["chunks"][index].get("status")

            if status in ("ready", "empty"):
                continue

            prepare_chunk(dataset_id, index)

        manifest = load_manifest(dataset_id)
        manifest["status"] = "complete"
        manifest["error"] = None
        update_ready_until(manifest)
        update_playback_start(dataset_id, manifest)
        save_manifest(dataset_id, manifest)
        return manifest

    except Exception as exc:
        manifest = load_manifest(dataset_id)
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        save_manifest(dataset_id, manifest)
        raise


def prepare_chunk(dataset_id: str, chunk_index: int) -> dict:
    manifest = load_manifest(dataset_id)

    if chunk_index < 0 or chunk_index >= len(manifest["chunks"]):
        raise ValueError(f"Invalid chunk index: {chunk_index}")

    if chunk_exists(dataset_id, chunk_index):
        chunk_data = load_chunk(dataset_id, chunk_index)

        if chunk_has_required_fields(chunk_data):
            sample_count = len(chunk_data.get("samples", []))
            manifest["chunks"][chunk_index]["status"] = "ready" if sample_count > 0 else "empty"
            manifest["chunks"][chunk_index]["sampleCount"] = sample_count
            manifest["chunks"][chunk_index]["error"] = None
            update_ready_until(manifest)
            update_playback_start(dataset_id, manifest)
            save_manifest(dataset_id, manifest)
            return manifest

    session_key = int(manifest["sessionKey"])

    from storage import load_json, raw_path

    session = load_json(raw_path(dataset_id, "session"))
    laps = load_json(raw_path(dataset_id, "laps"))
    stints = load_json(raw_path(dataset_id, "stints"))

    replay_start_time = parse_iso(session["date_start"])
    chunk = manifest["chunks"][chunk_index]

    chunk_start_t = float(chunk["startT"])
    chunk_end_t = float(chunk["endT"])
    overlap = int(manifest["overlapSeconds"])

    request_start_t = max(0.0, chunk_start_t - overlap)
    request_end_t = min(float(manifest["durationSeconds"]), chunk_end_t + overlap)

    request_start_time = replay_start_time + timedelta(seconds=request_start_t)
    request_end_time = replay_start_time + timedelta(seconds=request_end_t)

    manifest["chunks"][chunk_index]["status"] = "downloading"
    manifest["chunks"][chunk_index]["error"] = None
    save_manifest(dataset_id, manifest)

    try:
        locations = fetch_location_range(
            session_key=session_key,
            start=request_start_time,
            end=request_end_time,
        )

        positions = fetch_position_range(
            session_key=session_key,
            start=request_start_time,
            end=request_end_time,
        )

        car_data = fetch_car_data_range(
            session_key=session_key,
            start=request_start_time,
            end=request_end_time,
        )

        save_raw(dataset_id, f"position_chunk_{chunk_index:04d}", positions)
        save_raw(dataset_id, f"location_chunk_{chunk_index:04d}", locations)
        save_raw(dataset_id, f"car_data_chunk_{chunk_index:04d}", car_data)

        replay_chunk = build_replay_chunk(
            dataset_id=dataset_id,
            chunk_index=chunk_index,
            chunk_start_t=chunk_start_t,
            chunk_end_t=chunk_end_t,
            overlap_seconds=overlap,
            replay_start_time=replay_start_time,
            locations=locations,
            car_data=car_data,
            positions=positions,
            tires=build_tire_samples(
                replay_start_time=replay_start_time,
                chunk_start_t=request_start_t,
                chunk_end_t=request_end_t,
                laps=laps,
                stints=stints,
            ),
        )

        save_chunk(dataset_id, chunk_index, replay_chunk)

        manifest = load_manifest(dataset_id)
        sample_count = len(replay_chunk.get("samples", []))
        manifest["chunks"][chunk_index]["status"] = "ready" if sample_count > 0 else "empty"
        manifest["chunks"][chunk_index]["sampleCount"] = sample_count
        manifest["chunks"][chunk_index]["error"] = None
        update_ready_until(manifest)
        update_playback_start(dataset_id, manifest)
        save_manifest(dataset_id, manifest)

        return manifest

    except Exception as exc:
        manifest = load_manifest(dataset_id)
        manifest["chunks"][chunk_index]["status"] = "failed"
        manifest["chunks"][chunk_index]["error"] = str(exc)
        save_manifest(dataset_id, manifest)
        raise


def prefetch_chunks(dataset_id: str, start_index: int, count: int) -> dict:
    manifest = load_manifest(dataset_id)

    end_index = min(len(manifest["chunks"]), start_index + count)

    for index in range(start_index, end_index):
        prepare_chunk(dataset_id, index)

    return load_manifest(dataset_id)


def sync_existing_chunks(dataset_id: str, manifest: dict) -> None:
    for chunk in manifest["chunks"]:
        index = int(chunk["index"])

        if not chunk_exists(dataset_id, index):
            continue

        chunk_data = load_chunk(dataset_id, index)

        if not chunk_has_required_fields(chunk_data):
            chunk["status"] = "pending"
            chunk["sampleCount"] = 0
            chunk["error"] = None
            continue

        sample_count = len(chunk_data.get("samples", []))
        chunk["status"] = "ready" if sample_count > 0 else "empty"
        chunk["sampleCount"] = sample_count
        chunk["error"] = None

    update_ready_until(manifest)
    update_playback_start(dataset_id, manifest)


def chunk_has_required_fields(chunk_data: dict) -> bool:
    if "tires" not in chunk_data:
        return False

    samples = chunk_data.get("samples", [])
    if not samples:
        return True

    return all(field in samples[0] for field in ENGINE_SAMPLE_FIELDS)


def build_car_data_index(replay_start_time, car_data: list[dict]) -> dict[int, list[dict]]:
    samples_by_driver: dict[int, list[dict]] = {}
    seen = set()

    for row in car_data:
        if row.get("date") is None or row.get("driver_number") is None:
            continue

        driver_number = int(row["driver_number"])
        key = (driver_number, row.get("date"))

        if key in seen:
            continue

        seen.add(key)
        sample_time = parse_iso(row["date"])
        t = (sample_time - replay_start_time).total_seconds()

        samples_by_driver.setdefault(driver_number, []).append({
            "t": t,
            "rpm": safe_float(row.get("rpm")),
            "throttle": safe_float(row.get("throttle")),
            "speed": safe_float(row.get("speed")),
            "n_gear": safe_int(row.get("n_gear")),
            "brake": safe_int(row.get("brake")),
            "drs": safe_int(row.get("drs")),
        })

    for samples in samples_by_driver.values():
        samples.sort(key=lambda item: item["t"])

    return samples_by_driver


def find_nearest_car_data(samples: list[dict], t: float) -> dict:
    if not samples:
        return {}

    low = 0
    high = len(samples) - 1

    while low < high:
        mid = (low + high) // 2

        if samples[mid]["t"] < t:
            low = mid + 1
        else:
            high = mid

    candidates = [samples[low]]

    if low > 0:
        candidates.append(samples[low - 1])

    nearest = min(candidates, key=lambda item: abs(item["t"] - t))

    if abs(nearest["t"] - t) > MAX_TELEMETRY_GAP_SECONDS:
        return {}

    return nearest


def safe_float(value) -> float:
    if value is None:
        return 0.0

    return float(value)


def safe_int(value) -> int:
    if value is None:
        return 0

    return int(value)


def update_ready_until(manifest: dict) -> None:
    ready_until = 0.0

    for chunk in manifest["chunks"]:
        if chunk["status"] not in ("ready", "empty"):
            break

        ready_until = float(chunk["endT"])

    manifest["readyUntilT"] = round(ready_until, 3)


def update_playback_start(dataset_id: str, manifest: dict) -> None:
    requested_start_t = float(manifest.get("playbackStartT") or 0.0)

    if requested_start_t > 0.0:
        for chunk in manifest["chunks"]:
            if chunk.get("status") != "ready":
                continue

            if float(chunk["startT"]) <= requested_start_t <= float(chunk["endT"]):
                manifest["playbackStartChunkIndex"] = int(chunk["index"])
                manifest["playbackStartT"] = requested_start_t
                return

    for chunk in manifest["chunks"]:
        if chunk.get("status") != "ready":
            continue

        index = int(chunk["index"])

        if not chunk_exists(dataset_id, index):
            continue

        chunk_data = load_chunk(dataset_id, index)

        if not chunk_data.get("samples"):
            continue

        manifest["playbackStartChunkIndex"] = index
        if requested_start_t <= 0.0:
            manifest["playbackStartT"] = float(chunk["startT"])
        else:
            manifest["playbackStartT"] = requested_start_t
        return

    manifest["playbackStartChunkIndex"] = 0
    manifest["playbackStartT"] = requested_start_t


def build_position_samples(replay_start_time, positions: list[dict]) -> list[dict]:
    samples = []
    seen = set()

    for row in positions:
        key = (
            row.get("session_key"),
            row.get("driver_number"),
            row.get("date"),
            row.get("position"),
        )

        if key in seen:
            continue

        seen.add(key)

        sample_time = parse_iso(row["date"])
        t = (sample_time - replay_start_time).total_seconds()

        samples.append({
            "t": round(t, 3),
            "driverNumber": int(row["driver_number"]),
            "position": int(row["position"]),
        })

    samples.sort(key=lambda item: (item["driverNumber"], item["t"]))
    return samples


def build_tire_samples(
    replay_start_time,
    chunk_start_t: float,
    chunk_end_t: float,
    laps: list[dict],
    stints: list[dict],
) -> list[dict]:
    samples = []
    stints_by_driver: dict[int, list[dict]] = {}

    for stint in stints:
        driver_number = int(stint["driver_number"])
        stints_by_driver.setdefault(driver_number, []).append(stint)

    for driver_stints in stints_by_driver.values():
        driver_stints.sort(key=lambda item: int(item.get("lap_start") or 0))

    for lap in laps:
        if not lap.get("date_start") or lap.get("lap_number") is None:
            continue

        driver_number = int(lap["driver_number"])
        lap_number = int(lap["lap_number"])
        stint = find_stint(stints_by_driver.get(driver_number, []), lap_number)

        if stint is None:
            continue

        sample_time = parse_iso(lap["date_start"])
        t = (sample_time - replay_start_time).total_seconds()

        if t < chunk_start_t or t > chunk_end_t:
            continue

        tire_age = stint.get("tyre_age_at_start")
        if tire_age is not None:
            tire_age = int(tire_age) + max(0, lap_number - int(stint.get("lap_start") or lap_number))

        samples.append({
            "t": round(t, 3),
            "driverNumber": driver_number,
            "compound": str(stint.get("compound", "")),
            "tireAge": tire_age,
        })

    samples.sort(key=lambda item: (item["driverNumber"], item["t"]))
    return samples


def find_stint(stints: list[dict], lap_number: int) -> dict | None:
    for stint in stints:
        lap_start = int(stint.get("lap_start") or 0)
        lap_end = stint.get("lap_end")

        if lap_end is None:
            lap_end = 999
        else:
            lap_end = int(lap_end)

        if lap_start <= lap_number <= lap_end:
            return stint

    return None


def find_first_lap_start_t(session_start, laps: list[dict]) -> float:
    first_lap_start = None

    for lap in laps:
        if not lap.get("date_start"):
            continue

        lap_start = parse_iso(lap["date_start"])

        if first_lap_start is None or lap_start < first_lap_start:
            first_lap_start = lap_start

    if first_lap_start is None:
        return 0.0

    return max(0.0, (first_lap_start - session_start).total_seconds())




