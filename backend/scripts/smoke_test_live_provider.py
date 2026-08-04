"""실제 Claude/GPT API가 붙어있는지 수동으로 확인하는 스크립트.

pytest 스위트에는 포함되지 않는다. 실행 전 backend/.env에 실제 API 키를
채워 넣어야 한다.

사용법: cd backend && export $(grep -v '^#' .env | xargs) && venv/bin/python scripts/smoke_test_live_provider.py
"""

import asyncio
import os
from app.providers.live import LiveModelProvider


async def main():
    provider = LiveModelProvider(
        claude_api_key=os.environ["ANTHROPIC_API_KEY"], claude_model=os.environ["CLAUDE_MODEL"],
        gpt_api_key=os.environ["OPENAI_API_KEY"], gpt_model=os.environ["GPT_MODEL"],
        gpt_transcribe_model=os.environ.get("GPT_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
    )
    profile = {"checks_enabled": {"gender_agreement": True, "register_consistency": True}}
    format_constraint = "줄당 50자 이내, 세그먼트당 최대 2줄을 지켜서 제안할 것."

    print("--- analyze_characters (GPT) ---")
    characters_result = await provider.analyze_characters(
        [{"id": "p1", "target_text": "Hola, ¿cómo has estado?"}], profile,
    )
    print(characters_result)

    print("--- correct_primary (Claude 1차) ---")
    correct_primary_result = await provider.correct_primary(
        pairs=[{"id": "p1", "korean_text": "안녕하세요, 잘 지내셨어요?",
                "target_text": "Hola, ¿cómo has estado?"}],
        profile=profile,
        characters=characters_result.get("characters", []),
        relationships=characters_result.get("relationships", []),
        pending_sensitive_hits=[], knowledge="", format_constraint=format_constraint,
    )
    print(correct_primary_result)

    print("--- verify_and_refine (GPT 2차) ---")
    verify_result = await provider.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "안녕하세요, 잘 지내셨어요?",
                "current_text": "Hola, ¿cómo has estado?"}],
        original_target_by_id={"p1": "Hola, ¿cómo has estado?"},
        profile=profile, knowledge="", format_constraint=format_constraint,
    )
    print(verify_result)

    print("--- shrink_line (Claude 안전망) ---")
    shrink_result = await provider.shrink_line(
        "Esta es una línea de subtítulo demasiado larga que definitivamente supera el límite",
        max_chars=50, max_lines=2,
    )
    print(shrink_result)


if __name__ == "__main__":
    asyncio.run(main())
