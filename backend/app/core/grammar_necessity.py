"""문법 필요성 판단(성별/격식 확인이 필요한 줄 선별)과, 가능한 경우 값 자체를
자동 판정하는 순수 파이썬 모듈. LLM 호출 없이 결정론적 규칙으로 판단한다.

필요성 판단(*_check_needed)은 대상언어(target_text) 형태소만 본다 — 화자·맥락은
텍스트에 없어 판단 불가능하므로, 그 줄 자체의 문법 구조(성별 표시 형용사/분사,
활용된 동사 존재 여부)만 본다. 애매하면 재현율을 우선한다(과탐지 허용, 누락
금지) — 성별 표시 형용사/분사는 전부 "후보"로만 반환하고, 실제로 사람을
가리키는지/누구인지/성별이 뭔지는 이 모듈의 책임이 아니다(pipeline.py의
resolve_gender_from_context LLM 호출이 판단한다) — spaCy 통계 모델은 amod
수식 대상이 사람인지 사물인지, 형용사가 서술적으로 쓰였는지 감탄사로 쓰였는지를
안정적으로 구분하지 못한다(design 2026-08-12-gender-detection-llm-redesign-
design.md §문제 진단).

값 자동 판정(resolved_*)은 반대로 **한국어 원문**(korean_text)을 본다 — 격식은
한국어 어미에, 성별은 호칭·대명사에 화자가 텍스트만으로 알 수 있는 단서가 실제로
있다. 성별(resolved_gender_from_korean)은 후보 단어가 정확히 1개일 때만
시도한다 — 2개 이상이면 한국어 단서 하나로 어느 후보를 가리키는지 안전하게
구분할 수 없어(design §그룹핑도 LLM이 직접), 확정 못 하면(단서가 없거나
상충하거나 후보가 여럿이면) None을 반환해 그대로 다음 단계(LLM, 그다음 사람)로
넘긴다. 격식(resolved_formality)은 다르다 — 한국어는 존댓말 어미가 없으면 곧
반말이라는 이분법이 성립하는 닫힌 체계라 "애매함"이 원천적으로 없다: 원문 자체가
비었을 때만 None이고, 그 외엔 항상 formal/informal 둘 중 하나로 확정된다."""

import re
from functools import lru_cache
from typing import List, Optional
import spacy
from kiwipiepy import Kiwi

LANGUAGE_TO_SPACY_MODEL = {
    "es": "es_core_news_sm",
}

# 한국어 존댓말/성별 단서는 형태소 분석기(kiwipiepy)로 판단한다. 정규식으로
# 어미/호칭을 나열하던 이전 방식은 한국어 조사·어미가 앞말에 그대로 붙어버려서
# (띄어쓰기 없음) 표면 문자열만으로 실제 의미와 구분이 안 되는 문제가 계속
# 나왔다("감사합니다"의 받침 병합, "그러니까"의 because-연결어미 vs 질문어미
# "니까", "그거/그런데"의 지시어 접두사 vs 3인칭 대명사 "그"). 형태소
# 분석기는 품사와 어말/연결 어미를 직접 태깅해줘서 이 모호성이 애초에 안 생긴다.
@lru_cache(maxsize=None)
def _kiwi() -> Kiwi:
    return Kiwi()


_KOREAN_FORMAL_EF_SUFFIXES = ("요", "죠", "니다", "니까", "시오")

# 짧은 호칭/대명사는 다른 흔한 단어의 접두사와 표면적으로 겹친다("그거/그런데"의
# "그", "형태"의 "형", "이모티콘"의 "이모") — 그래서 원문 substring 검사 대신
# 형태소 분석 결과에서 정확히 이 형태로 태깅된 토큰(명사는 NNG, 대명사 그녀는
# NP)이 있을 때만 인정한다. 여러 형태소로 쪼개지는 긴 합성어(남자친구 등)는
# "남자"+"친구"로 분리되어 다른 단어와 헷갈릴 위험이 없어 substring으로 충분.
#
# "그"(3인칭 남성 대명사)는 male 목록에 일부러 안 넣는다 — 품사 태깅(NP)으로
# "그거/그런데"류 오탐은 막아도, "그" 자체가 대명사로 쓰였다고 해서 항상
# 남성을 가리킨다고 확정할 근거는 약하다(번역문에서 여성 3인칭을 "그"로
# 옮기는 경우도 있고, 이 단서 하나로 사람 확인 없이 바로 스페인어 텍스트에
# 성별이 적용되므로 — resolved_gender_from_korean이 non-None이면 검수
# 스텝을 건너뛴다). "그녀"는 여성 대명사로 훨씬 명시적이라 유지한다.
_KOREAN_MALE_SINGLE_TERMS = {
    "오빠", "형", "아빠", "아버지", "삼촌", "아저씨", "남편", "할아버지",
    "남동생", "고모부", "외삼촌", "사위", "남성", "남자", "남학생", "남친",
}
_KOREAN_MALE_COMPOUND_TERMS = ("남편분", "남자친구", "남배우", "남가수")
_KOREAN_FEMALE_SINGLE_TERMS = {
    "언니", "누나", "엄마", "어머니", "이모", "아줌마", "아내", "할머니",
    "여동생", "고모", "외숙모", "며느리", "집사람", "부인", "여의사", "여군",
    "여경", "여성", "여자", "여학생", "여친", "여배우", "그녀",
}
_KOREAN_FEMALE_COMPOUND_TERMS = ("사모님", "여사님", "여사장", "여교수", "여자친구", "여가수")
_KOREAN_PRONOUN_TAG = {"그녀": "NP"}


def _matches_single_term(tok, terms: set) -> bool:
    if tok.form not in terms:
        return False
    return tok.tag == _KOREAN_PRONOUN_TAG.get(tok.form, "NNG")


@lru_cache(maxsize=None)
def _load_model(model_name: str):
    return spacy.load(model_name)


def _resolve_model(language: str):
    model_name = LANGUAGE_TO_SPACY_MODEL.get(language)
    if model_name is None:
        raise ValueError(f"문법 필요성 판단을 지원하지 않는 언어: {language}")
    return _load_model(model_name)


def _is_gendered_token(tok) -> bool:
    """성별 어미가 있는 형용사/분사인지 판단한다. ADJ만으로는 부족하다 —
    수동태/완료형 분사("fue abierta", "han sido invitados")는 spaCy가
    VERB+VerbForm=Part로 태깅해서 ADJ 필터에 안 잡힌다. 이 확장은 사물/상황에
    문법적으로 일치하는 분사도 같이 걸러낸다("la puerta fue abierta"의
    "abierta"는 사람이 아니라 puerta에 일치하는 것) — 하지만 그 판단(사람인지
    사물인지)은 여기서 하지 않는다. spaCy Gender 형태소가 있는 ADJ/분사는
    전부 후보다 — "실제로 사람 얘기인가"는 LLM(resolve_gender_from_context)의
    몫이다(design §spaCy는 순수 형태소 체크만)."""
    if not tok.morph.get("Gender"):
        return False
    if tok.pos_ == "ADJ":
        return True
    return tok.pos_ == "VERB" and tok.morph.get("VerbForm") == ["Part"]


def _candidate_tokens(doc) -> list:
    return [tok for tok in doc if _is_gendered_token(tok)]


def _formality_check_needed(doc) -> bool:
    """활용된(finite) 동사가 하나라도 있으면 True. usted는 스페인어 문법상
    3인칭 동사 활용을 그대로 쓰고 대명사도 자주 생략되어(자막은 특히), 인칭만
    보고는 tú/usted를 구분할 근거가 없다 — 그래서 인칭을 따지지 않고 대화체
    문장(활용된 동사가 있는 문장) 전체를 대상으로 잡는다."""
    return any(
        tok.pos_ in ("VERB", "AUX") and tok.morph.get("VerbForm") == ["Fin"]
        for tok in doc
    )


def _detect_korean_formality(korean_text: str) -> Optional[str]:
    """마지막 어말어미(EF 태그)로 존댓말/반말을 판정한다. 형태소 분석기가
    어말어미(EF)와 연결어미(EC)를 이미 구분해주므로, "니까"가 질문 어미(EF,
    "괜찮습니까")인지 "because" 연결어미(EC, "그러니까"/"추우니까")인지도
    별도 규칙 없이 저절로 갈린다. 어말어미가 아예 없으면(활용된 문장 자체가
    아님) informal 기본값 — 한국어 원문 자체가 비어 있을 때만 None으로
    사람에게 넘긴다."""
    text = korean_text.strip()
    if not text:
        return None
    ef_forms = [t.form for t in _kiwi().tokenize(text) if t.tag == "EF"]
    if ef_forms and any(ef_forms[-1].endswith(suf) for suf in _KOREAN_FORMAL_EF_SUFFIXES):
        return "formal"
    return "informal"


def _detect_korean_gender(korean_text: str) -> Optional[str]:
    """한국어 호칭·대명사(오빠/언니/엄마/그녀 등)로 성별 단서를 찾는다. 이
    호칭이 정확히 스페인어 문장의 어느 인칭(화자/상대/제3자)을 가리키는지까지는
    확인하지 않는다 — 문장에 성별 단서가 하나만, 애매함 없이 나오는 경우에만
    쓰고, 상충하거나(둘 다 나옴) 아예 없으면 None을 반환해 다음 폴백(LLM,
    그다음 사람)으로 넘긴다."""
    text = korean_text.strip()
    if not text:
        return None
    tokens = _kiwi().tokenize(text)
    has_male = (
        any(_matches_single_term(t, _KOREAN_MALE_SINGLE_TERMS) for t in tokens)
        or any(term in text for term in _KOREAN_MALE_COMPOUND_TERMS)
    )
    has_female = (
        any(_matches_single_term(t, _KOREAN_FEMALE_SINGLE_TERMS) for t in tokens)
        or any(term in text for term in _KOREAN_FEMALE_COMPOUND_TERMS)
    )
    if has_male and not has_female:
        return "male"
    if has_female and not has_male:
        return "female"
    return None


_GENDER_TO_MORPH = {"male": "Masc", "female": "Fem"}


def _inflect_gender_word(word: str, target_gender: str) -> str:
    """스페인어 형용사/분사 어미를 규칙 기반으로 다른 성별로 바꾼다.
    ponytail: -o/-a, -or/-ora 같은 흔한 규칙형만 처리한다 — 원래 성별
    무관한 형용사(feliz, inteligente, optimista 등)는 이 패턴에 안 걸려
    그대로 반환되니 안전하다. 커버리지가 부족해지면 스페인어 굴절
    라이브러리로 승급."""
    if not word:
        return word
    lower = word.lower()
    upper = word[-1].isupper()
    if target_gender == "female":
        if lower.endswith("or"):
            return word + ("A" if upper else "a")
        if lower.endswith("o"):
            return word[:-1] + ("A" if upper else "a")
    elif target_gender == "male":
        if lower.endswith("ora"):
            return word[:-1]
        if lower.endswith("a") and not lower.endswith("ista"):
            return word[:-1] + ("O" if upper else "o")
    return word


def _apply_span_replacements(text: str, replacements: List[tuple]) -> str:
    """(start, end, new_word) 스팬 리스트를 원본 오프셋 기준으로 치환한다.
    text.replace(old, new)처럼 표면형 문자열로 찾아 바꾸면, 같은 단어가
    문장에 두 번 나올 때(다른 인물 소속이라도) 전부 바뀌어버린다("Juan está
    cansado, pero María no está cansado."에서 María 쪽만 고치려 해도 Juan
    쪽까지 같이 바뀜) — 토큰 위치(tok.idx)로 정확히 그 자리만 바꾼다. 뒤에서
    부터 치환해야 앞쪽에 아직 안 바꾼 오프셋이 밀리지 않는다."""
    for start, end, new_word in sorted(replacements, key=lambda r: r[0], reverse=True):
        text = text[:start] + new_word + text[end:]
    return text


_GENDER_SLASH_RE = re.compile(r"\b(\w+)/(\w{1,3})\b")


def _collapse_gender_slashes(text: str, target_gender: str) -> str:
    """번역문에 "cansado/a"처럼 두 성별 형태를 슬래시로 같이 적어둔 미확정
    표기가 남아있으면, 목표 성별 하나로 접어서 지운다. spaCy 토큰화에
    기대지 않는다 — 슬래시 뒤 짧은 접미사가 ADJ로 오태깅되며 이 표기를
    놓치는 문제를 애초에 피해간다."""
    def repl(match: "re.Match[str]") -> str:
        return _inflect_gender_word(match.group(1), target_gender)
    return _GENDER_SLASH_RE.sub(repl, text)


def resolve_gender_in_texts(items: List[dict], language: str) -> dict:
    """검수자가 확정한 성별을 AI에게 "반영해달라"고 부탁하는 대신 파이썬이
    직접 문장에 반영한다 — 형용사 성별 어미는 결정론적 문법 규칙이라, AI가
    다른 검증(오역/뉘앙스 등)에 집중하다 이 지시를 놓치는 문제를 원천적으로
    없앤다. items: [{"id","text","gender"("male"/"female")}, ...]. 여러 건을
    한 번에 처리한다(spaCy 모델 로딩·파이프라인 오버헤드를 한 번만 지불).
    반환값은 {id: 수정된 text} — 이미 요청한 성별과 일치하는 문장은 그대로
    돌아온다."""
    nlp = _resolve_model(language)
    # 슬래시 미확정 표기는 spaCy가 보기 전에 먼저 접어둔다 — "/"가 ADJ로
    # 잘못 태깅되며 뒤 토큰 루프가 그 표기를 놓치는 문제를 피하고, 이후
    # 토큰화도 접힌 문장 기준으로 정확히 이뤄지게 하기 위해서다.
    texts = [
        _collapse_gender_slashes(i["text"], i["gender"]) if _GENDER_TO_MORPH.get(i["gender"]) else i["text"]
        for i in items
    ]
    docs = nlp.pipe(texts)
    results: dict = {}
    for item, text, doc in zip(items, texts, docs):
        target_morph = _GENDER_TO_MORPH.get(item["gender"])
        replacements = []
        if target_morph:
            for tok in _candidate_tokens(doc):
                if tok.morph.get("Gender")[0] == target_morph:
                    continue
                new_word = _inflect_gender_word(tok.text, item["gender"])
                if new_word != tok.text:
                    replacements.append((tok.idx, tok.idx + len(tok.text), new_word))
        results[item["id"]] = _apply_span_replacements(text, replacements)
    return results


def resolve_gender_groups_in_texts(items: List[dict], language: str) -> dict:
    """resolve_gender_in_texts의 다인물 버전 — 한 줄에 성별이 다른 인물이
    둘 이상 있을 때, 인물(그룹)별로 확정된 성별을 그 인물에 속한 단어에만
    적용한다(다른 인물의 단어는 건드리지 않는다). items:
    [{"id","text","groups":[{"candidate_indices":[int,...], "gender":
    "male"/"female"}, ...]}]. 반환값은 {id: 수정된 text}.

    candidate_indices는 "이 텍스트를 spaCy로 다시 파싱했을 때 나오는 후보
    토큰(성별 어미 있는 형용사/분사) 목록을 문장 속 등장 순서로 셌을 때 몇
    번째인가"다 — 이 순서는 같은 텍스트라면 항상 결정론적으로 같다. 그룹핑
    자체(어느 후보가 같은 인물인가)는 LLM(resolve_gender_from_context)이
    판단해 넘겨준 것을 그대로 신뢰하고, 여기서는 그 인덱스로 정확히 그
    토큰만 찾아 치환한다 — 의존구문을 다시 분석해 그룹을 재구성하지
    않는다(design §그룹핑도 LLM이 직접, 재적용은 순서 매칭만). 존재하지
    않는 인덱스(텍스트가 바뀌어 후보 수가 줄어든 경우 등)는 조용히
    무시한다."""
    nlp = _resolve_model(language)
    texts = [i["text"] for i in items]
    docs = nlp.pipe(texts)
    results: dict = {}
    for item, text, doc in zip(items, texts, docs):
        candidates = _candidate_tokens(doc)
        gender_by_index: dict = {}
        for group in item["groups"]:
            target_gender = group.get("gender")
            if target_gender not in _GENDER_TO_MORPH:
                continue
            for idx in group["candidate_indices"]:
                gender_by_index[idx] = target_gender
        replacements = []
        for idx, tok in enumerate(candidates):
            target_gender = gender_by_index.get(idx)
            if target_gender is None:
                continue
            target_morph = _GENDER_TO_MORPH[target_gender]
            if tok.morph.get("Gender")[0] == target_morph:
                continue
            new_word = _inflect_gender_word(tok.text, target_gender)
            if new_word != tok.text:
                replacements.append((tok.idx, tok.idx + len(tok.text), new_word))
        results[item["id"]] = _apply_span_replacements(text, replacements)
    return results


def check_grammar_necessity(pairs: List[dict], profile: dict) -> List[dict]:
    """입력 pairs([{"id","target_text","korean_text"}, ...])와 1:1 대응하는
    결과를 반환한다: {"id", "gender_check_needed", "formality_check_needed",
    "resolved_formality", "resolved_gender_from_korean", "candidate_words",
    "candidate_word_lemmas"}. resolved_* 필드는 한국어 원문에서 확정 가능했을
    때만 값이 채워지고, 확정 못 하면 None — 호출자(pipeline.py)가 이후 LLM
    문맥 판단, 그래도 안 되면 사람에게 순서대로 넘긴다. candidate_words/
    candidate_word_lemmas는 성별 표시 후보 단어를 문장 속 등장 순서로 나열한
    병렬 리스트다 — 실제로 사람을 가리키는지/누구인지/성별이 뭔지는 이 함수의
    책임이 아니다."""
    language = profile.get("language")
    nlp = _resolve_model(language)
    texts = [p.get("target_text", "") for p in pairs]
    docs = nlp.pipe(texts)
    results = []
    for p, doc in zip(pairs, docs):
        candidates = _candidate_tokens(doc)
        candidate_words = [tok.text for tok in candidates]
        candidate_word_lemmas = [tok.lemma_.lower() for tok in candidates]
        resolved_gender_from_korean = None
        if len(candidates) == 1:
            resolved_gender_from_korean = _detect_korean_gender(p.get("korean_text", ""))
        results.append({
            "id": p["id"],
            "gender_check_needed": bool(candidates),
            "formality_check_needed": _formality_check_needed(doc),
            "resolved_formality": _detect_korean_formality(p.get("korean_text", "")),
            "resolved_gender_from_korean": resolved_gender_from_korean,
            "candidate_words": candidate_words,
            "candidate_word_lemmas": candidate_word_lemmas,
        })
    return results
