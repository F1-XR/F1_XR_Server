# F1 XR Server

## Windows system tray

Run `run_tray.bat` to keep F1 XR Server controls in the Windows notification area.
Right-click the F1 icon to start or stop the server, check its status, open the dashboard,
or turn the `Windows 시작 시 실행` option on or off. This option starts only the tray app
when you sign in; the server remains stopped until you choose `서버 시작`.

이 프로젝트는 OpenF1 데이터를 받아 로컬 JSON 청크 API로 제공하는 FastAPI 서버입니다.

## 대시보드 실행

처음 클론한 경우 먼저 가상환경을 만들고 패키지를 설치합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Windows에서 `run_dashboard.bat`를 더블클릭하면 Tkinter 대시보드가 활성화됩니다.

대시보드에서 할 수 있는 일:

- 서버 시작
- 서버 종료
- 서버 상태 확인
- 서버 로그 확인

직접 실행하려면 다음 명령을 사용할 수 있습니다.

```powershell
.\run_dashboard.bat
```
