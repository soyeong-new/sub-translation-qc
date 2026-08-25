FROM python:3.14-slim

WORKDIR /app

# ffmpeg: ingest.py가 subprocess로 직접 호출한다(오디오 추출/영상 프록시 생성/
# 오디오 청크 분할) — slim 이미지엔 기본으로 없어서 없으면 업로드 즉시 실패한다.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# alembic upgrade head를 먼저 실행해 스키마를 최신으로 맞춘 뒤에만 서버를
# 띄운다 — 안 그러면 첫 배포 시 테이블이 아예 없어서 모든 요청이 실패한다.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
