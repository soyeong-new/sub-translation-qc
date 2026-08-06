"""SQLAlchemy 비동기 DB 엔진/세션을 설정하는 모듈."""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# 이 모듈은 os.getenv를 읽는 가장 이른 시점(다른 app.* 모듈 대부분이 이 모듈을
# 거쳐가므로)이라, .env 로딩을 여기서 한 번만 해도 이후 모든 os.getenv 호출
# (app/providers/base.py의 API 키 등)에 값이 채워져 있다. cwd에 의존하지 않도록
# 이 파일 기준 상대경로(backend/.env)로 명시한다 — uvicorn을 어느 디렉터리에서
# 실행하든 항상 같은 파일을 찾는다. 이미 설정된 환경변수는 덮어쓰지 않으므로
# (dotenv 기본 동작), 테스트가 conftest.py에서 미리 지정한 DATABASE_URL 등은
# 그대로 유지된다.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/sub_translation_qc_es"
)
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)
