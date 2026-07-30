"""실제 Gemini/Claude/GPT API가 붙어있는지 수동으로 확인하는 스크립트.

pytest 스위트에는 포함되지 않는다. 실행 전 backend/.env에 실제 API 키를
채워 넣어야 한다.

사용법: cd backend && export $(grep -v '^#' .env | xargs) && venv/bin/python scripts/smoke_test_live_provider.py
"""

import asyncio
import os
from app.providers.live import LiveModelProvider


async def main():
    provider = LiveModelProvider(
        gemini_api_key=os.environ["GEMINI_API_KEY"], gemini_model=os.environ["GEMINI_MODEL"],
        claude_api_key=os.environ["ANTHROPIC_API_KEY"], claude_model=os.environ["CLAUDE_MODEL"],
        gpt_api_key=os.environ["OPENAI_API_KEY"], gpt_model=os.environ["GPT_MODEL"],
    )
    pairs = [{"id": "p1", "korean_text": "안녕하세요", "target_text": "Hola"}]
    result = await provider.review_translation(pairs, "", {}, "줄당 50자 이내")
    print("review_translation 결과:", result)


if __name__ == "__main__":
    asyncio.run(main())
