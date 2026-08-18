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

    @abstractmethod
    async def gloss_words(self, items: List[dict], profile: dict) -> List[dict]:
        """성별/격식 확인 화면에 뜨는, 성별 표시가 걸린 대상언어 단어들의 뜻을
        한국어로 풀이한다 — 검수자가 대상언어를 몰라 "이 단어가 사람 얘기인지
        사물 얘기인지"조차 판단 못 하는 문제를 돕는다(예: "caro"가 사람이
        아니라 가격을 뜻한다는 걸 알아야 성별 확인이 필요 없다는 걸 판단할
        수 있음). 입력은 [{"id": str, "word": str, "context": str(그 단어가
        들어간 문장)}, ...], 반환값은 [{"id": str, "meaning": str(간결한
        한국어 뜻)}, ...]."""
        ...

    @abstractmethod
    async def apply_formality(self, items: List[dict], profile: dict) -> List[dict]:
        """확정된 격식(formal/informal)만 문장에 반영하는 전담 호출 — 오역/
        뉘앙스/직역투 등 다른 검증과 한 프롬프트에 섞으면 모델이 부차적
        지시(격식)를 놓치는 문제가 있었다(design §격식 지시가 무시됨). 오직
        2인칭 대명사·동사 활용만 바꾸고 다른 건 손대지 않는다 — 이 결과가
        이후 이중검증(S2)의 새 기준 텍스트가 된다. 입력은
        [{"id": str, "target_text": str, "formality": "formal"|"informal"}],
        반환값은 [{"id": str, "corrected_text": str}] — 이미 일치하면
        target_text 그대로 돌아온다."""
        ...

    @abstractmethod
    async def split_scenes(self, pairs: List[dict], profile: dict) -> List[dict]:
        """자막 전체(시간순 pairs)를 화제 전환·화자 구성 변화·시공간 이동·
        분위기 반전 기준으로 씬 단위로 나눈다. correct_primary/verify_and_refine
        에 pairs를 영화 전체 통째로 넘기면 응답이 토큰 한도에서 잘려 파싱이
        통째로 실패하거나, 항목이 많을수록 모델이 segment_id를 엉뚱한 줄에
        붙이는 오귀속이 늘어난다 — 씬 단위로 나눠 호출하면 두 문제 다
        줄어들고, 대화가 이어지는 도중에 끊기지도 않는다(순수 개수/시간
        기준 청킹과 달리 문맥 경계에서 자른다).

        입력 pairs는 [{"id","korean_text","target_text","start","end"}, ...].
        반환값은 [{"start_id","end_id","summary"}, ...] — 호출자가 pairs를
        처음부터 끝까지 순서대로 빠짐없이 겹치지 않게 커버하는지 검증하며,
        하나라도 어긋나면(파싱 실패 포함) 타임코드 공백 기준 청킹으로
        폴백한다."""
        ...

    @abstractmethod
    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """문장 리스트를 받아서 OpenAI text-embedding-3-small 기반 다국어 임베딩 벡터 목록을 반환한다."""
        ...

    @abstractmethod
    async def resolve_gender_from_context(self, items: List[dict], profile: dict) -> List[dict]:
        """spaCy가 이미 찾아낸 성별 표시 후보 단어(candidate_words, 문장 속
        등장 순서)가 실제로 사람을 가리키는지, 누구를 가리키는지(그룹핑),
        성별이 뭔지 스페인어 문장(target_text) 전체와 한국어 원문
        (korean_text)을 같이 보고 판단한다. spaCy 통계 모델은 형용사/부사/
        감탄사 겸용 단어(예: "rápido")나 amod 수식 대상이 사람인지 사물인지
        안정적으로 구분하지 못해(design 2026-08-12-gender-detection-llm-
        redesign-design.md), 이 판단을 문맥을 실제로 이해하는 LLM에 맡긴다.
        인물 그룹핑도 여기서 함께 판단한다 — 미리 계산된 그룹을 프롬프트에
        "이미 확정된 사실"로 먼저 보여주면 모델이 독립적으로 재도출하기보다
        그냥 승인하는 앵커링 편향이 생기므로, 원문 그대로만 보고 판단하게
        한다.

        입력은 [{"id": str, "target_text": str, "korean_text": str,
        "candidate_words": [str, ...]}, ...] — candidate_words는 문장 속
        등장 순서 그대로다(인덱스가 곧 이 순서). 반환값은
        [{"id": str, "words": [
            {"index": int, "is_person": bool, "group_id": int,
             "gender": "male"|"female"|None, "referent": str|None}, ...
        ]}, ...] — words는 입력 candidate_words와 정확히 같은 개수·순서로
        돌아와야 한다(index는 검증용). is_person=false면 사람 얘기가 아니라는
        뜻(그 후보는 성별 확인 대상에서 제외됨). 같은 인물을 가리키는 후보는
        group_id가 같아야 한다(문장 안에서만 의미 있는 임의의 정수). gender는
        확신이 있을 때만 채우고, 애매하면 None(사람에게 물어봄). referent는
        그 그룹이 누구를 가리키는지 검수자에게 보여줄 짧은 한국어 설명
        (예: "화자 자신", "Juan", "제3자")이다."""
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
            claude_light_model=os.getenv("CLAUDE_LIGHT_MODEL", "claude-haiku-4-5"),
            gpt_api_key=required["OPENAI_API_KEY"], gpt_model=required["GPT_MODEL"],
            gpt_light_model=os.getenv("GPT_LIGHT_MODEL", "gpt-5.6-luna"),
            gpt_transcribe_model=os.getenv("GPT_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        )

    raise ProviderNotConfiguredError(
        f"알 수 없거나 아직 구현되지 않은 프로바이더: {name}. "
        "실제 STT/LLM 프로바이더는 이 계획의 범위 밖에서 구현합니다."
    )
