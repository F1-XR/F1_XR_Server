from __future__ import annotations

from datetime import datetime
from time import monotonic

from models import SessionOption, TrackOption
from openf1_client import fetch_sessions
from storage import DATA_ROOT, load_json, manifest_path, raw_path


MIN_YEAR = 2023
SESSION_CACHE_SECONDS = 300.0

_session_cache: dict[int, tuple[float, list[dict]]] = {}


def get_years() -> dict:
    current_year = datetime.utcnow().year
    cached_years = set()

    for manifest in cached_manifests():
        year = manifest.get("year")

        if year is not None:
            cached_years.add(int(year))

    all_years = set(range(MIN_YEAR, current_year + 1)) | cached_years
    ordered_years = sorted(cached_years) + sorted(all_years - cached_years)

    return {
        "years": ordered_years
    }


def get_tracks(year: int) -> dict:
    if year < MIN_YEAR:
        raise ValueError(f"year must be >= {MIN_YEAR}")

    try:
        sessions = sessions_for_year(year)
        return tracks_from_sessions(year, sessions)
    except Exception as exc:
        print(f"[Cache fallback] tracks year={year}: {exc}")
        return tracks_from_cache(year)


def get_sessions(year: int, circuit_key: int) -> dict:
    if year < MIN_YEAR:
        raise ValueError(f"year must be >= {MIN_YEAR}")

    try:
        sessions = sessions_for_year(year)
        return sessions_from_openf1(year, circuit_key, sessions)
    except Exception as exc:
        print(f"[Cache fallback] sessions year={year}, circuit_key={circuit_key}: {exc}")
        return sessions_from_cache(year, circuit_key)


def sessions_for_year(year: int) -> list[dict]:
    now = monotonic()
    cached = _session_cache.get(year)

    if cached is not None and now - cached[0] < SESSION_CACHE_SECONDS:
        return cached[1]

    sessions = fetch_sessions(year)
    _session_cache[year] = (now, sessions)
    return sessions


def tracks_from_sessions(year: int, sessions: list[dict]) -> dict:
    by_circuit: dict[int, dict] = {}

    for session in sessions:
        circuit_key = session.get("circuit_key")

        if circuit_key is None:
            continue

        circuit_key = int(circuit_key)

        if circuit_key not in by_circuit:
            by_circuit[circuit_key] = {
                "circuitKey": circuit_key,
                "circuitShortName": str(session.get("circuit_short_name", "")),
                "location": str(session.get("location", "")),
                "countryName": str(session.get("country_name", "")),
                "meetingName": str(session.get("meeting_name", "")),
            }

    tracks = [
        TrackOption(**track).model_dump()
        for track in by_circuit.values()
    ]

    tracks.sort(key=lambda item: item["meetingName"])

    return {
        "year": year,
        "tracks": tracks,
    }


def sessions_from_openf1(year: int, circuit_key: int, sessions: list[dict]) -> dict:
    result = []

    for session in sessions:
        if int(session.get("circuit_key", -1)) != circuit_key:
            continue

        result.append(session_option_from_session(session, year).model_dump())

    result.sort(key=lambda item: item["dateStart"])

    return {
        "year": year,
        "circuitKey": circuit_key,
        "sessions": result,
    }


def tracks_from_cache(year: int) -> dict:
    by_circuit: dict[int, dict] = {}

    for manifest in cached_manifests(year):
        session = cached_session(manifest)
        circuit_key = int(session.get("circuit_key") or manifest.get("sessionKey") or 0)

        if circuit_key <= 0 or circuit_key in by_circuit:
            continue

        by_circuit[circuit_key] = {
            "circuitKey": circuit_key,
            "circuitShortName": str(session.get("circuit_short_name") or manifest.get("circuit") or ""),
            "location": str(session.get("location", "")),
            "countryName": str(session.get("country_name", "")),
            "meetingName": str(session.get("meeting_name") or manifest.get("circuit") or "Cached Replay"),
        }

    tracks = [TrackOption(**track).model_dump() for track in by_circuit.values()]
    tracks.sort(key=lambda item: item["meetingName"])

    return {
        "year": year,
        "tracks": tracks,
    }


def sessions_from_cache(year: int, circuit_key: int) -> dict:
    result = []

    for manifest in cached_manifests(year):
        session = cached_session(manifest)

        if int(session.get("circuit_key") or manifest.get("sessionKey") or -1) != circuit_key:
            continue

        result.append(session_option_from_cache(manifest, session, year).model_dump())

    result.sort(key=lambda item: item["dateStart"])

    return {
        "year": year,
        "circuitKey": circuit_key,
        "sessions": result,
    }


def cached_manifests(year: int | None = None) -> list[dict]:
    if not DATA_ROOT.exists():
        return []

    manifests = []

    for dataset_path in DATA_ROOT.iterdir():
        if not dataset_path.is_dir():
            continue

        path = manifest_path(dataset_path.name)

        if not path.exists():
            continue

        try:
            manifest = load_json(path)
        except Exception as exc:
            print(f"[Cache fallback] skipped invalid manifest {path}: {exc}")
            continue

        if year is not None and int(manifest.get("year", -1)) != year:
            continue

        manifests.append(manifest)

    return manifests


def cached_session(manifest: dict) -> dict:
    try:
        return load_json(raw_path(str(manifest["datasetId"]), "session"))
    except Exception:
        return {}


def session_option_from_session(session: dict, year: int) -> SessionOption:
    return SessionOption(
        sessionKey=int(session["session_key"]),
        meetingKey=int(session["meeting_key"]),
        circuitKey=int(session["circuit_key"]),
        circuitShortName=str(session.get("circuit_short_name", "")),
        location=str(session.get("location", "")),
        countryName=str(session.get("country_name", "")),
        meetingName=str(session.get("meeting_name", "")),
        sessionName=str(session.get("session_name", "")),
        sessionType=str(session.get("session_type", "")),
        dateStart=str(session.get("date_start", "")),
        dateEnd=session.get("date_end"),
        year=int(session.get("year", year)),
    )


def session_option_from_cache(manifest: dict, session: dict, year: int) -> SessionOption:
    return SessionOption(
        sessionKey=int(manifest["sessionKey"]),
        meetingKey=int(manifest.get("meetingKey") or session.get("meeting_key") or 0),
        circuitKey=int(session.get("circuit_key") or manifest["sessionKey"]),
        circuitShortName=str(session.get("circuit_short_name") or manifest.get("circuit") or ""),
        location=str(session.get("location", "")),
        countryName=str(session.get("country_name", "")),
        meetingName=str(session.get("meeting_name") or manifest.get("circuit") or "Cached Replay"),
        sessionName=str(manifest.get("sessionName") or session.get("session_name") or "Cached Replay"),
        sessionType=str(session.get("session_type", "")),
        dateStart=str(session.get("date_start", "")),
        dateEnd=session.get("date_end"),
        year=int(manifest.get("year", year)),
    )

