from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DATA_ROOT
from event_service import attach_replay_events


def dataset_dir(dataset_id: str) -> Path:
    return DATA_ROOT / dataset_id


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_path(dataset_id: str) -> Path:
    return dataset_dir(dataset_id) / "manifest.json"


def raw_path(dataset_id: str, name: str) -> Path:
    return dataset_dir(dataset_id) / "raw" / f"{name}.json"


def chunk_path(dataset_id: str, chunk_index: int) -> Path:
    return dataset_dir(dataset_id) / "chunks" / f"chunk_{chunk_index:04d}.json"


def save_manifest(dataset_id: str, manifest: dict) -> None:
    save_json(manifest_path(dataset_id), manifest)


def load_manifest(dataset_id: str) -> dict:
    return attach_replay_events(load_json(manifest_path(dataset_id)))


def save_raw(dataset_id: str, name: str, data: Any) -> None:
    save_json(raw_path(dataset_id, name), data)


def save_chunk(dataset_id: str, chunk_index: int, chunk: dict) -> None:
    save_json(chunk_path(dataset_id, chunk_index), chunk)


def load_chunk(dataset_id: str, chunk_index: int) -> dict:
    return load_json(chunk_path(dataset_id, chunk_index))


def chunk_exists(dataset_id: str, chunk_index: int) -> bool:
    return chunk_path(dataset_id, chunk_index).exists()
