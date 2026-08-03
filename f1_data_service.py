"""세션 단위 원본 데이터(OpenF1 shape) 캐시 게이트웨이.

AI 에이전트(F1_XR_AI)가 필요로 하는 조회형 데이터를 session_key 기준으로 제공한다.
원칙:
  - 캐시(F1_CACHE_ROOT/{session_key}/{resource}.json)가 있으면 디스크에서 반환.
  - 없으면 OpenF1에서 받아 캐시한 뒤 반환.
  - OpenF1이 막혔고(라이브 세션 401 등) 캐시도 없으면 OpenF1Unavailable.
이렇게 해서 데이터 소유·캐싱은 서버가 전담하고, OpenF1 가용성과 무관하게 동작한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from config import F1_CACHE_ROOT
from openf1_client import (
    fetch_drivers,
    fetch_intervals,
    fetch_laps,
    fetch_pit,
    fetch_position,
    fetch_race_control,
    fetch_sessions,
    fetch_stints,
    fetch_weather,
)


# resource 이름 → OpenF1 fetch 함수(모두 session_key 하나만 받는다)
RESOURCE_FETCHERS = {
    "drivers": fetch_drivers,
    "race_control": fetch_race_control,
    "position": fetch_position,
    "intervals": fetch_intervals,
    "pit": fetch_pit,
    "stints": fetch_stints,
    "laps": fetch_laps,
    "weather": fetch_weather,
}


class OpenF1Unavailable(RuntimeError):
    """캐시도 없고 OpenF1도 못 받은 상태."""


def _resource_path(session_key: int, resource: str) -> Path:
    return F1_CACHE_ROOT / str(session_key) / f"{resource}.json"


def _sessions_path(year: int) -> Path:
    return F1_CACHE_ROOT / "sessions" / f"{year}.json"


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def get_resource(
    session_key: int,
    resource: str,
    driver_number: int | None = None,
) -> list[dict]:
    if resource not in RESOURCE_FETCHERS:
        raise KeyError(resource)

    path = _resource_path(session_key, resource)

    if path.exists():
        rows = _load(path)
    else:
        try:
            rows = RESOURCE_FETCHERS[resource](session_key)
        except Exception as exc:
            raise OpenF1Unavailable(
                f"OpenF1 unavailable and no cache for session {session_key} "
                f"'{resource}': {exc}"
            ) from exc

        # 빈 결과는 캐시하지 않는다(라이브 이후 재수집 여지를 남김).
        if rows:
            _save(path, rows)

    if driver_number is not None:
        rows = [
            row for row in rows
            if str(row.get("driver_number")) == str(driver_number)
        ]

    return rows


def search_sessions(
    year: int,
    country: str | None = None,
    circuit: str | None = None,
    session_name: str | None = None,
) -> list[dict]:
    path = _sessions_path(year)

    if path.exists():
        sessions = _load(path)
    else:
        try:
            sessions = fetch_sessions(year)
        except Exception as exc:
            raise OpenF1Unavailable(
                f"OpenF1 unavailable and no cache for sessions {year}: {exc}"
            ) from exc

        if sessions:
            _save(path, sessions)

    def matches(session: dict) -> bool:
        if country and str(session.get("country_name", "")).lower() != country.lower():
            return False
        if circuit and str(session.get("circuit_short_name", "")).lower() != circuit.lower():
            return False
        if session_name and str(session.get("session_name", "")).lower() != session_name.lower():
            return False
        return True

    return [session for session in sessions if matches(session)]
