"""영상 음원 추출 로컬 서버 — 웹 UI + 작업 API 한 프로세스.

구조: POST /api/jobs 로 작업 생성 → 스레드에서 yt-dlp 가 받고 ffmpeg 로 mp3 변환
    → GET /api/jobs/{id} 폴링으로 진행률 → GET /api/jobs/{id}/file 로 내려받기.
작업 상태를 서버가 들고 있어서 브라우저를 닫아도 변환은 계속된다(PWA 전환 대비).

의존성을 yt-dlp(순수 Python) 하나로 묶기 위해 웹 계층은 표준 라이브러리만 쓴다.
FastAPI 는 pydantic 이 Rust 컴파일을 요구해서 Termux(안드로이드)에서 설치가 깨진다.
"""

import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse, urlsplit

from yt_dlp import YoutubeDL

STATIC_DIR = Path(__file__).parent / "static"
DOWNLOAD_DIR = Path(os.environ.get("YTAUDIO_DOWNLOAD_DIR", Path.home() / "Downloads" / "ytaudio"))
AUDIO_BITRATE = os.environ.get("YTAUDIO_BITRATE", "192")
#인증이 없는 API 라 루프백 고정 — LAN 에 열 수단을 남기지 않는다
HOST = "127.0.0.1"
PORT = int(os.environ.get("YTAUDIO_PORT", "8777"))
TMP_DIR = DOWNLOAD_DIR / ".tmp"

#파일명에 못 쓰는 문자 — 경로 구분자를 지워서 DOWNLOAD_DIR 밖으로 못 나가게 한다
INVALID_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
#안드로이드 공유 저장소가 파일명 255바이트 제한이라 문자 수가 아니라 UTF-8 바이트로 자른다
#('.mp3'·' (12)' 가 뒤에 붙을 자리를 남겨 200)
MAX_FILENAME_BYTES = 200

JOB_ID_RE = r"([0-9a-f]{12})"
JOB_PATH = re.compile(rf"^/api/jobs/{JOB_ID_RE}$")
JOB_FILE_PATH = re.compile(rf"^/api/jobs/{JOB_ID_RE}/file$")

#루프백으로 들어온 요청만 받는다 — Host 가 다르면 DNS 리바인딩, Origin 이 다르면 외부 사이트의 CSRF
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

#작업 저장소 — 개인용 단일 프로세스라 메모리 dict 로 충분(재시작 시 목록은 사라지고 파일만 남음)
jobs: dict[str, dict] = {}

#폰에서 yt-dlp+ffmpeg 를 여러 개 동시에 돌리면 메모리가 터진다 — 2개만 돌리고 나머지는 queued 로 대기
job_slots = threading.Semaphore(2)


def _safe_stem(title: str) -> str:
    """사용자가 입력한 제목 → 안전한 파일명(확장자 제외)."""
    #선행·후행 점 제거로 '..'·숨김파일 차단
    cleaned = INVALID_FILENAME_CHARS.sub("", title).strip().strip(".")
    #멀티바이트 문자가 경계에서 반쪽만 남으면 버린다. 절단 후 다시 점을 떼야 '..mp3' 가 안 생긴다
    return cleaned.encode()[:MAX_FILENAME_BYTES].decode(errors="ignore").strip().strip(".")


def _unique_path(directory: Path, stem: str) -> Path:
    """같은 이름이 이미 있으면 '(2)' 부터 번호를 붙인다 — 기존 mp3 를 덮어쓰지 않게."""
    target = directory / f"{stem}.mp3"
    seq = 2
    while target.exists():
        target = directory / f"{stem} ({seq}).mp3"
        seq += 1
    return target


def _run_job(job_id: str, url: str) -> None:
    job = jobs[job_id]

    #슬롯을 못 잡으면 여기서 멈춰 대기 — status 는 queued 로 남아 UI 의 '대기 중'이 실제 대기를 뜻한다
    with job_slots:
        workdir = TMP_DIR / job_id
        workdir.mkdir(parents=True, exist_ok=True)

        def hook(d: dict) -> None:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    job["progress"] = round(d.get("downloaded_bytes", 0) / total * 100, 1)
                job["status"] = "downloading"
            elif d["status"] == "finished":
                #다운로드 끝 = ffmpeg 변환 시작 지점. 변환은 진행률이 안 나와서 단계만 바꾼다
                job["progress"] = 100.0
                job["status"] = "converting"

        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(workdir / "%(title)s.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": AUDIO_BITRATE}
            ],
            "progress_hooks": [hook],
            #noplaylist 는 '영상+재생목록 혼합' URL 에만 듣는다 — 순수 재생목록·채널 URL 은
            #playlist_items 로 막아야 전체를 받아놓고 1개만 옮기고 나머지를 지우는 일이 없다
            "noplaylist": True,
            "playlist_items": "1",
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
        }

        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            #재생목록 URL 이면 제목이 목록 이름이라 실제로 받은 첫 항목에서 가져온다(파일명과 어긋나지 않게)
            if info.get("_type") == "playlist":
                info = next(iter(info.get("entries") or []), None) or {}
            job["title"] = info.get("title") or url

            #workdir 는 작업당 하나라 비어 있었다 — 변환 결과 mp3 가 유일한 산출물
            produced = next(iter(workdir.glob("*.mp3")), None)
            if produced is None:
                raise RuntimeError("mp3 변환 결과를 찾지 못했습니다")

            final = _unique_path(DOWNLOAD_DIR, produced.stem)
            shutil.move(str(produced), str(final))
            job["filename"] = final.name
            job["size"] = final.stat().st_size
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 — 실패 사유를 UI 에 그대로 보여준다
            #error 를 먼저 채운다 — status 가 먼저 바뀌면 폴링 한 틱 동안 사유 없는 '실패'가 보인다
            job["error"] = str(e)
            job["status"] = "error"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def create_job(url: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "url": url,
        "status": "queued",
        "progress": 0.0,
        "title": None,
        "filename": None,
        "size": None,
        "error": None,
    }
    threading.Thread(target=_run_job, args=(job_id, url), daemon=True).start()
    return jobs[job_id]


def rename_job(job: dict, title: str) -> dict:
    """제목 수정 — 디스크의 mp3 파일명까지 같이 바꾼다(제목과 파일명이 어긋나지 않게)."""
    stem = _safe_stem(title)
    if not stem:
        raise ValueError("제목을 입력해 주세요.")

    current = DOWNLOAD_DIR / job["filename"]
    if not current.exists():
        raise FileNotFoundError("파일이 삭제되었습니다.")
    if current.stem == stem:
        #이름이 그대로면 아무것도 안 한다 — 자기 파일을 피해 '(2)' 가 붙는 것 방지
        return job

    target = _unique_path(DOWNLOAD_DIR, stem)
    current.rename(target)
    job["filename"] = target.name
    job["title"] = target.stem
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "ytaudio"
    #Python 버전을 Server 헤더에 흘리지 않는다
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        #1초 폴링이 로그를 덮어버리므로 상태 조회만 조용히 넘긴다
        if "/api/jobs" in self.path and self.command == "GET":
            return
        super().log_message(fmt, *args)

    # ── 응답 헬퍼 ─────────────────────────────────────
    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, detail: str) -> None:
        self._send_json(status, {"detail": detail})

    def _read_json(self) -> dict | None:
        """본문 파싱 — 깨진 JSON·dict 아닌 바디는 None. 예외로 터지면 응답 없이 연결이 끊긴다."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, UnicodeDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def _is_local_request(self) -> bool:
        """로컬 브라우저에서 온 요청인지 — 인증 없는 API 라 서버가 직접 출처를 봐야 한다.

        text/plain 본문은 CORS 단순요청이라 프리플라이트 없이 통과하므로, 사용자가 방문한
        아무 사이트가 이 도구에 작업을 시킬 수 있다. Host 로 DNS 리바인딩까지 같이 막는다.
        """
        host = self.headers.get("Host", "")
        try:
            hostname = urlsplit(f"//{host}").hostname
        except ValueError:
            return False
        if hostname not in LOCAL_HOSTS:
            return False
        #Origin 이 붙어 있으면 같은 출처만 허용(없는 요청 = 브라우저 밖 curl 등은 통과)
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    # ── 라우팅 ────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 규약
        path = urlparse(self.path).path

        if path == "/api/jobs":
            return self._send_json(200, list(reversed(jobs.values())))

        if m := JOB_PATH.match(path):
            job = jobs.get(m.group(1))
            return self._send_json(200, job) if job else self._send_error_json(404, "작업을 찾을 수 없습니다.")

        if m := JOB_FILE_PATH.match(path):
            return self._send_file(jobs.get(m.group(1)))

        return self._send_static(path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._is_local_request():
            return self._send_error_json(403, "로컬에서만 사용할 수 있습니다.")
        if urlparse(self.path).path != "/api/jobs":
            return self._send_error_json(404, "없는 경로입니다.")

        payload = self._read_json()
        if payload is None:
            return self._send_error_json(400, "요청 본문을 확인해 주세요.")

        url = str(payload.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return self._send_error_json(400, "URL 을 확인해 주세요.")
        return self._send_json(200, create_job(url))

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._is_local_request():
            return self._send_error_json(403, "로컬에서만 사용할 수 있습니다.")

        m = JOB_PATH.match(urlparse(self.path).path)
        if not m:
            return self._send_error_json(404, "없는 경로입니다.")

        job = jobs.get(m.group(1))
        if job is None:
            return self._send_error_json(404, "작업을 찾을 수 없습니다.")
        if job["status"] != "done":
            return self._send_error_json(409, "완료된 작업만 제목을 바꿀 수 있습니다.")

        payload = self._read_json()
        if payload is None:
            return self._send_error_json(400, "요청 본문을 확인해 주세요.")

        try:
            return self._send_json(200, rename_job(job, str(payload.get("title", ""))))
        except ValueError as e:
            return self._send_error_json(400, str(e))
        except FileNotFoundError as e:
            return self._send_error_json(410, str(e))
        except OSError as e:
            #권한·파일시스템 오류 — 그냥 두면 응답 없이 연결이 끊긴다
            return self._send_error_json(500, f"파일명을 바꾸지 못했습니다: {e}")

    # ── 파일·정적 응답 ─────────────────────────────────
    def _send_file(self, job: dict | None) -> None:
        if job is None or job["status"] != "done":
            return self._send_error_json(404, "아직 준비되지 않았습니다.")

        path = DOWNLOAD_DIR / job["filename"]
        if not path.exists():
            return self._send_error_json(410, "파일이 삭제되었습니다.")

        #한글 파일명은 RFC 5987 형식으로 — 그냥 넣으면 헤더 인코딩에서 깨진다
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        try:
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            #브라우저가 다운로드를 중단한 정상 상황 — traceback 을 남기지 않는다
            return

    def _send_static(self, path: str) -> None:
        target = STATIC_DIR / "index.html" if path == "/" else STATIC_DIR / path.lstrip("/")

        #resolve 후 STATIC_DIR 하위인지 확인 — '..' 로 다른 파일을 읽어가지 못하게
        try:
            resolved = target.resolve()
            resolved.relative_to(STATIC_DIR.resolve())
        except (ValueError, OSError):
            return self._send_error_json(404, "없는 경로입니다.")

        if not resolved.is_file():
            return self._send_error_json(404, "없는 경로입니다.")

        mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        #charset 은 텍스트 계열에만 — png 같은 바이너리에 붙으면 의미 없는 헤더가 된다
        if mime.startswith("text/") or mime.endswith("+json") or mime == "application/javascript":
            mime = f"{mime}; charset=utf-8"
        body = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        #서비스워커·HTML 은 캐시하지 않아야 수정이 바로 반영된다
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    print(f"ytaudio → http://{HOST}:{PORT}  (저장: {DOWNLOAD_DIR})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
