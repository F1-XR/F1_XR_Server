from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from server_runtime import start_server_process


PROJECT_ROOT = Path(__file__).resolve().parent
LOGO_PATH = PROJECT_ROOT / "assets" / "f1_logo.png"
HOST = "127.0.0.1"
PORT = 8000
HEALTH_URL = f"http://{HOST}:{PORT}/health"
STARTUP_SHORTCUT = (
    Path.home()
    / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
    / "F1 XR Server Tray.lnk"
)
INSTANCE_MUTEX_NAME = "Local\\F1_XR_Server_Tray"


class TrayServer:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.server_log_file: object | None = None
        self.dashboard_process: subprocess.Popen[str] | None = None
        self.monitor_stop = threading.Event()
        self.icon = pystray.Icon(
            "f1_xr_server", self.create_icon(False), "F1 XR Server", self.create_menu()
        )

    @staticmethod
    def create_icon(running: bool) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 63, 63), radius=14, fill="#151515")

        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo.putdata(
            [
                (red, green, blue, 0) if red > 245 and green > 245 and blue > 245 else (red, green, blue, alpha)
                for red, green, blue, alpha in logo.getdata()
            ]
        )
        logo = logo.crop(logo.getbbox())
        if running:
            logo.putdata(
                [(37, 178, 108, alpha) if alpha else (0, 0, 0, 0) for _, _, _, alpha in logo.getdata()]
            )

        logo.thumbnail((64, 40), Image.Resampling.LANCZOS)
        # Windows renders notification icons at about 16 px. Slightly enlarging the
        # logo vertically keeps it legible at that small size while filling the tray slot.
        if logo.height < 28:
            logo = logo.resize((logo.width, 28), Image.Resampling.LANCZOS)
        logo_x = (64 - logo.width) // 2
        logo_y = (64 - logo.height) // 2
        image.alpha_composite(logo, (logo_x, logo_y))
        return image

    def create_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "서버 전환",
                self.toggle_server,
                default=True,
                visible=False,
            ),
            pystray.MenuItem(self.status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("서버 시작", self.start_server, enabled=lambda _: not self.is_server_running()),
            pystray.MenuItem("서버 종료", self.stop_server, enabled=lambda _: self.is_server_running()),
            pystray.MenuItem("상태 새로 고침", self.refresh_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Windows 시작 시 실행",
                self.toggle_startup,
                checked=lambda _: STARTUP_SHORTCUT.exists(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("대시보드 열기", self.open_dashboard),
            pystray.MenuItem("앱 종료", self.quit_app),
        )

    def toggle_server(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        running = self.is_server_running()
        if running:
            if not self.confirm_server_action("종료"):
                return
            self.stop_server(_, __)
        else:
            self.start_server(_, __)

    @staticmethod
    def confirm_server_action(action: str) -> bool:
        """Show a confirmation dialog outside the tray menu callback."""
        dialog_code = (
            "import sys\n"
            "import tkinter as tk\n"
            "from tkinter import messagebox\n"
            "root = tk.Tk()\n"
            "root.withdraw()\n"
            f"logo = tk.PhotoImage(file={str(LOGO_PATH)!r})\n"
            "root.iconphoto(True, logo)\n"
            "root.attributes('-topmost', True)\n"
            f"accepted = messagebox.askyesno('F1 XR Server', 'F1 XR 서버를 {action}할까요?', parent=root)\n"
            "root.destroy()\n"
            "sys.exit(0 if accepted else 1)\n"
        )
        result = subprocess.run([sys.executable, "-c", dialog_code], check=False)
        return result.returncode == 0

    @staticmethod
    def is_server_running() -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError):
            return False

    def status_text(self, _: pystray.MenuItem) -> str:
        return "서버 상태: 실행 중" if self.is_server_running() else "서버 상태: 중지됨"

    def notify(self, title: str, message: str) -> None:
        self.icon.notify(message, title)

    def update_icon(self) -> None:
        self.icon.icon = self.create_icon(self.is_server_running())
        self.icon.update_menu()

    def start_server(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        if self.is_server_running():
            self.notify("F1 XR Server", "서버가 이미 실행 중입니다.")
            self.update_icon()
            return

        self.process, self.server_log_file = start_server_process()
        threading.Thread(target=self.wait_for_server_start, daemon=True).start()

    def wait_for_server_start(self) -> None:
        deadline = time.monotonic() + 10.0

        while time.monotonic() < deadline:
            if self.is_server_running():
                self.notify("F1 XR Server", "서버를 시작했습니다.")
                self.open_dashboard(self.icon, None)
                self.update_icon()
                return

            if self.process and self.process.poll() is not None:
                self.notify("F1 XR Server", "서버가 시작되지 않았습니다. 로그를 확인하세요.")
                self.update_icon()
                return

            time.sleep(0.25)

        self.notify("F1 XR Server", "서버 시작 시간이 초과되었습니다. 로그를 확인하세요.")
        self.update_icon()

    def stop_server(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        else:
            pid = self.find_listening_server_pid()
            if pid is not None:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)

        self.notify("F1 XR Server", "서버를 종료했습니다.")
        if self.server_log_file:
            self.server_log_file.close()
            self.server_log_file = None
        self.close_dashboard()
        self.update_icon()

    @staticmethod
    def find_listening_server_pid() -> int | None:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess",
            ],
            capture_output=True,
            text=True,
        )
        pid = result.stdout.strip()
        return int(pid) if pid.isdigit() else None

    def refresh_status(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        running = self.is_server_running()
        self.notify("F1 XR Server", "서버가 실행 중입니다." if running else "서버가 중지되어 있습니다.")
        self.update_icon()

    def start_status_monitor(self, _: pystray.Icon) -> None:
        # Explicitly show the icon once the Windows tray message loop is ready.
        self.icon.visible = True
        threading.Thread(target=self.monitor_status, daemon=True).start()

    def monitor_status(self) -> None:
        """Keep the tray logo and menu current without manual refreshes."""
        while not self.monitor_stop.is_set():
            running = self.is_server_running()
            self.icon.icon = self.create_icon(running)
            self.icon.update_menu()
            interval_seconds = 2 if running else 8
            self.monitor_stop.wait(interval_seconds)

    @staticmethod
    def powershell_text(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def set_startup_shortcut(self) -> None:
        target = str(PROJECT_ROOT / "run_tray.bat")
        script = "\n".join(
            [
                "$decode = { param($v) [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($v)) }",
                f"$shortcutPath = & $decode '{self.powershell_text(str(STARTUP_SHORTCUT))}'",
                f"$targetPath = & $decode '{self.powershell_text(target)}'",
                f"$workingDirectory = & $decode '{self.powershell_text(str(PROJECT_ROOT))}'",
                "$shell = New-Object -ComObject WScript.Shell",
                "$shortcut = $shell.CreateShortcut($shortcutPath)",
                "$shortcut.TargetPath = $env:ComSpec",
                "$shortcut.Arguments = '/c \"\"' + $targetPath + '\"\" --startup'",
                "$shortcut.WorkingDirectory = $workingDirectory",
                "$shortcut.IconLocation = $env:ComSpec + ',0'",
                "$shortcut.Save()",
            ]
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], check=True, capture_output=True)

    def toggle_startup(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        try:
            if STARTUP_SHORTCUT.exists():
                STARTUP_SHORTCUT.unlink()
                message = "Windows 시작 시 자동 실행을 해제했습니다."
            else:
                self.set_startup_shortcut()
                message = "Windows 로그인 시 트레이 앱이 자동 실행됩니다."
            self.notify("F1 XR Server", message)
        except (OSError, subprocess.CalledProcessError):
            self.notify("F1 XR Server", "시작프로그램 설정을 변경하지 못했습니다.")
        self.icon.update_menu()

    def open_dashboard(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        if self.dashboard_process and self.dashboard_process.poll() is None:
            return

        python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        executable = str(python) if python.exists() else sys.executable
        self.dashboard_process = subprocess.Popen(
            [executable, "dashboard_tk.py"],
            cwd=PROJECT_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def close_dashboard(self) -> None:
        if not self.dashboard_process or self.dashboard_process.poll() is not None:
            return

        subprocess.run(
            ["taskkill", "/PID", str(self.dashboard_process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        self.dashboard_process = None

    def quit_app(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        if self.process and self.process.poll() is None:
            self.stop_server(_, __)

        # Hide the icon immediately. Icon.stop() normally ends the Windows
        # message loop, but a fallback is needed when that message is ignored.
        self.monitor_stop.set()
        self.icon.visible = False
        threading.Thread(target=self.force_exit_if_needed, daemon=True).start()
        self.icon.stop()

    @staticmethod
    def force_exit_if_needed() -> None:
        threading.Event().wait(1.0)
        os._exit(0)

    def run(self) -> None:
        self.icon.run(setup=self.start_status_monitor)


if __name__ == "__main__":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not mutex or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if mutex:
            kernel32.CloseHandle(mutex)
        ctypes.WinDLL("user32").MessageBoxW(
            None,
            "F1 XR Server 트레이 앱이 이미 실행 중입니다.\n작업 표시줄 오른쪽의 F1 아이콘을 확인하세요.",
            "F1 XR Server",
            0x40,
        )
        sys.exit(0)

    TrayServer().run()
    kernel32.CloseHandle(mutex)
