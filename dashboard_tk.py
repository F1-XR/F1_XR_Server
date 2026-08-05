from __future__ import annotations

import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import BOTH, DISABLED, END, LEFT, NORMAL, RIGHT, X, Button, Frame, Label, Tk
from tkinter.scrolledtext import ScrolledText

from server_runtime import SERVER_LOG_PATH, start_server_process


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
        self.server_log_file: object | None = None
        self.log_position = 0
        self.pending_connection_reset_trace: list[str] = []

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
        self.root.after(200, self.tail_server_log)
        self.root.after(500, self.schedule_status_check)
        self.root.after(100, self.show_window)

    def run(self) -> None:
        self.write_log("대시보드를 시작했습니다.")
        self.root.mainloop()

    def show_window(self) -> None:
        """Bring a dashboard opened from the tray into the foreground."""
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def start_server(self) -> None:
        if self.is_server_running():
            self.write_log("서버가 이미 실행 중입니다.")
            self.check_server_status()
            return

        if self.process and self.process.poll() is None:
            self.write_log("서버가 이미 실행 중입니다.")
            return

        self.write_log("서버를 시작합니다.")
        self.process, self.server_log_file = start_server_process()
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
        if self.server_log_file:
            self.server_log_file.close()
            self.server_log_file = None
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

    def check_server_status(self) -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
                if response.status == 200:
                    self.status_indicator.config(fg="green")
                    self.status_label.config(text=f"서버 상태: 실행 중 ({HEALTH_URL})")
                    return True
        except (urllib.error.URLError, TimeoutError):
            pass

        self.status_indicator.config(fg="red")
        self.status_label.config(text="서버 상태: 중지됨")
        return False

    def schedule_status_check(self) -> None:
        running = self.check_server_status()
        self.root.after(2000 if running else 8000, self.schedule_status_check)

    @staticmethod
    def is_server_running() -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError):
            return False

    def tail_server_log(self) -> None:
        try:
            if SERVER_LOG_PATH.exists():
                size = SERVER_LOG_PATH.stat().st_size
                if size < self.log_position:
                    self.log_position = 0

                with SERVER_LOG_PATH.open("r", encoding="utf-8", errors="replace") as log_file:
                    log_file.seek(self.log_position)
                    for line in log_file:
                        self.display_server_log_line(line.rstrip())
                    self.log_position = log_file.tell()
        finally:
            self.root.after(300, self.tail_server_log)

    def display_server_log_line(self, line: str) -> None:
        if self.pending_connection_reset_trace:
            self.pending_connection_reset_trace.append(line)
            if "ConnectionResetError: [WinError 10054]" in line:
                self.write_log("클라이언트 연결이 종료되었습니다.")
                self.pending_connection_reset_trace.clear()
            elif not line:
                for trace_line in self.pending_connection_reset_trace:
                    self.write_log(self.humanize_server_log(trace_line))
                self.pending_connection_reset_trace.clear()
            return

        if "Exception in callback _ProactorBasePipeTransport._call_connection_lost()" in line:
            self.pending_connection_reset_trace.append(line)
            return

        self.write_log(self.humanize_server_log(line))

    @staticmethod
    def humanize_server_log(line: str) -> str:
        """Translate common server output into concise Korean dashboard messages."""
        startup_messages = {
            "Waiting for application startup.": "서버 애플리케이션을 시작하는 중입니다.",
            "Application startup complete.": "서버가 준비되었습니다.",
            "Shutting down": "서버를 종료하는 중입니다.",
            "Waiting for application shutdown.": "서버 종료를 준비하는 중입니다.",
            "Application shutdown complete.": "서버가 정상적으로 종료되었습니다.",
        }
        for source, translated in startup_messages.items():
            if source in line:
                return translated

        if match := re.search(r"Started server process \[(\d+)\]", line):
            return f"서버 프로세스를 시작했습니다. (PID: {match.group(1)})"
        if match := re.search(r"Finished server process \[(\d+)\]", line):
            return f"서버 프로세스가 종료되었습니다. (PID: {match.group(1)})"
        if "Uvicorn running on " in line:
            return "서버가 실행 중입니다: " + line.split("Uvicorn running on ", 1)[1].split(" ", 1)[0]
        if "[Errno 10048]" in line:
            return "서버 시작 실패: 8000번 포트를 다른 프로그램이 사용 중입니다."

        if match := re.search(r'"([A-Z]+) ([^ ]+) HTTP/[^\"]+" (\d+)', line):
            return f"요청 완료: {match.group(1)} {match.group(2)} (상태 코드: {match.group(3)})"
        if line.startswith("[GET] "):
            return "OpenF1 데이터 요청: " + line[6:]
        if line.startswith("[OpenF1 HTTP ERROR]"):
            return "OpenF1 요청 오류: " + line.replace("[OpenF1 HTTP ERROR] ", "")
        if line.startswith("[OpenF1 URL ERROR]"):
            return "OpenF1 연결 오류: " + line.replace("[OpenF1 URL ERROR] ", "")
        if line.startswith("[Cache fallback]"):
            return "로컬 캐시를 사용합니다: " + line.replace("[Cache fallback] ", "")
        if line.startswith("[OpenF1] location empty range:"):
            return "위치 데이터가 없는 구간입니다: " + line.split(":", 1)[1].strip()

        return line

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
