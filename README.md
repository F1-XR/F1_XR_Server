# F1 XR Server

OpenF1 데이터를 받아 F1 XR 재생용 로컬 JSON 청크 API로 제공하는 FastAPI 서버입니다.

## 처음 설치

프로젝트 폴더에서 한 번만 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

기존 프로젝트를 업데이트한 경우에도 `requirements.txt`가 바뀌었다면 아래 명령을 한 번 실행하세요.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 트레이 앱으로 실행하기

Windows에서 `run_tray.bat`를 더블클릭합니다. F1 아이콘이 작업 표시줄 오른쪽의 숨겨진 아이콘 메뉴(`^`)에 나타납니다.

- 빨간 F1 로고: 서버 중지 상태
- 초록 F1 로고: 서버 실행 중
- F1 아이콘 왼쪽 클릭: 서버 시작 또는 종료 확인
- 서버 시작 확인에서 `예`를 누르면 대시보드가 함께 열림
- 서버 종료 시 자동으로 열린 대시보드도 함께 닫힘
- F1 아이콘 우클릭: 서버 제어, 대시보드 열기, 자동 실행 설정, 앱 종료 메뉴

`run_tray.bat`를 실행해도 서버는 자동 시작하지 않습니다. 트레이 아이콘에서 서버를 시작하세요.

## Windows 로그인 시 트레이 자동 실행

트레이 F1 아이콘을 우클릭한 뒤 `Windows 시작 시 실행`을 선택합니다. 이후 Windows에 로그인하면 트레이 앱이 자동으로 실행됩니다.

작업 표시줄이 준비된 뒤 실행되도록 로그인 후 잠시 기다린 다음 트레이 아이콘을 표시합니다. 자동 실행된 경우에도 서버는 중지 상태로 대기합니다.

## 대시보드만 실행하기

트레이를 사용하지 않고 대시보드만 열려면 다음 파일을 실행합니다.

```powershell
.\run_dashboard.bat
```

## 로그 확인

서버 로그는 `logs/server.log`에 기록됩니다. 트레이에서 서버를 시작해도 대시보드에서 이 로그를 실시간으로 확인할 수 있습니다.

- 서버 시작·종료 상태
- OpenF1 데이터 요청과 통신 오류
- 캐시 사용 여부
- API 요청 결과와 상태 코드

자동 상태 확인용 `/health` 요청은 로그에 표시되지 않습니다.

## 서버 상태 확인

서버 실행 중 아래 주소를 브라우저에서 열면 정상 동작 여부를 확인할 수 있습니다.

```text
http://127.0.0.1:8000/health
```

`{"status":"ok"}`가 보이면 정상입니다.
