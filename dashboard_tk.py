from __future__ import annotations

import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import BOTH, DISABLED, END, LEFT, NORMAL, RIGHT, X, Button, Frame, Label, Tk
from tkinter.scrolledtext import ScrolledText


PROJECT_ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8000
HEALTH_URL = f"http://{HOST}:{PORT}/health"


class ServerDashboard:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("F1 XR 서버 대시보드")
        self.root.geometry("820x520")

        self.process: subprocess.Popen[str] | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        status_frame = Frame(self.root)
        status_frame.pack(fill=X, padx=12, pady=(12, 6))

        self.status_indicator = Label(status_frame, text="●", fg="gray", font=("맑은 고딕", 14))
        self.status_indicator.pack(side=LEFT, padx=(0, 6))

        self.status_label = Label(status_frame, text="서버 상태: 확인 중", anchor="w")
        self.status_label.pack(side=LEFT, fill=X, expand=True)

        button_frame = Frame(self.root)
        button_frame.pack(fill=X, padx=12, pady=6)

        self.start_button = Button(button_frame, text="서버 시작", command=self.start_server, width=14)
        self.start_button.pack(side=LEFT, padx=(0, 8))

        self.stop_button = Button(button_frame, text="서버 종료", command=self.stop_server, width=14)
        self.stop_button.pack(side=LEFT, padx=(0, 8))

        self.refresh_button = Button(button_frame, text="상태 새로고침", command=self.check_server_status, width=14)
        self.refresh_button.pack(side=LEFT)

        self.clear_button = Button(button_frame, text="로그 지우기", command=self.clear_logs, width=14)
        self.clear_button.pack(side=RIGHT)

        log_label = Label(self.root, text="로그", anchor="w")
        log_label.pack(fill=X, padx=12, pady=(12, 4))

        self.log_text = ScrolledText(self.root, state=DISABLED, height=22)
        self.log_text.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.flush_logs)
        self.root.after(500, self.check_server_status)

    def run(self) -> None:
        self.write_log("대시보드를 시작했습니다.")
        self.root.mainloop()

    def start_server(self) -> None:
        if self.process and self.process.poll() is None:
            self.write_log("서버가 이미 실행 중입니다.")
            return

        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ]

        self.write_log("서버를 시작합니다.")
        self.process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        threading.Thread(target=self.read_server_output, daemon=True).start()
        self.root.after(1000, self.check_server_status)

    def stop_server(self) -> None:
        if not self.process or self.process.poll() is not None:
            pid = self.find_listening_server_pid()

            if pid is None:
                self.write_log("종료할 서버 프로세스가 없습니다.")
                self.check_server_status()
                return

            self.write_log(f"이미 실행 중인 서버를 종료합니다. PID={pid}")
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode == 0:
                self.write_log("서버 종료 명령을 보냈습니다.")
            else:
                self.write_log(f"서버 종료 실패: {result.stderr.strip() or result.stdout.strip()}")

            self.check_server_status()
            return

        self.write_log("서버를 종료합니다.")
        self.process.terminate()

        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.write_log("정상 종료가 지연되어 강제 종료합니다.")
            self.process.kill()
            self.process.wait(timeout=5)

        self.write_log("서버가 종료되었습니다.")
        self.check_server_status()

    def find_listening_server_pid(self) -> int | None:
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"Get-NetTCPConnection -LocalPort {PORT} -State Listen "
                "-ErrorAction SilentlyContinue | "
                "Select-Object -First 1 -ExpandProperty OwningProcess"
            ),
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        pid = result.stdout.strip()

        if pid.isdigit():
            return int(pid)

        return None

    def check_server_status(self) -> None:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
                if response.status == 200:
                    self.status_indicator.config(fg="green")
                    self.status_label.config(text=f"서버 상태: 실행 중 ({HEALTH_URL})")
                    return
        except (urllib.error.URLError, TimeoutError):
            pass

        self.status_indicator.config(fg="red")
        self.status_label.config(text="서버 상태: 중지됨")

    def read_server_output(self) -> None:
        if not self.process or not self.process.stdout:
            return

        for line in self.process.stdout:
            self.log_queue.put(line.rstrip())

        self.log_queue.put("서버 로그 스트림이 종료되었습니다.")

    def flush_logs(self) -> None:
        while not self.log_queue.empty():
            self.write_log(self.log_queue.get_nowait())

        self.root.after(200, self.flush_logs)

    def write_log(self, message: str) -> None:
        self.log_text.config(state=NORMAL)
        self.log_text.insert(END, f"{message}\n")
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def clear_logs(self) -> None:
        self.log_text.config(state=NORMAL)
        self.log_text.delete("1.0", END)
        self.log_text.config(state=DISABLED)

    def on_close(self) -> None:
        if self.process and self.process.poll() is None:
            self.stop_server()

        self.root.destroy()


if __name__ == "__main__":
    ServerDashboard().run()
