#!/usr/bin/env bash
#폰(Termux) 서버 실행 — 실행: bash start.sh
set -e

#화면이 꺼지면 안드로이드가 프로세스를 멈춘다 — wake-lock 으로 변환 중 중단 방지
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
  trap 'termux-wake-unlock 2>/dev/null || true' EXIT
else
  echo "⚠ termux-wake-lock 없음 — 화면이 꺼지면 변환이 멈출 수 있습니다."
  echo "  'pkg install termux-api' 후 다시 실행하세요. (Mac 에서는 무시)"
fi

#폰 공유 저장소에 바로 저장 — 변환이 끝나면 음악·파일 앱에서 즉시 보인다
export YTAUDIO_DOWNLOAD_DIR="${YTAUDIO_DOWNLOAD_DIR:-$HOME/storage/downloads/ytaudio}"

cd "$(dirname "$0")"
#exec 을 쓰면 셸이 python 으로 바뀌어 EXIT trap 이 사라진다 — wake-lock 이 안 풀려 배터리를 먹는다
python server.py
