from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


def get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return int(value)


def get_env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return [item.strip() for item in value.split(",") if item.strip()]


def get_env_path(name: str, default: Path) -> Path:
    value = get_env(name, str(default))
    path = Path(value).expanduser()

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


OPENF1_BASE_URL = get_env("OPENF1_BASE_URL", "https://api.openf1.org/v1")
OPENF1_REQUEST_TIMEOUT = get_env_int("OPENF1_REQUEST_TIMEOUT", 30)
CORS_ORIGINS = get_env_list("CORS_ORIGINS", ["*"])
DATA_ROOT = get_env_path("DATA_ROOT", PROJECT_ROOT / "data" / "api_datasets")
