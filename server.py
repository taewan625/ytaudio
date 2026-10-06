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
from urllib.parse import quote, urlparse

from yt_dlp import YoutubeDL

STATIC_DIR = Path(__file__).parent / "static"
DOWNLOAD_DIR = Path(os.environ.get("YTAUDIO_DOWNLOAD_DIR", Path.home() / "Downloads" / "ytaudio"))
AUDIO_BITRATE = os.environ.get("YTAUDIO_BITRATE", "192")
HOST = os.environ.get("YTAUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("YTAUDIO_PORT", "8777"))
TMP_DIR = DOWNLOAD_DIR / ".tmp"

#파일명에 못 쓰는 문자 — 경로 구분자를 지워서 DOWNLOAD_DIR 밖으로 못 나가게 한다
INVALID_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
MAX_FILENAME_LEN = 120

JOB_ID_RE = r"([0-9a-f]{12})"
JOB_PATH = re.compile(rf"^/api/jobs/{JOB_ID_RE}$")
JOB_FILE_PATH = re.compile(rf"^/api/jobs/{JOB_ID_RE}/file$")

#작업 저장소 — 개인용 단일 프로세스라 메모리 dict 로 충분(재시작 시 목록은 사라지고 파일만 남음)
jobs: dict[str, dict] = {}


def _safe_stem(title: str) -> str:
    """사용자가 입력한 제목 → 안전한 파일명(확장자 제외)."""
    #선행·후행 점 제거로 '..'·숨김파일 차단
    return INVALID_FILENAME_CHARS.sub("", title).strip().strip(".")[:MAX_FILENAME_LEN].strip()


def _run_job(job_id: str, url: str) -> None:
    job = jobs[job_id]
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
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        job["title"] = info.get("title") or url

        #workdir 는 작업당 하나라 비어 있었다 — 변환 결과 mp3 가 유일한 산출물
        produced = next(iter(workdir.glob("*.mp3")), None)
        if produced is None:
            raise RuntimeError("mp3 변환 결과를 찾지 못했습니다")

        final = DOWNLOAD_DIR / produced.name
        shutil.move(str(produced), str(final))
        job["filename"] = final.name
        job["size"] = final.stat().st_size
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 — 실패 사유를 UI 에 그대로 보여준다
        job["status"] = "error"
        job["error"] = str(e)
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

    #같은 이름이 이미 있으면 번호를 붙인다 — 다른 작업의 파일을 덮어쓰지 않게
    target = DOWNLOAD_DIR / f"{stem}.mp3"
    seq = 2
    while target.exists() and target != current:
        target = DOWNLOAD_DIR / f"{stem} ({seq}).mp3"
        seq += 1

    current.rename(target)
    job["filename"] = target.name
    job["title"] = target.stem
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "ytaudio"

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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

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
        if urlparse(self.path).path != "/api/jobs":
            return self._send_error_json(404, "없는 경로입니다.")

        url = str(self._read_json().get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return self._send_error_json(400, "URL 을 확인해 주세요.")
        return self._send_json(200, create_job(url))

    def do_PATCH(self) -> None:  # noqa: N802
        m = JOB_PATH.match(urlparse(self.path).path)
        if not m:
            return self._send_error_json(404, "없는 경로입니다.")

        job = jobs.get(m.group(1))
        if job is None:
            return self._send_error_json(404, "작업을 찾을 수 없습니다.")
        if job["status"] != "done":
            return self._send_error_json(409, "완료된 작업만 제목을 바꿀 수 있습니다.")

        try:
            return self._send_json(200, rename_job(job, str(self._read_json().get("title", ""))))
        except ValueError as e:
            return self._send_error_json(400, str(e))
        except FileNotFoundError as e:
            return self._send_error_json(410, str(e))

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
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

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
        body = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime if "charset" in mime else f"{mime}; charset=utf-8")
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
