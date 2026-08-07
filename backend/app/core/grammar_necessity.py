"""문법 필요성 판단(성별/격식 확인이 필요한 줄 선별)을 spaCy 형태소 분석으로
수행하는 순수 파이썬 모듈. LLM 호출 없이 결정론적 규칙으로 판단한다 —
화자·맥락은 텍스트에 없어 어차피 판단 불가능하므로, 그 줄 자체의 문법
구조(성별 표시 형용사/분사, 활용된 동사 존재 여부)만 본다.

애매하면 재현율을 우선한다(과탐지 허용, 누락 금지) — 과탐지는 검수자가 스테퍼에서
버튼 한 번 눌러 넘기면 되지만, 누락은 존댓말/격식 오류가 검수 없이 그대로
나가는 더 나쁜 실패다."""

from functools import lru_cache
from typing import List
import spacy

LANGUAGE_TO_SPACY_MODEL = {
    "es": "es_core_news_sm",
}


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


def check_grammar_necessity(pairs: List[dict], profile: dict) -> List[dict]:
    """입력 pairs([{"id","target_text"}, ...])와 1:1 대응하는
    [{"id","gender_check_needed","formality_check_needed"}, ...]를 반환한다.
    check_grammar_necessity 프로바이더 메서드(이제 제거됨)와 동일한 계약을
    유지해 pipeline.py 호출부를 그대로 재사용할 수 있게 한다."""
    language = profile.get("language")
    nlp = _resolve_model(language)
    texts = [p.get("target_text", "") for p in pairs]
    docs = nlp.pipe(texts)
    return [
        {
            "id": p["id"],
            "gender_check_needed": _gender_check_needed(doc),
            "formality_check_needed": _formality_check_needed(doc),
        }
        for p, doc in zip(pairs, docs)
    ]
