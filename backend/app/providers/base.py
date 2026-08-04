"""STT/번역/민감어 판단을 수행하는 ModelProvider 추상 인터페이스와 프로바이더 선택 로직."""

import os
from abc import ABC, abstractmethod
from typing import List, Optional


class ProviderNotConfiguredError(RuntimeError):
    pass


class ModelProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: str) -> List[dict]:
        """한국어 오디오 파일을 텍스트 세그먼트로 변환한다.

        반환값은 [{"start": float, "end": float, "text": str}, ...] 형태이며
        오디오 시작 시점 기준 초 단위 타임코드를 사용한다. 이 호출이
        파이프라인에서 오디오가 LLM/STT 엔진에 들어가는 유일한 지점이다."""
        ...

    @abstractmethod
    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        """정렬된 한국어-대상언어 세그먼트 쌍 전체를 읽고 인물을 식별한다.

        profile["checks_enabled"]에 따라 gender_agreement/register_consistency
        중 활성화된 항목만 태깅한다. 반환값:
        {"characters": [{"label": str, "gendered_segment_ids": [str]}],
         "relationships": [{"speaker_label": str, "addressee_label": str,
                            "formality_segment_ids": [str]}]}"""
        ...

    @abstractmethod
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               characters: List[dict], relationships: List[dict],
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        """Claude 1차: 사전에 없는 애매한 비속어/글로서리 표기/성별 일치/
        존댓말·반말 일관성을 직접 고쳐 다시 쓴다. 변경이 필요한 세그먼트만
        반환한다. 반환값은 [{"segment_id": str,
        "category": "sensitivity"|"glossary"|"gender"|"register",
        "corrected_text": str, "description": str(한국어)}, ...]"""
        ...

    @abstractmethod
    async def verify_and_refine(self, pairs: List[dict], original_target_by_id: dict,
                                 profile: dict, knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        """GPT 2차: 원문(korean_text)과 1차 결과(current_text)를 대조해
        번역정확성·문화맥락·뉘앙스어조·화법·자연스러운흐름(직역투)·함축의미 6개
        기준으로 검증하고, 로컬라이제이션(#8)을 처음 판단한다. 변경이 필요한
        세그먼트만 반환한다. 반환값은 [{"segment_id": str,
        "category": "translation"|"localization",
        "corrected_text": str, "description": str(한국어)}, ...]"""
        ...

    @abstractmethod
    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        """최종 안전망: 글자수 위반 한 줄만 의미를 보존하며 제약 안으로 줄인다."""
        ...


def get_provider() -> ModelProvider:
    name = os.getenv("QC_PROVIDER", "live")
    if name == "mock":
        if "PYTEST_CURRENT_TEST" not in os.environ:
            raise ProviderNotConfiguredError("mock 프로바이더는 자동화 테스트 전용입니다.")
        from app.providers.mock import MockProvider
        return MockProvider()
    if name == "live":
        from app.providers.live import LiveModelProvider
        required = {
            "ANTHROPIC_API_KEY": None, "CLAUDE_MODEL": None,
            "OPENAI_API_KEY": None, "GPT_MODEL": None,
        }
        for key in required:
            value = os.getenv(key)
            if not value:
                raise ProviderNotConfiguredError(f"{key} 환경변수가 설정되지 않았습니다.")
            required[key] = value
        return LiveModelProvider(
            claude_api_key=required["ANTHROPIC_API_KEY"], claude_model=required["CLAUDE_MODEL"],
            gpt_api_key=required["OPENAI_API_KEY"], gpt_model=required["GPT_MODEL"],
            gpt_transcribe_model=os.getenv("GPT_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        )
    raise ProviderNotConfiguredError(
        f"알 수 없거나 아직 구현되지 않은 프로바이더: {name}. "
        "실제 STT/LLM 프로바이더는 이 계획의 범위 밖에서 구현합니다."
    )
