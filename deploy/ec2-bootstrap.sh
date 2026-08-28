#!/usr/bin/env bash
# EC2 인스턴스(Ubuntu 22.04)에서 최초 1회만 실행한다. 로컬 개발 머신에서
# 돌리면 안 된다 — 스왑 파일 생성과 Docker 설치가 시스템 전역에 영향을 준다.
set -euo pipefail

# 스왑 4GB — t3.micro는 RAM 1GB뿐인데, grammar_necessity.py가 쓰는
# fr_core_news_lg(spaCy 프랑스어 모델)만 디스크 612MB로 다른 언어
# 모델(15MB 안팎)보다 압도적으로 커서, 프랑스어 작품 처리 중 Postgres +
# FastAPI 기본 오버헤드와 합쳐 1GB를 넘길 수 있다(OOM 위험). 스왑은 이
# 스파이크를 느리게라도 버티게 하는 안전망이다(design
# 2026-08-28-aws-deployment-design.md).
if ! swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo "스왑 4GB 생성 완료"
else
  echo "스왑 이미 있음, 건너뜀"
fi

# Docker + Compose plugin
if ! command -v docker &> /dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker 설치 완료 — 재로그인해야 docker 그룹 권한이 적용됩니다"
else
  echo "Docker 이미 있음, 건너뜀"
fi
