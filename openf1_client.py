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
    # Preserve milliseconds. Truncating every cutoff to ``.000`` made runtime
    # speed/location almost one second older than the 1 Hz training grid.
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "")


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


def fetch_meeting_by_key(meeting_key: int) -> dict:
    meetings = api_get("meetings", {"meeting_key": meeting_key})
    return meetings[0] if meetings else {}


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


def fetch_pit(session_key: int) -> list[dict]:
    try:
        return api_get("pit", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_race_control(session_key: int) -> list[dict]:
    try:
        return api_get("race_control", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_weather(session_key: int) -> list[dict]:
    """세션 단위 날씨(공기·트랙 온도·습도·강수 등). race_control과 동일하게 가벼운 세션 조회.
    추월 예측 모델의 track_temperature/air_temperature/humidity/rainfall 피처에 쓰인다."""
    try:
        return api_get("weather", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_position(session_key: int) -> list[dict]:
    try:
        return api_get("position", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_intervals(session_key: int) -> list[dict]:
    try:
        return api_get("intervals", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_starting_grid(session_key: int) -> list[dict]:
    try:
        starting_grid = api_get(
            "starting_grid",
            {"session_key": session_key},
            retry=1,
        )
        if starting_grid:
            return starting_grid
    except HTTPError as exc:
        if exc.code != 404:
            raise

    try:
        positions = api_get("position", {"session_key": session_key})
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise

    first_by_driver = {}

    for row in sorted(positions, key=lambda item: str(item.get("date", ""))):
        driver_number = row.get("driver_number")
        position = row.get("position")

        if driver_number is None or position is None:
            continue

        first_by_driver.setdefault(int(driver_number), row)

    return sorted(
        first_by_driver.values(),
        key=lambda item: int(item["position"]),
    )


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


def fetch_car_data_range(session_key: int, start: datetime, end: datetime) -> list[dict]:
    try:
        return api_get("car_data", {
            "session_key": session_key,
            "date>=": format_api_date(start),
            "date<": format_api_date(end),
        })
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_car_data_window(
    session_key: int, driver_number: int, start: datetime, end: datetime
) -> list[dict]:
    """시점 근처 car_data(speed·drs)를 '드라이버 1명 + 짧은 시간창'으로 좁혀 조회.
    car_data는 세션 전체가 초대용량(수십만~백만 행)이라 절대 통째로 받지 않는다.
    추월 예측의 speed·drs_active·speed_delta 피처에 쓰인다."""
    try:
        return api_get("car_data", {
            "session_key": session_key,
            "driver_number": driver_number,
            "date>=": format_api_date(start),
            "date<": format_api_date(end),
        })
    except HTTPError as exc:
        if exc.code == 404:
            return []

        raise


def fetch_location_window(
    session_key: int, driver_number: int, start: datetime, end: datetime
) -> list[dict]:
    """시점/랩 구간의 위치 좌표(x·y)를 '드라이버 1명 + 시간창'으로 조회.
    location도 세션 전체가 초대용량이라 창으로만 받는다. 두 용도:
      ① 트랙 기준선용 = 깨끗한 한 바퀴 창(경기당 1회)  ② 시점 좌표 = 짧은 창(투영용).
    추월 예측의 track_progress·sin·cos·segment 피처에 쓰인다."""
    try:
        return api_get("location", {
            "session_key": session_key,
            "driver_number": driver_number,
            "date>=": format_api_date(start),
            "date<": format_api_date(end),
        })
    except HTTPError as exc:
        if exc.code == 404:
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
