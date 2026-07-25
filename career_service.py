"""선수 커리어 게이트웨이 — 번호→Jolpica id 해석 + 커리어 캐시 제공.

AI 에이전트가 session_key + driver_number로 물으면, 서버가 Jolpica id를 해석해
통산 기록(생년월일·국적·통산우승)을 캐시 우선으로 돌려준다. 커리어는 세션 무관이라
Jolpica id 기준으로 캐시한다(F1_CACHE_ROOT/career/{id}.json).
"""
from __future__ import annotations

import json
from pathlib import Path

from config import F1_CACHE_ROOT
from f1_data_service import get_resource
from jolpica_client import fetch_driver_profile, fetch_driver_wins


# 데모 대상 경기의 드라이버 번호 → Jolpica id (정확 매칭용).
# 매핑에 없으면 세션 drivers 캐시의 last_name 소문자로 근사한다.
NUMBER_TO_JOLPICA: dict[int, str] = {
    1: "max_verstappen",
    44: "hamilton",
    16: "leclerc",
    4: "norris",
    63: "russell",
    55: "sainz",
    81: "piastri",
    11: "perez",
    14: "alonso",
    # TODO: 대상 경기 드라이버 번호 → Jolpica id 채우기
}


def _career_path(jolpica_id: str) -> Path:
    return F1_CACHE_ROOT / "career" / f"{jolpica_id}.json"


def resolve_jolpica_id(session_key: int, driver_number: int) -> str | None:
    """번호 → Jolpica id. 매핑 우선, 없으면 세션 drivers 캐시의 성(last_name)."""
    if driver_number in NUMBER_TO_JOLPICA:
        return NUMBER_TO_JOLPICA[driver_number]

    drivers = get_resource(session_key, "drivers", driver_number)  # 캐시 우선
    if drivers:
        last = drivers[0].get("last_name")
        if last:
            return str(last).lower()
    return None


def get_career(session_key: int, driver_number: int) -> dict:
    """통산 기록을 캐시 우선으로 반환. 못 찾으면 빈 dict."""
    jolpica_id = resolve_jolpica_id(session_key, driver_number)
    if not jolpica_id:
        return {}

    path = _career_path(jolpica_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        profile = fetch_driver_profile(jolpica_id) or {}
    except Exception:
        profile = {}
    try:
        wins = fetch_driver_wins(jolpica_id)
    except Exception:
        wins = 0

    career = {
        "jolpicaId": jolpica_id,
        "dateOfBirth": profile.get("dateOfBirth"),
        "nationality": profile.get("nationality"),
        "wins": wins,
    }

    # 프로필을 실제로 받았을 때만 캐시(실패분 고착 방지)
    if profile:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(career, ensure_ascii=False), encoding="utf-8")

    return career
