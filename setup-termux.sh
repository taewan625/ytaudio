#!/usr/bin/env bash
#폰(Termux) 1회 설치 — 실행: bash setup-termux.sh
set -e

echo "== 패키지 설치 (python, ffmpeg, termux-api) =="
pkg update -y
#termux-api 에 termux-wake-lock 이 들어 있다 — 없으면 화면이 꺼질 때 변환이 멈춘다
pkg install -y python ffmpeg termux-api

echo "== yt-dlp 설치 =="
#yt-dlp 는 순수 Python 이라 컴파일 없이 설치된다(웹 계층은 표준 라이브러리만 씀)
pip install --upgrade pip
pip install -U yt-dlp

STORAGE="$HOME/storage/downloads"
if [ ! -d "$STORAGE" ]; then
  echo
  echo "⚠ 저장소 권한이 아직 없습니다. 아래를 먼저 실행하고 팝업에서 '허용'을 누르세요:"
  echo "    termux-setup-storage"
  echo "  그 다음 이 스크립트를 다시 실행하세요."
  exit 1
fi

mkdir -p "$STORAGE/ytaudio"
echo
echo "✅ 설치 완료. 실행: bash start.sh"
echo "   저장 위치: $STORAGE/ytaudio  (폰 '파일'·음악 앱에서 바로 보임)"
