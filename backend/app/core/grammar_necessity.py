"""문법 필요성 판단(성별/격식 확인이 필요한 줄 선별)과, 가능한 경우 값 자체를
자동 판정하는 순수 파이썬 모듈. LLM 호출 없이 결정론적 규칙으로 판단한다.

필요성 판단(*_check_needed)은 대상언어(target_text) 형태소만 본다 — 화자·맥락은
텍스트에 없어 판단 불가능하므로, 그 줄 자체의 문법 구조(성별 표시 형용사/분사,
활용된 동사 존재 여부)만 본다. 애매하면 재현율을 우선한다(과탐지 허용, 누락
금지) — 과탐지는 검수자가 스테퍼에서 버튼 한 번 눌러 넘기면 되지만, 누락은
존댓말/격식 오류가 검수 없이 그대로 나가는 더 나쁜 실패다.

값 자동 판정(resolved_*)은 반대로 **한국어 원문**(korean_text)을 본다 — 격식은
한국어 어미에, 성별은 호칭·대명사에 화자가 텍스트만으로 알 수 있는 단서가 실제로
있다. 성별(resolved_gender_from_korean)은 확정 못 하면(단서가 없거나 상충하면)
None을 반환해 그대로 사람에게 넘긴다 — "판단 어려운 것만 질문"이 목표이지,
억지로 다 자동 판정하는 게 목표가 아니다. 격식(resolved_formality)은 다르다 —
한국어는 존댓말 어미가 없으면 곧 반말이라는 이분법이 성립하는 닫힌 체계라
"애매함"이 원천적으로 없다: 원문 자체가 비었을 때만 None이고, 그 외엔 항상
formal/informal 둘 중 하나로 확정된다."""

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


def _referent_key(tok):
    """성별 표시 형용사/분사 토큰이 문장 속 누구(어느 명사/대명사) 얘기인지
    spaCy 의존구문을 따라가 추정한 앵커 토큰의 인덱스를 반환한다 — 정답을
    보장하지 않는다, 어디까지나 문법 구조 기반 추정이다. 한 줄에 성별이
    다른 인물이 둘 이상 있을 때, 확정된 성별 하나를 문장 전체에 무작정
    적용하면 엉뚱한 인물의 형용사까지 잘못 바뀌는 걸 막기 위한 최선의
    근사치다(사용자 피드백: "인칭을 제대로 구분 못하는 경우가 있다").
    (1) 자기 자신에게 딸린 주어(nsubj)가 있으면 그 주어가 곧 이 형용사가
        가리키는 인물이다(예: "Él está cansado y ella está feliz"에서 각
        형용사가 자기 주어를 따로 갖는다).
    (2) 명사를 직접 수식하면(amod, 예: "un hombre cansado") 그 명사가 인물이다.
    (3) 등위접속(conj)으로 앞 형용사에 이어붙었고 자기 주어가 없으면(예:
        "Está cansado y aburrido" — 주어 생략, 같은 인물의 술어 두 개), 앞
        형용사와 같은 인물을 가리키는 것이므로 그 앞 형용사의 앵커를 그대로
        물려받는다.
    (4) 그 무엇도 없으면(주어 생략, 단독 술어) 자기 자신이 앵커다.

    한계: 같은 절 안에서는 잘 맞지만, 절을 넘어가는 지시 관계는 원천적으로
    못 푼다 — 이건 문법(syntax)이 아니라 의미(semantics)/담화 맥락을 알아야
    풀리는 문제라 dependency parse만으로는 안 된다. 예: "Juan le dijo a
    María que estaba cansada."에서 "cansada"의 실제 화자는 María이지만,
    spaCy는 종속절의 관계대명사 "que"를 nsubj로 잡아 (1)이 "que" 자신을
    앵커로 반환해버린다(Juan도 María도 아닌 엉뚱한 토큰). 이런 경우 그룹은
    만들어지지만(구분은 됨) 그 그룹이 실제 누구인지는 이 함수도, 호출자도
    모른다 — 검수자가 문맥/영상을 보고 판단해야 하는 영역이다."""
    for child in tok.children:
        if child.dep_ == "nsubj":
            return child.i
    if tok.dep_ == "amod":
        return tok.head.i
    if tok.dep_ == "conj" and tok.head.i != tok.i:
        return _referent_key(tok.head)
    return tok.i


# amod(명사 직접 수식)로 걸린 형용사/분사가 가리키는 명사가 사람일 가능성이
# 높은 경우만 화이트리스트로 잡는다("un hombre cansado"의 hombre 등) —
# "tiempo compartido"(타임셰어)의 "tiempo"처럼 흔한 사물/추상 명사까지 다
# 사람으로 오인하면, 실제로 사고가 난다: 이 그룹의 성별 확인에 누군가
# 남성/여성을 답하면(회귀 재현, "compartido"→"compartida") 사람과 무관한
# 단어가 조용히 잘못 바뀌어 최종 자막에 그대로 나간다 — "해당없음" 버튼이
# 있어도 검수자가 "이게 왜 성별 확인 대상인지" 이해 못 하면 못 거른다.
# PROPN/PRON(이름·대명사)은 화이트리스트 없이 항상 사람으로 본다.
_COMMON_PERSON_NOUNS = frozenset({
    "hombre", "mujer", "chico", "chica", "chiquillo", "chiquilla",
    "niño", "niña", "muchacho", "muchacha", "joven",
    "señor", "señora", "señorita", "amigo", "amiga",
    "novio", "novia", "esposo", "esposa", "amante", "persona", "tipo", "tipa",
})


def _modifies_plausible_person(head_tok) -> bool:
    if head_tok.pos_ in ("PROPN", "PRON"):
        return True
    return head_tok.lemma_.lower() in _COMMON_PERSON_NOUNS


def _is_gendered_token(tok) -> bool:
    """성별 어미가 있는 형용사/분사인지 판단한다. ADJ만으로는 부족하다 —
    수동태/완료형 분사("fue abierta", "han sido invitados")는 spaCy가
    VERB+VerbForm=Part로 태깅해서 ADJ 필터에 안 잡힌다. 이 확장은 사물/상황에
    문법적으로 일치하는 분사도 같이 걸러낸다("la puerta fue abierta"의
    "abierta"는 사람이 아니라 puerta에 일치하는 것) — spaCy 품사 태그만으로는
    이 명사가 사람인지 사물인지 구분할 근거가 없다(예: "estudiantes"(사람)와
    "puerta"(사물) 둘 다 그냥 NOUN). 이 프로젝트는 애매하면 과탐지를
    허용하는 쪽이라(놓치는 것보다 낫다는 방침), 사물/상황에 걸린 경우는
    검수자가 "해당없음" 버튼으로 걸러낸다(findings.py의 not_applicable —
    한 번 답하면 같은 단어는 다음부터 자동 추천까지 된다).

    단, amod(명사를 직접 수식)로 걸린 경우만은 예외다 — nsubj(자기 주어가
    있는 서술 형용사, "Ella está cansada" 같은 대화체)는 사람 얘기일 확률이
    압도적으로 높아 과탐지를 감수할 가치가 있지만, amod는 "tiempo
    compartido"처럼 흔한 사물 명사를 수식하는 경우가 실제로 많아 위험 대비
    이득이 낮다. 그래서 amod는 수식받는 명사가 사람일 근거(_modifies_
    plausible_person)가 있을 때만 인정한다."""
    if not tok.morph.get("Gender"):
        return False
    if tok.dep_ == "amod" and not _modifies_plausible_person(tok.head):
        return False
    if tok.pos_ == "ADJ":
        return True
    return tok.pos_ == "VERB" and tok.morph.get("VerbForm") == ["Part"]


def _referent_group_indices(doc) -> tuple[dict, dict]:
    """성별 표시(ADJ+Gender) 토큰마다 몇 번째 그룹(첫 등장 순서 기준)에
    속하는지 {tok.i: group_index}로 매핑하고, 각 그룹의 앵커 토큰 텍스트도
    {group_index: 앵커 단어}로 함께 반환한다(주로 이름/대명사 — "Juan",
    "María", "ella" 등. _referent_key 참고). 앵커 텍스트는 검수 화면에서
    "이 성별 확인이 정확히 누구 얘기인지"를 보여주는 데 쓴다 — "인물 1"/
    "인물 2"라는 무의미한 번호만으로는 검수자가 문장을 직접 읽고 유추해야
    한다.

    _group_gender_words_by_referent와 resolve_gender_groups_in_texts가 이
    매핑을 공유한다 — 같은 텍스트를 두 번 파싱해도(검수 화면에 보여줄 때
    한 번, 검수자가 확정한 성별을 실제로 적용할 때 한 번) 그룹 순서가
    항상 같아야, 나중에 그룹 인덱스로 확정된 성별을 정확히 같은 사람의
    단어에 다시 적용할 수 있다. lemma로 재매칭하면 안 되는 이유: spaCy가
    성별 어미 차이를 하나의 lemma로 정규화한다(cansado/cansada 둘 다 lemma
    "cansado") — 그래서 두 사람이 같은 형용사를 쓰면("Juan está cansado y
    María está cansada") lemma만으로는 두 그룹이 구분이 안 돼 뒤 그룹이
    앞 그룹을 덮어쓰는 사고가 난다."""
    key_to_index: dict = {}
    index_to_anchor: dict = {}
    result: dict = {}
    for tok in doc:
        if not _is_gendered_token(tok):
            continue
        key = _referent_key(tok)
        if key not in key_to_index:
            index = len(key_to_index)
            key_to_index[key] = index
            index_to_anchor[index] = doc[key].text
        result[tok.i] = key_to_index[key]
    return result, index_to_anchor


def _group_gender_words_by_referent(doc) -> List[dict]:
    """성별 표시 단어들을 가리키는 인물(_referent_key)별로 묶는다. 한 줄에
    인물이 하나뿐이면(절대다수의 경우) 그룹이 하나뿐이라 기존 동작과 동일하고,
    둘 이상이면 인물별로 따로 확인해야 한다는 신호가 된다. 반환값은 문장 속
    첫 등장 순서를 유지한
    [{"group_index":.., "referent":.., "words":[...], "lemmas":[...]}, ...]
    — group_index는 리스트 위치와 항상 같은 값이다(리스트 순서 자체가 이미
    식별자라 중복 정보이지만, JSON으로 직렬화되어 DB/프론트를 오가는 동안
    "위치가 곧 식별자"라는 암묵 규약에만 의존하지 않도록 명시해둔다).
    referent는 이 그룹이 가리키는 앵커 단어(이름/대명사 등, _referent_key
    참고) — 검수 화면에 "이게 누구 얘기인지" 보여주는 용도다.
    resolve_gender_groups_in_texts가 확정된 성별을 다시 적용할 때 이
    인덱스로 매칭한다."""
    group_index_by_tok, index_to_anchor = _referent_group_indices(doc)
    groups: List[dict] = []
    for tok in doc:
        idx = group_index_by_tok.get(tok.i)
        if idx is None:
            continue
        while len(groups) <= idx:
            groups.append({
                "group_index": len(groups), "referent": index_to_anchor.get(len(groups)),
                "words": [], "lemmas": [],
            })
        groups[idx]["words"].append(tok.text)
        groups[idx]["lemmas"].append(tok.lemma_.lower())
    return groups


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
    """성별 표시가 걸린 형용사/분사가 1인칭(화자 자신)/2인칭(상대방)/3인칭
    (제3자) 중 무엇을 가리키는지 추정한다 — 확정값이 아니라 검수 화면에
    보여주는 참고용 힌트다(최종 판단은 검수자가 영상/맥락을 보고 직접
    내린다, FlaggedSegmentStepper의 personLabel). 동사에만 기대면 "Tú
    primero"처럼 동사 없는 생략문(대명사가 인칭 정보를 직접 들고 있음)에서
    아무것도 못 찾는다 — 그래서 품사를 가리지 않고 문장 안에서 인칭 정보
    (Person 형태소)를 가진 토큰 중 성별 표시 단어와 토큰 위치가 가장 가까운
    것을 쓴다. 이 "가까운 토큰" 휴리스틱은 진짜 의존 관계가 아니라 위치
    근사치라 틀릴 수 있다 — 예: "Yo vi a María cansada."에서 cansada는
    María를 수식(amod)하지만 María는 스페인어에서 고유명사라 Person
    형태소가 안 붙어 후보에서 빠지고, 유일하게 남은 Yo(1인칭)가 뽑혀
    실제로는 3인칭인데 1인칭으로 나온다. 아무 인칭 정보도 없으면 None."""
    gendered = [tok for tok in doc if _is_gendered_token(tok)]
    person_tokens = [tok for tok in doc if tok.morph.get("Person")]
    if not person_tokens:
        return None
    if gendered:
        anchor = gendered[0]
        closest = min(person_tokens, key=lambda t: abs(t.i - anchor.i))
        return closest.morph.get("Person")[0]
    return person_tokens[0].morph.get("Person")[0]


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
    쓰고, 상충하거나(둘 다 나옴) 아예 없으면 None을 반환해 다음 폴백(영어 힌트,
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
            for tok in doc:
                if not _is_gendered_token(tok):
                    continue
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
    [{"id","text","groups":[{"lemmas":[...], "gender":"male"/"female"}, ...]}].
    반환값은 {id: 수정된 text}.

    groups는 반드시 원래 gender_groups와 같은 순서(첫 등장 순서)로 와야 한다
    — lemma가 아니라 이 순서(그룹 인덱스)로 토큰을 매칭한다.
    _referent_group_indices를 이 텍스트에 다시 적용해 같은 순서로 그룹을
    재구성하기 때문이다. lemma로 매칭하면 안 되는 이유: spaCy가 성별 어미
    차이를 하나의 lemma로 정규화해서(cansado/cansada 둘 다 "cansado"), 두
    사람이 같은 형용사를 쓰면 그룹이 lemma 하나로 충돌해 뒤 그룹이 앞 그룹의
    확정 성별을 덮어쓴다."""
    nlp = _resolve_model(language)
    texts = [i["text"] for i in items]
    docs = nlp.pipe(texts)
    results: dict = {}
    for item, text, doc in zip(items, texts, docs):
        group_index_by_tok, _ = _referent_group_indices(doc)
        groups = item["groups"]
        replacements = []
        for tok in doc:
            group_idx = group_index_by_tok.get(tok.i)
            if group_idx is None or group_idx >= len(groups):
                continue
            target_gender = groups[group_idx].get("gender")
            if target_gender not in _GENDER_TO_MORPH:
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
    "resolved_formality", "resolved_gender_from_korean", "grammatical_person",
    "gender_groups"}. resolved_* 필드는 한국어 원문에서 확정 가능했을 때만
    값이 채워지고, 확정 못 하면 None — 호출자(pipeline.py)가 이후 영어 SRT
    힌트, 그래도 안 되면 사람에게 순서대로 넘긴다. gender_groups는 성별
    표시 단어를 가리키는 인물별로 묶은 목록 — 길이가 2 이상이면 한 줄에
    인물이 여럿이라는 뜻이라, 호출자가 인물별로 따로 확인을 받아야 한다."""
    language = profile.get("language")
    nlp = _resolve_model(language)
    texts = [p.get("target_text", "") for p in pairs]
    docs = nlp.pipe(texts)
    results = []
    for p, doc in zip(pairs, docs):
        gender_groups = _group_gender_words_by_referent(doc)
        results.append({
            "id": p["id"],
            "gender_check_needed": bool(gender_groups),
            "formality_check_needed": _formality_check_needed(doc),
            "resolved_formality": _detect_korean_formality(p.get("korean_text", "")),
            "resolved_gender_from_korean": _detect_korean_gender(p.get("korean_text", "")),
            "grammatical_person": _grammatical_person(doc),
            "gender_words": [w for g in gender_groups for w in g["words"]],
            "gender_word_lemmas": [w for g in gender_groups for w in g["lemmas"]],
            "gender_groups": gender_groups,
        })
    return results
