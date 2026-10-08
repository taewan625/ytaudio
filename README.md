# ytaudio

영상 링크에서 음원(m4a)을 추출하는 개인용 도구. 웹 UI + 작업 API 한 프로세스.

구성은 두 파일뿐입니다.

| 파일 | 역할 |
|---|---|
| `server.py` | API 4개 + 정적 UI 서빙. 웹 계층은 Python 표준 라이브러리만 사용 |
| `static/index.html` | 링크 입력·작업 목록·진행바·제목 수정. PWA(manifest·service worker) 포함 |

의존성은 **yt-dlp(순수 Python)와 ffmpeg(바이너리) 둘**입니다. FastAPI를 쓰지 않는 이유는 pydantic이 Rust 컴파일을 요구해 Termux(안드로이드) 설치가 깨지기 때문입니다.

## API

| 메서드 | 경로 | 동작 |
|---|---|---|
| POST | `/api/jobs` | `{"url": "..."}` → 작업 생성, 스레드에서 추출·변환 시작 |
| GET | `/api/jobs` | 작업 목록(최신순) |
| GET | `/api/jobs/{id}` | 단건 상태 — 진행률 폴링용 |
| PATCH | `/api/jobs/{id}` | `{"title": "..."}` → 제목 + 실제 음원 파일명 변경 (완료 상태만) |
| GET | `/api/jobs/{id}/file` | 음원 내려받기 |

상태 흐름: `queued → downloading → converting → done` (실패 시 `error`).
작업 상태를 서버가 들고 있어서 브라우저를 닫거나 새로고침해도 변환은 계속됩니다.

- **재생목록·채널 URL은 첫 항목만** 받습니다 (영상 1개 = 작업 1개).
- 동시 변환은 2개까지, 나머지는 `queued`로 대기합니다 (폰 메모리 보호).
- 인증이 없는 API라 `127.0.0.1`에만 바인드하고, Host·Origin이 로컬이 아닌 요청은 403입니다.

## 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `YTAUDIO_DOWNLOAD_DIR` | `~/Downloads/ytaudio` | 음원 저장 경로 |
| `YTAUDIO_PORT` | `8777` | 포트 |

## 음질 — 재인코딩하지 않습니다

원본 오디오 스트림을 **컨테이너만 바꿔 담습니다**(`-acodec copy` 상당). 손실 압축을 풀었다 다시 압축하는 과정이 없어 소스 그대로의 음질이고, 변환이 빨라 배터리도 덜 씁니다.

- 포맷은 **m4a(AAC) 우선** — opus 는 Samsung Music 등 일부 음악 앱이 목록에 띄우지 않습니다
- `FFmpegMetadata` 로 제목·아티스트 태그를 심습니다. 없으면 음악 앱에 곡명 대신 파일명이 뜹니다
- 저장·이름변경 후 `termux-media-scan` 을 호출합니다. 안드로이드는 미디어 DB 에 등록돼야 음악 앱 목록에 나타납니다 (Mac 에서는 자동으로 건너뜀)

검증: 소스 `aac 69578bps` → 산출 `aac 69578bps` (동일 비트레이트 = 무변환).

## 실행 — Mac

```bash
pip install -U yt-dlp          # ffmpeg 는 brew install ffmpeg
python server.py               # → http://127.0.0.1:8777
```

## 실행 — 폰 (Termux)

폰이 곧 서버입니다. 별도 서버·클라우드가 필요 없고, 데이터센터 IP로 접속하지 않아 차단 이슈도 없습니다.

1. **F-Droid**에서 Termux 설치 (Play 스토어판은 방치되어 패키지 설치가 깨집니다)
2. `termux-setup-storage` → 권한 팝업에서 허용
3. `bash setup-termux.sh` → python·ffmpeg·termux-api·yt-dlp 설치
4. `bash start.sh` → 서버 실행
5. Chrome에서 `http://localhost:8777` → 메뉴 → **홈 화면에 추가** (PWA 설치)

`localhost`는 Chrome이 보안 출처로 취급하므로 HTTPS 없이도 PWA가 설치됩니다.
음원은 폰의 `Download/ytaudio/`에 저장돼 음악·파일 앱에서 바로 보입니다.

### 폰에서 알아둘 것

- **Termux가 떠 있어야** API가 동작합니다. 종료하면 PWA는 열리지만 작업 생성이 실패합니다
- `start.sh`가 `termux-wake-lock`을 걸어 화면이 꺼져도 변환이 계속됩니다
- 배터리 최적화 예외에 Termux를 넣어두면 백그라운드 종료가 줄어듭니다
- 부팅 시 자동 실행은 **Termux:Boot** 애드온이 필요합니다
- yt-dlp는 플랫폼 변경을 따라가는 도구라 주기적으로 `pip install -U yt-dlp`가 필요합니다

## 범위

개인이 권리를 가진 영상, CC·퍼블릭도메인, 다운로드가 허용된 강의·팟캐스트용입니다.
