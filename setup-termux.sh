#!/usr/bin/env bash
#폰(Termux) 1회 설치 — 실행: bash setup-termux.sh
set -e

echo "== 기존 패키지 업그레이드 =="
#update(목록 갱신)만으론 부족하다 — 새로 설치한 Termux 의 기본 패키지가 저장소보다 낮으면
#ffmpeg 가 끌고 오는 libplacebo 등이 최신 C++ 런타임을 요구해 'cannot locate symbol' 로 깨진다
pkg upgrade -y

echo "== 패키지 설치 (python, ffmpeg, termux-api) =="
#termux-api 에 termux-wake-lock 이 들어 있다 — 없으면 화면이 꺼질 때 변환이 멈춘다
pkg install -y python ffmpeg termux-api

#설치 직후 실행까지 확인한다 — dpkg 는 성공해도 동적 링크가 깨져 있을 수 있다
if ! ffmpeg -version >/dev/null 2>&1; then
  echo
  echo "⚠ ffmpeg 가 설치됐지만 실행되지 않습니다(동적 링크 오류). 아래를 실행하고 다시 시도하세요:"
  echo "    pkg upgrade -y && apt --fix-broken install -y"
  exit 1
fi

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
