"""STT/번역/민감어 판단을 수행하는 ModelProvider 추상 인터페이스와 프로바이더 선택 로직."""

import os
import re
from abc import ABC, abstractmethod
from typing import List, Optional

_HANGUL_RE = re.compile(r"[가-힣]")


def contains_hangul(text: str) -> bool:
    """corrected_text가 대상언어가 아니라 한국어로 새어나온 경우를 감지한다
    (design 논의: 프롬프트에 '한국어를 절대 섞지 마라'는 지시가 이미 있어도
    모델이 배치 처리 중 다른 항목의 korean_text를 착각해 그대로 옮기는
    사례가 실측 확인됨 — 지시만으로는 못 막아 응답 내용 자체를 검증해야 함)."""
    return bool(_HANGUL_RE.search(text or ""))


CATEGORY_ENUM = ["sensitivity", "mistranslation", "nuance_tone",
                  "unnatural_style", "locale_convention", "glossary"]

VERIFICATION_PRIORITY_PARAGRAPH = (
    "⚠️ [우선순위] 아래 규칙들이 서로 충돌하면 이 순서를 따르라: "
    "오역/심의 정확성 > 씬 내 반복 표현 일관성 > 자연스러움. "
    "위 우선순위를 지키는 한 원문의 어순·문장 구조를 그대로 따를 의무는 없다 — "
    "같은 내용을 전달하면 문장을 자유롭게 재구성해 가장 자연스러운 표현으로 의역하라. "
    "사실이 아닌 부연 설명·수식어는 간결하게 줄여도 된다.\n\n"
)

def build_batch_scope_intro(glossary_block: str = "") -> str:
    step_count = 6 if glossary_block else 5
    return (
        f"각 세그먼트를 먼저 전체적으로 읽고, 명백한 문제가 있다고 확신되는 경우에만 아래 [{step_count}단계 체크리스트]에서 해당하는 카테고리를 찾아 교정 사항(findings)을 작성하라. "
        f"'혹시 여기도 어느 카테고리 하나쯤 해당되지 않을까' 하는 식으로 {step_count}개 카테고리를 억지로 하나씩 끼워 맞추려 하지 마라 — 명백한 문제가 없는 세그먼트는 그냥 건너뛰어라.\n\n"
    )

BATCH_SKIP_CLEAN_LINE = "   - 수정할 오류가 없는 깨끗한 문장은 절대 응답 배열에 포함하지 마라.\n"

def build_requery_scope_intro(glossary_block: str = "") -> str:
    step_count = 6 if glossary_block else 5
    return (
        "이 세그먼트는 검수자가 이미 문제가 있다고 판단해 재검토를 요청한 것이다 — "
        "너 스스로 '문제가 명백한지' 다시 판단해 건너뛰지 말고, "
        f"아래 [{step_count}단계 체크리스트]에서 "
        "가장 가까운 카테고리를 찾아 검수자 지시사항을 반영한 교정 사항(findings)을 반드시 작성하라. "
        "이 세그먼트를 배열에서 빼는 것은 금지된다.\n"
        "⚠️ 아래 target_text는 이전 검토에서 이미 한 번 고친 결과물이다 — 네가(또는 다른 "
        "모델이) 만들었다는 이유로 이미 맞다고 안일하게 판단하지 말고, korean_text와 처음부터 "
        "다시 대조해 검수자 지시사항 관점에서 재검토하라.\n\n"
    )

REQUERY_SKIP_CLEAN_LINE = (
    "   - (재질문 예외) 이 세그먼트는 검수자가 이미 지적했으므로, 위 규칙과 달리 "
    "반드시 응답 배열에 포함하라.\n"
)


def build_verification_checklist(language_label: str, skip_clean_line: str,
                                  glossary_block: str = "") -> str:
    """[검수 범위 및 교정 원칙] + [N단계 순차 검증 체크리스트]. claude/gpt가
    재질문 여부에 따라 다른 skip_clean_line만 끼워 넣고 나머지는 동일하게
    쓴다. glossary_block이 있으면(작품 용어집에 이미 확정된 표기가 하나라도
    있으면) 고유명사 표기 일관성 항목이 체크리스트에 추가되고 단계 수가
    6단계로 늘어난다 — glossary_block은 이 함수 밖(호출부)에서 실제
    [작품 용어집] 내용으로 시스템 프롬프트에 삽입된다."""
    step_count = 6 if glossary_block else 5
    glossary_item = (
        '5. 고유명사 표기 일관성 (category: "glossary"):\n'
        '   - 기준: 아래 [작품 용어집]에 등록된 한국어 용어가 korean_text에 있는데, target_text의 표기가 용어집의 표기와 다른가?\n'
        '   - 교정 지침: 용어집 표기로 통일하되, 문장 문법(관사·전치사·성수 일치)에 맞게 자연스럽게 넣어라. 용어집에 없는 고유명사는 건드리지 마라.\n'
        '   - 주의: 같은 성씨를 쓰는 다른 인물 등 문맥상 다른 대상을 가리키는 게 분명하면 교정하지 마라.\n'
        '   - [작품 용어집]의 한국어 용어는 대표형이다. 축약형·호격형(예: 김현 → 현, 현아)도 같은 대상으로 보고 같은 스펠링을 쓰되, 문장에서 실제로 부르는 형태(성+이름 전체 / 이름만)는 원문을 따라라.\n'
    ) if glossary_block else ""
    return (
        "⚠️ [검수 범위 및 교정 원칙]\n"
        "1. 반드시 교정해야 하는 대상:\n"
        "   - 오역 및 핵심 의미 누락/와전 (category: \"mistranslation\")\n"
        "   - 방송/미디어 심의 위반 비속어 (category: \"sensitivity\")\n"
        "   - 한국어 구조를 그대로 따라가 현지인이 읽기에 어색한 직역투 (category: \"unnatural_style\")\n"
        "   - 현지 문화권 관습, 관용구, 단위 표기 오류 (category: \"locale_convention\")\n"
        "   - 지정된 성별(대상언어 문법상 성별 어미) 및 격식(존댓말/반말) 파라미터 위반\n"
        "2. 교정 금지 대상 (취향 차이의 다듬기):\n"
        "   - 의미 왜곡이 없고 현지 구어체로 이미 타당한 번역인데, 단순히 AI 개인 선호 어휘나 동의어로 다듬는 수정은 제안하지 마라.\n"
        + skip_clean_line +
        "   - nuance_tone(뉘앙스·어조)은 다음 경우에만 제안하라:\n"
        f"     * 직역투로 인해 명백히 어색한 경우 (한국어 구조를 그대로 따라가 {language_label}로서 부자연스러운 경우)\n"
        "     * 한국어 원문의 감정·톤(급함, 거침, 간결함, 여유로움 등)이 명확히 다르게 전달된 경우\n"
        "   - 이미 자연스러운 구어체 표현이면 건드리지 마라. 원문의 감정·톤을 정확히 전달하고 있으면 제안하지 마라.\n\n"
        f"[{step_count}단계 순차 검증 체크리스트]\n"
        "1. 방송/미디어 심의 비속어 검수 (category: \"sensitivity\"):\n"
        "   - 기준: 영상 방영 및 미디어 심의(Broadcasting Rating)상 제재나 경고 대상이 될 수 있는 심한 비속어, 성적·인격모독적 표현이 포함되어 있는가?\n"
        "   - 교정 지침: 대사의 거친 뉘앙스는 유지하되, 방송 심의 기준에 적합한 수위가 약한 비속어나 자연스러운 순화 표현으로 교정(`corrected_text`)하라.\n"
        "2. 오역 및 핵심 의미 누락 (category: \"mistranslation\"):\n"
        "   - 기준: korean_text의 실제 의미와 target_text의 번역 의미가 다르게 와전되었거나, 문장의 핵심 의미가 생략되었는가? 또는 원문의 구체적 사실(인물·장소·숫자·행동)이 생략·변경·추가되었는가? 단, 사실이 아닌 부연 설명·수식어를 줄인 것은 여기 해당하지 않는다.\n"
        "   - 교정 지침: 원문의 뜻을 왜곡 없이 정확하게 전달하도록 교정하라.\n"
        "3. 어색한 어조 및 직역투 (category: \"unnatural_style\" 또는 \"nuance_tone\"):\n"
        f"   - 기준: 문법은 맞지만 한국어 어순/표현을 그대로 따라간 직역투라 {language_label}로서 어색한가? 또는 한국어 원문의 감정·톤이 명확히 다르게 전달되었는가?\n"
        f"   - 교정 지침: 원문의 감정·톤을 정확히 살리면서 {language_label}권 현지인이 실제로 사용하는 자연스러운 구어체로 교정하라. 자막은 화면과 함께 순간적으로 읽는 매체이니 뜻이 통하는 선에서 최대한 간결하게 써라 — 화면으로 이미 전달되는 정보나 불필요한 부연 설명은 생략하라. 같은 씬 안에서 한국어 원문의 단어/표현이 반복되면, 문법적으로 다르게 써야 할 이유가 없는 한 같은 번역으로 통일하라.\n"
        "   - 주의: 원문이 이미 자연스러운 구어체로 한국어의 감정·톤을 잘 전달하고 있으면 nuance_tone 제안을 하지 마라.\n"
        "4. 문화 맥락 및 로컬라이제이션 (category: \"locale_convention\"):\n"
        f"   - 기준: {language_label}권 문화 관습, 관용 표현, 단위 표기(미터법/화폐 등)에 안 맞는 번역이 있는가?\n"
        "   - 교정 지침: 해당 언어권의 문화적 관습과 로컬라이제이션 관례에 맞게 교정하라.\n"
        + glossary_item +
        f"{step_count}. 이미 반영된 성별/격식 형태 보존:\n"
        "   - 기준: target_text에 이미 특정 성별 어미(대상언어 문법상 형용사·분사·명사 어미)나 격식(존댓말/반말) 형태가 반영되어 있을 수 있다 — 그 형태가 사전상 어색하거나 비표준으로 보여도, 검수 과정에서 의도적으로 맞춘 것이니 임의로 '자연스럽게' 되돌리지 마라.\n"
        f"   - 교정 지침: 위 1~{step_count - 1}번 문제를 고치기 위해 교정문(`corrected_text`)을 작성할 때도, target_text에 이미 있는 성별 어미·격식 형태는 그대로 유지하라 — 오직 그 카테고리의 문제만 고쳐라.\n\n"
    )


def build_json_instruction(envelope_declaration: str) -> str:
    """수정 불필요 항목 처리 지시(배치용). envelope_declaration만 API별로
    다르고(claude: JSON 배열, gpt: {"findings": [...]} 객체) 나머지 스킵 로직은 동일하다."""
    return (
        envelope_declaration +
        "수정이 필요 없는 세그먼트는 배열에 포함하지 마라. "
        "검토 도중 판단을 바꿔 결국 수정이 필요 없다고 결론 내렸다면, 그 항목은 "
        "배열에서 완전히 빼라 — description에 '다시 검토하니', '재검토 결과' 같은 "
        "번복 과정을 남기지 마라. 배열에 포함하는 항목은 처음부터 끝까지 하나의 "
        "최종 결론만 담아야 한다."
    )


def build_json_instruction_requery(envelope_declaration: str) -> str:
    """수정 불필요 항목 처리 지시(재질문용) — 검수자가 이미 지적한 단건이므로
    배열에서 빼는 것 자체를 금지한다."""
    return (
        envelope_declaration +
        "이 세그먼트는 검수자가 이미 지적한 것이므로 배열에서 빼는 것은 금지된다 — 검토 도중 판단이 "
        "바뀌더라도 배열에 포함한 채로, description에 '다시 검토하니', '재검토 결과' "
        "같은 번복 과정 없이 하나의 최종 결론만 담아 작성하라."
    )


def build_findings_schema_instruction(lead_in: str) -> str:
    """findings 응답 스키마 산문 설명. lead_in(배열을 어떻게 지칭할지)만
    API별로 다르고 필드 설명은 동일하다."""
    return (
        lead_in +
        'segment_id (문자열, 입력 pair의 "id"와 반드시 일치), '
        'category (문자열, 반드시 다음 중 하나: '
        '"sensitivity"(사전에 없어 애매한 비속어), '
        '"mistranslation"(의미가 잘못 옮겨졌거나 함축된 의미가 빠진 경우), '
        '"nuance_tone"(뉘앙스·어조가 원문과 다른 경우), '
        '"unnatural_style"(문법은 맞지만 한국어 구조를 그대로 따라간 직역투·어색한 흐름), '
        '"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현), '
        '"glossary"(작품 용어집에 등록된 고유명사 표기와 다르게 번역된 경우)), '
        "corrected_text (문자열, 최종 교정된 전체 대상언어 텍스트 — 절대 한국어로 쓰면 "
        "안 된다. 아래 '한국어로 써라' 지침은 description 필드에만 적용되고 "
        "corrected_text에는 적용되지 않는다), "
        "description (문자열, 무엇을 왜 그렇게 고쳤는지 한국어로 설명). "
        "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라. "
        "description의 설명 문장 자체는 예외 없이 한국어로 써라 — 다른 언어로 "
        "설명하지 마라. corrected_text는 정반대로 한국어를 절대 섞지 말고 대상언어로만 "
        "써라. 단, 대상언어 원문 표현을 예시로 인용하는 것은 괜찮다(예: \"'경비아저씨' "
        "표현이 어색해 'el guardia'로 수정\")."
    )


def build_naturalness_instruction_line(naturalness_instruction: str) -> str:
    """언어 프로파일의 naturalness_check.llm_instruction 주입 줄. 비어 있으면
    아무것도 추가하지 않는다."""
    if not naturalness_instruction:
        return ""
    return f"자연스러움 지침: {naturalness_instruction}\n"


def build_glossary_block(entries: List[dict]) -> str:
    """작품 용어집을 검증 프롬프트에 주입할 [작품 용어집] 블록으로 만든다.
    entries가 비어 있으면(첫 회차·첫 언어라 아직 확정된 표기가 없으면) 빈
    문자열을 반환해, build_verification_checklist의 체크리스트에서 고유명사
    표기 일관성 항목 자체가 빠지게 한다."""
    if not entries:
        return ""
    lines = ["[작품 용어집]"]
    for e in entries:
        line = f"- {e['korean_term']} ({e['category']}): {e['canonical']}"
        if e.get("aliases"):
            line += f" (별칭: {', '.join(e['aliases'])})"
        lines.append(line)
    return "\n".join(lines) + "\n\n"


def build_improvement_judgment_criteria(language_label: str) -> str:
    """back_translate의 is_improvement 판정 문단. "정보 보존"이라는 뭉뚱그린
    기준 대신, 판정 난이도를 감안해 두 개의 닫힌 목록(false 사유 / false
    사유 아님)으로 쓴다(경량 모델이 수행하므로 열린 질문보다 부담이 적다)."""
    return (
        "2. text가 original_text보다 reference_korean의 의미·톤을 더 잘 "
        f"살리는 자연스러운 {language_label} 표현인지 판단하라"
        "(is_improvement). 의미 왜곡 없이 이미 자연스러운데 단순히 어휘 "
        "취향만 다르다면 개선으로 보지 마라 — 동등하면 false.\n"
        "다음 중 하나라도 해당하면 false다:\n"
        "  - reference_korean과 다른 인물·장소·숫자·행동을 가리키게 되었다\n"
        "  - reference_korean에 없던 사실이 새로 생겼다\n"
        "  - 장면을 이해하는 데 필요한 사실이 사라졌다\n"
        "다음은 false 사유가 아니다 — 해당하면 개선으로 인정하라:\n"
        "  - 어순이 바뀌었다\n"
        "  - 문장 구조가 재구성되었다\n"
        "  - 자막 길이에 맞춰 중복되는 표현이나 부연 수식어가 압축되었다\n"
    )


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
        """Claude로 대상언어 텍스트를 한국어로 역번역하고(감사/참고용), 동시에
        text가 original_text보다 실제로 나아졌는지도 판단한다. GPT가 만든
        텍스트만 여기로 들어온다 — 자기가 만든 텍스트를 자기가 판단하면
        스스로의 오류를 매끄럽게 얼버무려 가릴 위험이 있어(같은 모델의 왕복
        번역/판단은 오류를 숨기는 경향), 항상 반대쪽 모델이 판단한다. 입력은
        [{"id": str, "reference_korean": str(한국어 원문),
        "original_text": str(교정 전), "text": str(교정 후)}], 반환값은
        [{"id": str, "korean_text": str(text의 역번역),
        "original_korean_text": str(original_text의 역번역),
        "is_improvement": bool}]."""
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

    @abstractmethod
    async def verify_gender_swap(self, items: List[dict], profile: dict) -> List[dict]:
        """확정된 성별을 파이썬이 문법 규칙으로 기계적으로 치환한 직후, 실제로
        바뀐 문장만 좁게 검증한다(design §치환 직후 검증+롤백 안전망). spaCy
        구조 규칙과 LLM is_person 판단을 다 거쳐도 못 거르는 미지의 오탐
        (예: spaCy가 애초에 잘못 태깅한 단어를 성별 어미로 착각해 엉뚱하게
        치환)이 남을 수 있다 — 이 콜은 그 마지막 방어선이다. S2(이중검증)
        프롬프트는 "이미 반영된 성별 형태는 되돌리지 마라"고 명시적으로
        지시받으므로, 이 검증은 반드시 S2 호출 전에 끝나야 한다. 의미·
        자연스러움·어휘 선택은 판단 대상이 아니다 — 오직 "이 치환으로 문장이
        문법적으로 깨졌는가(존재하지 않는 단어, 성별/수 불일치 등)"만 본다.

        입력은 [{"id": str, "text": str(치환 후 문장)}, ...], 반환값은
        [{"id": str, "has_error": bool}, ...]."""
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
