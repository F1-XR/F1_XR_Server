"""Jolpica-F1 클라이언트 — 선수 커리어·통산 기록(Ergast 후계 API).

OpenF1이 '지금 이 경기'만 준다면, Jolpica는 통산 우승·생년월일·국적 등
'시즌 무관 고정 경력'을 준다. 무료·키 불필요. openf1_client.py와 같은 stdlib 스타일.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from config import JOLPICA_BASE_URL, OPENF1_REQUEST_TIMEOUT


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{JOLPICA_BASE_URL}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=OPENF1_REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_driver_profile(driver_id: str) -> dict | None:
    """드라이버 프로필. driver_id 예: 'hamilton', 'max_verstappen'.

    반환: {givenName, familyName, nationality, dateOfBirth, ...}
    """
    data = _get(f"drivers/{driver_id}.json")
    drivers = data.get("MRData", {}).get("DriverTable", {}).get("Drivers", [])
    return drivers[0] if drivers else None


def fetch_driver_wins(driver_id: str) -> int:
    """통산 우승(1위 완주) 횟수. results/1 의 total 로 단일 호출."""
    data = _get(f"drivers/{driver_id}/results/1.json", {"limit": 1})
    total = data.get("MRData", {}).get("total")
    return int(total) if total is not None else 0
