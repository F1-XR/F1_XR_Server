from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from config import OPENF1_BASE_URL, OPENF1_REQUEST_TIMEOUT

BASE_URL = OPENF1_BASE_URL
COMPARISON_OPERATORS = [">=", "<=", ">", "<"]


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_api_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000")


def build_query(params: dict) -> str:
    parts = []

    for key, value in params.items():
        matched = False

        for op in COMPARISON_OPERATORS:
            if key.endswith(op):
                field = key[:-len(op)]
                parts.append(
                    f"{urllib.parse.quote(str(field), safe='_')}"
                    f"{op}"
                    f"{urllib.parse.quote(str(value), safe=':-T.')}"
                )
                matched = True
                break

        if not matched:
            parts.append(
                f"{urllib.parse.quote(str(key), safe='_')}="
                f"{urllib.parse.quote(str(value), safe=':-T. ')}"
            )

    return "&".join(parts)


def api_get(endpoint: str, params: dict, retry: int = 3) -> list[dict]:
    url = f"{BASE_URL}/{endpoint}?{build_query(params)}"

    for attempt in range(1, retry + 1):
        try:
            print(f"[GET] {url}")

            with urllib.request.urlopen(url, timeout=OPENF1_REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))

        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            print(f"[OpenF1 HTTP ERROR] attempt={attempt}/{retry} {exc.code} {exc.reason}")
            print(body[:500])

            if attempt == retry:
                raise

            time.sleep(1.5 * attempt)

        except URLError as exc:
            print(f"[OpenF1 URL ERROR] attempt={attempt}/{retry} {exc}")

            if attempt == retry:
                raise

            time.sleep(1.5 * attempt)

    return []


def fetch_sessions(year: int) -> list[dict]:
    return api_get("sessions", {"year": year})


def fetch_session_by_key(session_key: int) -> dict:
    sessions = api_get("sessions", {"session_key": session_key})

    if not sessions:
        raise RuntimeError(f"Session not found: session_key={session_key}")

    return sessions[0]


def fetch_drivers(session_key: int) -> list[dict]:
    return api_get("drivers", {"session_key": session_key})


def fetch_laps(session_key: int) -> list[dict]:
    return api_get("laps", {"session_key": session_key})


def fetch_stints(session_key: int) -> list[dict]:
    try:
        return api_get("stints", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_location_range(session_key: int, start: datetime, end: datetime) -> list[dict]:
    try:
        return api_get("location", {
            "session_key": session_key,
            "date>=": format_api_date(start),
            "date<": format_api_date(end),
        })
    except HTTPError as exc:
        if exc.code == 404:
            print(
                "[OpenF1] location empty range: "
                f"session_key={session_key}, "
                f"start={format_api_date(start)}, "
                f"end={format_api_date(end)}"
            )
            return []

        raise


def fetch_position_range(session_key: int, start: datetime, end: datetime) -> list[dict]:
    try:
        return api_get("position", {
            "session_key": session_key,
            "date>=": format_api_date(start),
            "date<": format_api_date(end),
        })
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise
