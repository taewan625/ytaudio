#!/usr/bin/env bash
#폰(Termux) 서버 실행 — 실행: bash start.sh
set -e

#화면이 꺼지면 안드로이드가 프로세스를 멈춘다 — wake-lock 으로 변환 중 중단 방지
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
  trap 'termux-wake-unlock 2>/dev/null || true' EXIT
fi

#폰 공유 저장소에 바로 저장 — 변환이 끝나면 음악·파일 앱에서 즉시 보인다
export YTAUDIO_DOWNLOAD_DIR="${YTAUDIO_DOWNLOAD_DIR:-$HOME/storage/downloads/ytaudio}"

cd "$(dirname "$0")"
exec python server.py
