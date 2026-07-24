from __future__ import annotations

import re
from datetime import timedelta
from math import ceil

from event_service import build_replay_events, load_replay_events
from models import CreateDatasetRequest, DatasetManifest, ReplayChunk
from openf1_client import (
    fetch_car_data_range,
    fetch_drivers,
    fetch_laps,
    fetch_location_range,
    fetch_pit,
    fetch_position_range,
    fetch_race_control,
    fetch_session_by_key,
    fetch_starting_grid,
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
    starting_grid: list[dict],
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
        positions=build_position_samples(
            replay_start_time,
            positions,
            starting_grid if chunk_index == 0 else [],
        ),
        tires=tires,
    ).model_dump()


def create_dataset(request: CreateDatasetRequest) -> dict:
    try:
        return create_dataset_from_openf1(request)
    except Exception as exc:
        cached_manifest = find_cached_manifest(request)

        if cached_manifest is not None:
            print(f"[Cache fallback] dataset session_key={request.sessionKey}: {exc}")
            dataset_id = cached_manifest["datasetId"]
            cached_manifest["events"] = build_replay_events(
                dataset_id,
                cached_manifest,
            )
            save_manifest(dataset_id, cached_manifest)
            return load_manifest(dataset_id)

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
    pits = fetch_pit(request.sessionKey)
    race_control = fetch_race_control(request.sessionKey)
    starting_grid = fetch_starting_grid(request.sessionKey)
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
    save_raw(dataset_id, "pit", pits)
    save_raw(dataset_id, "race_control", race_control)
    save_raw(dataset_id, "starting_grid", starting_grid)
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
        events=load_replay_events(int(session["session_key"])),
        chunkMinutes=request.chunkMinutes,
        overlapSeconds=request.overlapSeconds,
        durationSeconds=round(duration_seconds, 3),
        requestedDurationSeconds=round(requested_duration_seconds, 3),
        readyUntilT=0.0,
        playbackStartChunkIndex=playback_start_chunk_index,
        playbackStartT=round(playback_start_t, 3),
        **build_race_control_summary(
            session_start,
            race_control,
            requested_duration_seconds,
        ),
        chunks=chunks,
    ).model_dump()

    sync_existing_chunks(dataset_id, manifest)
    seed_cached_starting_grid(dataset_id, starting_grid)
    manifest["events"] = build_replay_events(dataset_id, manifest)
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
        manifest["events"] = build_replay_events(dataset_id, manifest)
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
            manifest["events"] = build_replay_events(dataset_id, manifest)
            update_ready_until(manifest)
            update_playback_start(dataset_id, manifest)
            save_manifest(dataset_id, manifest)
            return manifest

    session_key = int(manifest["sessionKey"])

    from storage import load_json, raw_path

    session = load_json(raw_path(dataset_id, "session"))
    laps = load_json(raw_path(dataset_id, "laps"))
    stints = load_json(raw_path(dataset_id, "stints"))
    starting_grid_path = raw_path(dataset_id, "starting_grid")
    starting_grid = load_json(starting_grid_path) if starting_grid_path.exists() else []

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
            starting_grid=starting_grid,
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
        manifest["events"] = build_replay_events(dataset_id, manifest)
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

    for driver_samples in samples_by_driver.values():
        driver_samples.sort(key=lambda item: item["t"])

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
    return nearest if abs(nearest["t"] - t) <= MAX_TELEMETRY_GAP_SECONDS else {}


def safe_float(value) -> float:
    return 0.0 if value is None else float(value)


def safe_int(value) -> int:
    return 0 if value is None else int(value)


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


def build_position_samples(
    replay_start_time,
    positions: list[dict],
    starting_grid: list[dict] | None = None,
) -> list[dict]:
    samples = []
    seen = set()

    for row in starting_grid or []:
        driver_number = row.get("driver_number")
        position = row.get("position")

        if driver_number is None or position is None:
            continue

        key = (int(driver_number), 0.0, int(position))
        if key in seen:
            continue

        seen.add(key)
        samples.append({
            "t": 0.0,
            "driverNumber": int(driver_number),
            "position": int(position),
        })

    for row in positions:
        driver_number = int(row["driver_number"])
        position = int(row["position"])
        sample_time = parse_iso(row["date"])
        t = (sample_time - replay_start_time).total_seconds()
        key = (driver_number, round(t, 3), position)

        if key in seen:
            continue

        seen.add(key)

        samples.append({
            "t": round(t, 3),
            "driverNumber": driver_number,
            "position": position,
        })

    samples.sort(key=lambda item: (item["driverNumber"], item["t"]))
    return samples


def build_race_control_summary(
    replay_start_time,
    rows: list[dict],
    requested_duration_seconds: float,
) -> dict:
    events = []

    for row in rows:
        date = row.get("date")
        if not date:
            continue

        t = (parse_iso(date) - replay_start_time).total_seconds()
        if t < 0.0:
            continue

        events.append({
            "startT": round(t, 3),
            "endT": round(t, 3),
            "t": round(t, 3),
            "date": str(date),
            "category": str(row.get("category") or ""),
            "flag": str(row.get("flag") or ""),
            "scope": str(row.get("scope") or ""),
            "sector": safe_int(row.get("sector")),
            "message": str(row.get("message") or ""),
        })

    events.sort(key=lambda item: item["t"])

    race_starts = [
        event["t"]
        for event in events
        if event["category"].lower() == "sessionstatus" and
        "SESSION STARTED" in event["message"].upper()
    ]
    race_ends = [
        event["t"]
        for event in events
        if event["flag"].upper() == "CHEQUERED"
    ]

    for index, event in enumerate(events):
        flag = event["flag"].upper()
        if flag in ("YELLOW", "DOUBLE YELLOW"):
            event["endT"] = find_flag_end(events, index)
        elif flag == "RED":
            event["endT"] = find_red_flag_end(events, index)

    visible_end = max(0.0, float(requested_duration_seconds))
    race_start_t = race_starts[0] if race_starts else 0.0
    race_end_t = race_ends[0] if race_ends else 0.0

    return {
        "raceStartT": round(race_start_t, 3)
        if race_start_t <= visible_end else 0.0,
        "raceEndT": round(race_end_t, 3)
        if 0.0 < race_end_t <= visible_end else 0.0,
        "yellowFlags": [
            event
            for event in events
            if event["flag"].upper() in ("YELLOW", "DOUBLE YELLOW") and
            event["t"] <= visible_end
        ],
        "redFlags": [
            event
            for event in events
            if event["flag"].upper() == "RED" and
            event["t"] <= visible_end
        ],
    }


def find_flag_end(events: list[dict], start_index: int) -> float:
    start = events[start_index]

    for event in events[start_index + 1:]:
        flag = event["flag"].upper()

        if flag == "RED":
            return event["t"]

        if flag != "CLEAR":
            continue

        if start["scope"].lower() == "track":
            return event["t"]

        if event["sector"] == start["sector"]:
            return event["t"]

    return start["t"]


def find_red_flag_end(events: list[dict], start_index: int) -> float:
    start = events[start_index]

    for event in events[start_index + 1:]:
        if (
            event["category"].lower() == "sessionstatus" and
            "SESSION STARTED" in event["message"].upper()
        ):
            return event["t"]

    return start["t"]


def seed_cached_starting_grid(dataset_id: str, starting_grid: list[dict]) -> None:
    if not starting_grid or not chunk_exists(dataset_id, 0):
        return

    chunk = load_chunk(dataset_id, 0)
    existing_positions = chunk.get("positions", [])
    seeded_positions = build_position_samples(
        parse_iso("1970-01-01T00:00:00Z"),
        [],
        starting_grid,
    )
    seeded_drivers = {
        int(sample["driverNumber"])
        for sample in existing_positions
        if float(sample.get("t", -1.0)) == 0.0
    }

    for sample in seeded_positions:
        if int(sample["driverNumber"]) not in seeded_drivers:
            existing_positions.append(sample)

    existing_positions.sort(key=lambda item: (item["driverNumber"], item["t"]))
    chunk["positions"] = existing_positions
    save_chunk(dataset_id, 0, chunk)


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
