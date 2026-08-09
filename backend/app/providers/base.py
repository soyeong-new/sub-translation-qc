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
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        """Claude 검증 패스: 원본(korean_text/target_text)을 처음부터 독립적으로
        검토해 사전에 없는 애매한 비속어, 번역정확성·문화맥락·뉘앙스어조·
        자연스러운흐름(직역투)·함축의미·로컬라이제이션 문제를 찾아 고친다.
        GPT 검증 패스(verify_and_refine)와 동시에 같은 원본을 받아 서로
        독립적으로 판단한다 — 어느 쪽도 상대가 뭘 했는지 모른다(파이프라인이
        둘의 일치/불일치를 나중에 병합해 신뢰도 신호로 쓴다). 글로서리 표기
        통일(새 인물 이름)은 긴 컨텍스트에서 신뢰도가 낮아 여기서 다루지 않는다
        — glossary.yaml에 직접 등록하는 방식으로 대체한다. 성별/격식은 화자를
        특정할 근거가 없어 여기서 다루지 않는다 — check_grammar_necessity로
        걸러 사람이 직접 확인한다. 변경이 필요한 세그먼트만 반환한다. 반환값은
        [{"segment_id": str,
        "category": "sensitivity"|"mistranslation"|"nuance_tone"|"unnatural_style"|"locale_convention",
        "corrected_text": str, "description": str(한국어)}, ...]"""
        ...

    @abstractmethod
    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        """GPT 검증 패스: correct_primary와 대칭적으로, 같은 원본을 처음부터
        독립적으로 검토한다. Claude가 뭘 고쳤는지/안 고쳤는지 알려주지 않는다
        — "이전 교정을 검토"하는 프레이밍은 앵커링 편향(모델이 제시된 답을
        독립적으로 재도출하기보다 그냥 승인하는 쪽으로 기우는 현상)을 유발해
        정확도를 낮춘다. 변경이 필요한 세그먼트만 반환한다. 반환값은
        correct_primary와 동일한 형태."""
        ...

    @abstractmethod
    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        """최종 안전망: 글자수 위반 한 줄만 의미를 보존하며 제약 안으로 줄인다."""
        ...

    @abstractmethod
    async def back_translate_with_claude(self, texts: List[dict], profile: dict) -> List[dict]:
        """Claude로 대상언어 텍스트를 한국어로 역번역한다(감사/참고용). GPT가
        만든 텍스트만 여기로 들어온다 — 자기가 만든 텍스트를 자기가 역번역하면
        스스로의 오류를 매끄럽게 얼버무려 가릴 위험이 있어(같은 모델의 왕복
        번역은 오류를 숨기는 경향), 항상 반대쪽 모델이 역번역한다. 입력은
        [{"id": str, "text": str}], 반환값은 [{"id": str, "korean_text": str}]."""
        ...

    @abstractmethod
    async def back_translate_with_gpt(self, texts: List[dict], profile: dict) -> List[dict]:
        """back_translate_with_claude와 대칭. Claude가 만든 텍스트만 여기로
        들어온다."""
        ...

    @abstractmethod
    async def check_equivalence_with_claude(self, items: List[dict], profile: dict) -> List[dict]:
        """같은 줄을 Claude/GPT 둘 다 지적했지만 문구가 다를 때, text_a/text_b가
        같은 문제를 같은 방식으로 고친 것인지 Claude에게 판정하게 한다. 문구
        일치가 아니라 의미 동등성만 본다(단어 선택이 달라도 같은 해결책이면
        true). GPT의 판정(check_equivalence_with_gpt)과 독립적으로 물어보고,
        파이프라인은 둘 다 true여야만 진짜 합의로 확정한다 — 병합 판단
        하나만 단일 모델에 맡기면 "합의"라는 신뢰 신호 자체가 다시 단일
        모델 신뢰 문제로 돌아가기 때문이다. 입력은
        [{"id","korean_text","text_a","text_b"}], 반환값은
        [{"id","equivalent": bool}]."""
        ...

    @abstractmethod
    async def check_equivalence_with_gpt(self, items: List[dict], profile: dict) -> List[dict]:
        """check_equivalence_with_claude와 대칭."""
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
