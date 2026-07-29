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
    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        """한국어-대상언어 텍스트 쌍을 대조해 오역/번역투/로컬라이제이션 문제를
        찾는다. format_constraint(예: "줄당 50자 이내, 2줄 이내")를 프롬프트에
        포함해 제안 문장이 애초에 포맷 규칙을 지키도록 유도한다. 반환값은
        [{"segment_id": str, "category": "translation"|"localization",
          "description": str(한국어), "suggested_text": str(대상언어),
          "confidence": float}, ...]"""
        ...

    @abstractmethod
    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        """사전 필터(term_hits)로 1차 검출된 민감어 후보를 문맥과 함께 정밀
        판단한다. 반환값은 [{"segment_id": str, "description": str(한국어),
        "severity": "high"|"medium"|"low"}, ...]"""
        ...


def get_provider() -> ModelProvider:
    name = os.getenv("QC_PROVIDER", "gemini")
    if name == "mock":
        if "PYTEST_CURRENT_TEST" not in os.environ:
            raise ProviderNotConfiguredError("mock 프로바이더는 자동화 테스트 전용입니다.")
        from app.providers.mock import MockProvider
        return MockProvider()
    raise ProviderNotConfiguredError(
        f"알 수 없거나 아직 구현되지 않은 프로바이더: {name}. "
        "실제 STT/LLM 프로바이더는 이 계획의 범위 밖에서 구현합니다."
    )
