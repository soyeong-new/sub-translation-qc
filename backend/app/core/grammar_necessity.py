"""문법 필요성 판단(성별/격식 확인이 필요한 줄 선별)과, 가능한 경우 값 자체를
자동 판정하는 순수 파이썬 모듈. LLM 호출 없이 결정론적 규칙으로 판단한다.

필요성 판단(*_check_needed)은 대상언어(target_text) 형태소만 본다 — 화자·맥락은
텍스트에 없어 판단 불가능하므로, 그 줄 자체의 문법 구조(성별 표시 형용사/분사,
활용된 동사 존재 여부)만 본다. 애매하면 재현율을 우선한다(과탐지 허용, 누락
금지) — 과탐지는 검수자가 스테퍼에서 버튼 한 번 눌러 넘기면 되지만, 누락은
존댓말/격식 오류가 검수 없이 그대로 나가는 더 나쁜 실패다.

값 자동 판정(resolved_*)은 반대로 **한국어 원문**(korean_text)을 본다 — 격식은
한국어 어미에, 성별은 호칭·대명사에 화자가 텍스트만으로 알 수 있는 단서가 실제로
있다. 여기서 확정 못 하면(어미가 애매하거나 성별 단서가 없거나 상충하면) None을
반환해 그대로 사람에게 넘긴다 — "판단 어려운 것만 질문"이 목표이지, 억지로 다
자동 판정하는 게 목표가 아니다."""

import re
from functools import lru_cache
from typing import List, Optional
import spacy

LANGUAGE_TO_SPACY_MODEL = {
    "es": "es_core_news_sm",
}

# ponytail: 한국어 형태소 분석기(kiwipiepy 등) 없이 문장 종결 어미를 정규식으로만
# 본다 — 완벽한 문법 분석이 아니라 "확실한 경우만 잡고, 애매하면 None"이 목표라
# 이 정도로 충분하다. 새 어미 패턴이 자주 새는 게 보이면 그때 형태소 분석기로
# 업그레이드.
_KOREAN_FORMAL_ENDING_RE = re.compile(
    r"(습니다|습니까|ㅂ니다|ㅂ니까|으세요|세요|이에요|예요|해요|"
    r"아요|어요|여요|가요|나요|을까요|ㄹ까요|고요|거예요|는데요)[.?!~…\s]*$"
)
_KOREAN_INFORMAL_ENDING_RE = re.compile(
    r"(야|니|냐|자|지|네|거든|잖아|구나|란다|는다|았다|었다|다|아|어|와|가|해)[.?!~…\s]*$"
)

_KOREAN_MALE_TERMS = ("오빠", "형", "아빠", "아버지", "삼촌", "아저씨", "남편", "할아버지")
_KOREAN_FEMALE_TERMS = ("언니", "누나", "엄마", "어머니", "이모", "아줌마", "아내", "할머니", "그녀")


@lru_cache(maxsize=None)
def _load_model(model_name: str):
    return spacy.load(model_name)


def _resolve_model(language: str):
    model_name = LANGUAGE_TO_SPACY_MODEL.get(language)
    if model_name is None:
        raise ValueError(f"문법 필요성 판단을 지원하지 않는 언어: {language}")
    return _load_model(model_name)


def _gender_check_needed(doc) -> bool:
    """사람을 가리킬 수 있는 성별 표시 형용사/분사(예: cansado/cansada)가
    있으면 True. 명사/관사의 문법적 성별(momento, un 등)은 사람의 성별과
    무관하므로 제외 — ADJ 품사만 본다."""
    return any(tok.pos_ == "ADJ" and tok.morph.get("Gender") for tok in doc)


def _formality_check_needed(doc) -> bool:
    """활용된(finite) 동사가 하나라도 있으면 True. usted는 스페인어 문법상
    3인칭 동사 활용을 그대로 쓰고 대명사도 자주 생략되어(자막은 특히), 인칭만
    보고는 tú/usted를 구분할 근거가 없다 — 그래서 인칭을 따지지 않고 대화체
    문장(활용된 동사가 있는 문장) 전체를 대상으로 잡는다."""
    return any(
        tok.pos_ in ("VERB", "AUX") and tok.morph.get("VerbForm") == ["Fin"]
        for tok in doc
    )


def _grammatical_person(doc) -> Optional[str]:
    """성별 표시가 걸린 문장이 1인칭(화자 자신)/2인칭(상대방)/3인칭(제3자) 중
    무엇을 가리키는지 참고용으로 추정한다. 문장의 활용 동사 중 첫 번째로 인칭
    정보가 있는 것을 쓴다 — 정확한 의존관계 분석(이 형용사가 정확히 어느
    동사에 걸리는지)까지는 안 하는 근사치다(ponytail: 한 문장에 인칭이 다른
    동사가 여러 개 섞인 복문이면 틀릴 수 있음 — 필요해지면 의존 파싱으로 승급)."""
    for tok in doc:
        if tok.pos_ in ("VERB", "AUX"):
            person = tok.morph.get("Person")
            if person:
                return person[0]
    return None


def _detect_korean_formality(korean_text: str) -> Optional[str]:
    """한국어 문장 종결 어미로 존댓말/반말을 판정한다. 격식 표시가 스페인어
    tú/usted처럼 생략되지 않고 어미에 거의 항상 명시적으로 드러나므로, 대상언어
    쪽보다 훨씬 신뢰도 높게 자동 판정할 수 있다."""
    text = korean_text.strip()
    if _KOREAN_FORMAL_ENDING_RE.search(text):
        return "formal"
    if _KOREAN_INFORMAL_ENDING_RE.search(text):
        return "informal"
    return None


def _detect_korean_gender(korean_text: str) -> Optional[str]:
    """한국어 호칭·대명사(오빠/언니/엄마/그녀 등)로 성별 단서를 찾는다. 이
    호칭이 정확히 스페인어 문장의 어느 인칭(화자/상대/제3자)을 가리키는지까지는
    확인하지 않는다 — 문장에 성별 단서가 하나만, 애매함 없이 나오는 경우에만
    쓰고, 상충하거나(둘 다 나옴) 아예 없으면 None을 반환해 다음 폴백(영어 힌트,
    그다음 사람)으로 넘긴다."""
    has_male = any(term in korean_text for term in _KOREAN_MALE_TERMS)
    has_female = any(term in korean_text for term in _KOREAN_FEMALE_TERMS)
    if has_male and not has_female:
        return "male"
    if has_female and not has_male:
        return "female"
    return None


def check_grammar_necessity(pairs: List[dict], profile: dict) -> List[dict]:
    """입력 pairs([{"id","target_text","korean_text"}, ...])와 1:1 대응하는
    결과를 반환한다: {"id", "gender_check_needed", "formality_check_needed",
    "resolved_formality", "resolved_gender_from_korean", "grammatical_person"}.
    resolved_* 필드는 한국어 원문에서 확정 가능했을 때만 값이 채워지고,
    확정 못 하면 None — 호출자(pipeline.py)가 이후 영어 SRT 힌트, 그래도
    안 되면 사람에게 순서대로 넘긴다."""
    language = profile.get("language")
    nlp = _resolve_model(language)
    texts = [p.get("target_text", "") for p in pairs]
    docs = nlp.pipe(texts)
    return [
        {
            "id": p["id"],
            "gender_check_needed": _gender_check_needed(doc),
            "formality_check_needed": _formality_check_needed(doc),
            "resolved_formality": _detect_korean_formality(p.get("korean_text", "")),
            "resolved_gender_from_korean": _detect_korean_gender(p.get("korean_text", "")),
            "grammatical_person": _grammatical_person(doc),
        }
        for p, doc in zip(pairs, docs)
    ]
