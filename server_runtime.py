from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
SERVER_LOG_PATH = LOG_DIR / "server.log"
HOST = "127.0.0.1"
PORT = 8000


def start_server_process() -> tuple[subprocess.Popen[str], object]:
    """Start one server process and send all output to the shared log file."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = SERVER_LOG_PATH.open("w", encoding="utf-8", buffering=1)
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_file
