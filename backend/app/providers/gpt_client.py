"""GPT API로 2차 검증(원문 대조 verify+rewrite)을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from openai import AsyncOpenAI

from app.providers.base import contains_hangul

_JSON_INSTRUCTION = (
    '반드시 {"findings": [...]} 형태의 JSON 객체만 출력하라. 수정이 필요 없는 '
    "세그먼트는 findings에 포함하지 마라. "
    "검토 도중 판단을 바꿔 결국 수정이 필요 없다고 결론 내렸다면, 그 항목은 "
    "findings에서 완전히 빼라 — description에 '다시 검토하니', '재검토 결과' 같은 "
    "번복 과정을 남기지 마라. findings에 포함하는 항목은 처음부터 끝까지 하나의 "
    "최종 결론만 담아야 한다."
)

# 재질문(extra_instruction 있음) 전용 — 위 _JSON_INSTRUCTION의 "빼라" 지시가
# 검수자가 이미 지적한 단건 재검토와 충돌해 빈 응답을 유발하므로, 형식 지시는
# 유지하되 스킵 지시만 "반드시 포함, 판단이 바뀌어도 배열에 남긴 채 결론만
# 갱신"으로 바꿔 끼운다.
_JSON_INSTRUCTION_REQUERY = (
    '반드시 {"findings": [...]} 형태의 JSON 객체만 출력하라. 이 세그먼트는 검수자가 '
    "이미 지적한 것이므로 findings에서 빼는 것은 금지된다 — 검토 도중 판단이 "
    "바뀌더라도 findings에 포함한 채로, description에 '다시 검토하니', '재검토 결과' "
    "같은 번복 과정 없이 하나의 최종 결론만 담아 작성하라."
)

_VERIFY_SCHEMA_INSTRUCTION = (
    "findings 배열의 각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'segment_id (문자열), '
    'category (문자열, 반드시 다음 중 하나: '
    '"sensitivity"(사전에 없어 애매한 비속어), '
    '"mistranslation"(의미가 잘못 옮겨졌거나 함축된 의미가 빠진 경우), '
    '"nuance_tone"(뉘앙스·어조가 원문과 다른 경우), '
    '"unnatural_style"(문법은 맞지만 한국어 구조를 그대로 따라간 직역투·어색한 흐름), '
    '"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현)), '
    "corrected_text (문자열, 최종 교정된 전체 대상언어 텍스트 — 절대 한국어로 "
    "쓰면 안 된다. 아래 '한국어로 써라' 지침은 description 필드에만 적용되고 "
    "corrected_text에는 적용되지 않는다), "
    "description (문자열, 무엇을 왜 그렇게 고쳤는지 한국어로 설명). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라. "
    "description의 설명 문장 자체는 예외 없이 한국어로 써라 — 다른 언어로 "
    "설명하지 마라. corrected_text는 정반대로 한국어를 절대 섞지 말고 대상언어로만 "
    "써라. 단, 대상언어 원문 표현을 예시로 인용하는 것은 괜찮다."
)

_BACK_TRANSLATE_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "korean_text (문자열, text의 자연스러운 한국어 역번역), "
    "original_korean_text (문자열, original_text의 자연스러운 한국어 역번역 "
    "— 검수자가 교정 전 원문이 원래 무슨 뜻이었는지 비교할 수 있게), "
    "is_improvement (불리언, text가 original_text보다 reference_korean의 "
    "의미·톤을 더 잘 살리는 자연스러운 표현이면 true, 동등하거나 "
    "original_text가 더 낫다고 판단되면 false)."
)

_EQUIVALENCE_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "equivalent (불리언, text_a와 text_b가 같은 문제를 같은 방식으로 고친 "
    "것이면 true, 단어 선택이 달라도 무방하다 — 실질적으로 다른 내용·뉘앙스·"
    "해결책이면 false)."
)

_GLOSS_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "meaning (문자열, 그 단어가 context 문장 안에서 무슨 뜻인지 간결한 "
    "한국어로, 1~4단어 정도로)."
)

_FORMALITY_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "corrected_text (문자열, 격식만 반영한 전체 문장 — 이미 일치하면 "
    "원문 그대로)."
)

_GENDER_SWAP_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "has_error (불리언, 문법 오류가 있으면 true)."
)

_DEFAULT_FORMALITY_INSTRUCTION = (
    "informal이면 tú 활용형(2인칭 단수 반말)으로, formal이면 usted 활용형"
    "(3인칭 단수 활용 기반 존댓말)으로."
)

_SCENE_SPLIT_SYSTEM_PREFIX = (
    "다음은 한 영화/영상의 대사 목록이다(korean_text=한국어 원문, "
    "target_text=대상언어 자막, start/end=초 단위 타임코드). 처음부터 끝까지 "
    "순서대로 훑으며, 다음 4가지 기준 중 하나라도 뚜렷하게 나타나는 지점에서 "
    "씬(scene) 경계를 그어라: "
    "1) 화제/주제가 완전히 바뀔 때, "
    "2) 대화에 참여하는 인물 구성이 바뀔 때(새 인물 등장/퇴장), "
    "3) 장면·시간적 흐름이 바뀔 때(특히 타임코드 공백이 3~5초 이상인 지점), "
    "4) 가볍던 분위기가 진지/대립으로(또는 그 반대로) 반전될 때. "
    "모든 대사는 반드시 어느 한 씬에 속해야 하며(빠짐없이), 두 씬에 걸치거나 "
    "겹쳐선 안 되고, 입력 순서를 그대로 유지해야 한다(첫 씬의 start_id는 "
    "입력의 첫 id, 한 씬의 end_id 바로 다음 id가 다음 씬의 start_id)."
)

_SCENE_SPLIT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "scene_split",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_id": {"type": "string"},
                            "end_id": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["start_id", "end_id", "summary"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["scenes"],
            "additionalProperties": False,
        },
    },
}

_FINDINGS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "verify_findings",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["sensitivity", "mistranslation", "nuance_tone",
                                         "unnatural_style", "locale_convention"],
                            },
                            "corrected_text": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["segment_id", "category", "corrected_text", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["findings"],
            "additionalProperties": False,
        },
    },
}

# 재질문(extra_instruction 있음) 전용 — 검수자에게 보이는 역번역도 새
# corrected_text에 맞춰 갱신해야 하므로, 별도 교차모델 API 호출 대신 같은
# 응답에 back_translation 필드를 함께 요청한다.
_FINDINGS_SCHEMA_REQUERY = {
    "type": "json_schema",
    "json_schema": {
        "name": "verify_findings_requery",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["sensitivity", "mistranslation", "nuance_tone",
                                         "unnatural_style", "locale_convention"],
                            },
                            "corrected_text": {"type": "string"},
                            "description": {"type": "string"},
                            "back_translation": {"type": "string"},
                        },
                        "required": ["segment_id", "category", "corrected_text",
                                     "description", "back_translation"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["findings"],
            "additionalProperties": False,
        },
    },
}

_BACK_TRANSLATION_FIELD_INSTRUCTION = (
    "추가로 back_translation (문자열, corrected_text를 자연스러운 한국어로 "
    "역번역 — 대상언어를 모르는 검수자가 교정 결과를 이해할 수 있게) 키도 "
    "반드시 포함하라. back_translation의 문장 자체도 예외 없이 한국어로 써라."
)

_BACK_TRANSLATE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "back_translate",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "korean_text": {"type": "string"},
                            "original_korean_text": {"type": "string"},
                            "is_improvement": {"type": "boolean"},
                        },
                        "required": ["id", "korean_text", "original_korean_text", "is_improvement"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    },
}

_GENDER_RESOLUTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "gender_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "words": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "index": {"type": "integer"},
                                        "is_person": {"type": "boolean"},
                                        "group_id": {"type": "integer"},
                                        "gender": {"type": ["string", "null"], "enum": ["male", "female", None]},
                                        "referent": {"type": ["string", "null"]},
                                        "character_name": {"type": ["string", "null"]},
                                    },
                                    "required": [
                                        "index", "is_person", "group_id", "gender",
                                        "referent", "character_name",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["id", "words"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    },
}

_GENDER_RESOLUTION_SYSTEM_PREFIX = (
    "다음은 대상언어 문장(target_text), 그 문장의 한국어 원문(korean_text), "
    "이미 성별 표시 형태(형용사/분사 어미)가 있는 후보 단어 목록"
    "(candidate_words, 문장 속 등장 순서), 바로 앞뒤 대사"
    "(context_before/context_after, 한국어+대상언어 쌍 최대 2개, 시간순)다. "
    "문장 자체에 단서가 없으면 context를 참고하되, context에 문자 그대로 "
    "없는 이름·사실을 지어내지 마라 — 없으면 없는 대로 둔다.\n\n"
    "각 후보 단어마다 아래 5개 필드를 판단하라. words 배열은 candidate_words와 "
    "정확히 같은 개수·순서여야 하고, 각 원소의 index는 candidate_words에서의 "
    "위치(0부터)와 일치해야 한다.\n\n"
    "(1) is_person: 이 단어 자체가 그 사람의 성별에 따라 철자가 바뀌는 "
    "단어인가(예: profesor/profesora, amigo/amiga처럼 남성형·여성형이 "
    "따로 있는 직업/역할/관계 명사, 성별 일치 형용사·분사). 사람을 "
    "묘사/지칭하는 문장이어도 그 단어 자체는 성별과 무관하게 형태가 "
    "고정된 명사(예: pessoa, persona, víctima, individuo)면 false — "
    "\"사람 얘기인가\"가 아니라 \"이 단어가 성별에 따라 형태가 바뀌는가\"를 "
    "물어라. 사물/추상 개념/상황을 수식하는 성별 어미(관용구·복합명사 속 "
    "형용사 등)도 false. 애매하면(성별에 따라 형태가 바뀌는 단어일 "
    "가능성이 있으면) true로 두고 gender는 null로 남겨 사람에게 확인받게 "
    "하라 — 확실히 형태 고정 명사/사물/추상 개념일 때만 false.\n\n"
    "(2) group_id: 같은 인물을 가리키는 후보끼리만 같은 정수를 써라(문장 "
    "안에서만 의미 있는 임의값). 서로 다른 인물을 같은 group_id로 묶지 마라 "
    "— 복수 지칭(여러 명을 동시에 가리킴) 처리는 (5)를 따르라.\n\n"
    "(3) gender: 근거는 반드시 '이 후보가 가리키는 그 사람 자신'을 직접 "
    "지칭해야 한다 — 문장 속 다른 사람을 가리키는 단어는 이 후보의 근거가 "
    "될 수 없다.\n"
    "  A) 그 사람 자신을 직접 가리키는 단서(호칭, 이름, \"여자/남자/아들/딸\" "
    "같은 지칭어, 이미 성별이 확정된 다른 후보와의 명확한 동일 인물 관계)가 "
    "문장이나 context에 있으면 — 100% 확신이 아니어도 가장 합리적인 "
    "\"male\"/\"female\"을 채워라. \"새끼\"/\"인간\"/\"놈\"/\"년\" 같은 "
    "남녀 공용 속어는 근거로 인정하지 마라.\n"
    "  B) 그런 단서가 문장에도 context에도 없고 \"나\"/\"너\"/\"저\"/\"걔\"/"
    "\"-요·-네\" 같은 성별 무관 표현으로만 지칭되면(문장에 다른 사람 이름이 "
    "나와도 마찬가지 — 그건 다른 사람 얘기다) — 1인칭 고백·감정 표현, "
    "청자에게 하는 명령·평가문이 대표적 함정이다. 감정적 내용이나 통계적 "
    "성별상만으로 추정하지 말고 반드시 null로 남겨라(사람에게 확인받는다).\n\n"
    "(4) referent: 이 그룹이 누구인지 검수자에게 보여줄 짧은 한국어 설명"
    "(예: \"화자 자신\", \"특정 인물의 이름\", \"제3자\", \"듣고 있는 "
    "상대방\").\n\n"
    "(5) character_name: 이 인물의 실제 고유 이름/별명.\n"
    "  - 반드시 korean_text나 context의 한국어 쪽에 문자 그대로 나온 한국어 "
    "표기로 써라 — target_text/context의 대상언어 쪽 로마자·현지어 표기는 "
    "절대 쓰지 마라(다른 언어판과 매칭하는 데 쓰인다).\n"
    "  - 한국어 쪽에 이름이 안 나오고 대상언어 쪽에만 나오면(그 줄의 한국어가 "
    "호칭·대명사뿐이면) null — 대상언어 표기를 한국어로 추측해 지어내지 마라.\n"
    "  - 다른 인물과 겹칠 위험이 있으면 null: 성+직함(\"김씨\", \"김대리\", "
    "\"김 선생님\")처럼 여러 사람에게 쓰이는 표현, \"그녀\"/\"아가씨\"/\"저 "
    "아저씨\" 같은 대명사·일반 호칭.\n"
    "  - 이 후보가 이름이 다른 여러 인물을 동시에 가리키면(복수 지칭) — "
    "그중 한 명의 이름만 대표로 골라 채우지 말고 null.\n"
    "  - \"성경\", \"소진\"처럼 이 작품에서 특정 개인을 명확히 가리키는 "
    "고유한 이름/별명일 때만 채워라. 애매하면 null이 안전하다.\n\n"
    "is_person이 false면 group_id/gender/referent/character_name 값은 무시"
    "되니 아무 값이나 넣어도 된다(단, 스키마상 필드는 항상 채워야 한다)."
)


def _language_label(profile: dict) -> str:
    language = profile.get("language") or "대상언어"
    variant = profile.get("variant")
    return f"{language}({variant})" if variant else language


# findings을 만드는 호출들의 재실행 변동을 줄이기 위한 고정 seed(재현성용,
# temperature를 못 쓰는 모델의 대체 수단) — 값 자체엔 의미 없음.
_SEED = 42


class GptClient:
    def __init__(self, api_key: str, model: str, light_model: str = None, transcribe_model: str = "whisper-1"):
        self._model = model
        self._light_model = light_model or model
        self._transcribe_model = transcribe_model
        self._sdk_client = AsyncOpenAI(api_key=api_key)

    async def _call(self, system: str, user: str, key: str = "findings", label: str = "",
                     model_override: str = None, seed: int = None,
                     response_format: dict = None) -> List[dict]:
        # ponytail: gpt-5.6 계열은 temperature 커스텀 값을 거부한다(400
        # unsupported_value) — 기본값(1)만 허용, Claude와 달리 여기선 조절 불가.
        # 대신 seed로 재실행 시 결과 변동을 줄인다(완벽한 결정성 보장은 아님).
        target_model = model_override or self._model
        kwargs = {"seed": seed} if seed is not None else {}
        response = await self._sdk_client.chat.completions.create(
            model=target_model,
            response_format=response_format or {"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        if not response.choices:
            raise ValueError("GPT 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            parsed = json.loads(text)
            items = parsed[key]
            if not isinstance(items, list):
                raise TypeError(f"{key}가 리스트가 아님")
            return items
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            prefix = f"GPT {label} 응답" if label else "GPT 응답"
            raise ValueError(f"{prefix}이 기대한 JSON 형태가 아님: {preview}") from exc

    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        language_label = _language_label(profile)
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")

        # extra_instruction은 지금 재질문(다시 질문하기, requery.py) 단건 호출만
        # 채워 보낸다 — 배치 검증(pipeline.py)은 항상 빈 문자열이다. 배치용
        # "애매하면 findings에서 빼라" 지시를 재질문에도 그대로 쓰면, 검수자가
        # 이미 콕 집은 줄인데도 모델이 "명백한 문제 아님"으로 판단해 빈
        # 응답을 내고, 그 결과 제안문이 그대로 남는 문제가 있었다(회귀: 사용자
        # 재현 — 재질문해도 반영이 안 됨). 그래서 이 값의 유무로 "애매하면
        # 스킵" vs "이미 지적됐으니 반드시 포함" 두 지시를 통째로 바꿔 끼운다.
        if extra_instruction:
            scope_intro = (
                "이 세그먼트는 검수자가 이미 문제가 있다고 판단해 재검토를 요청한 것이다 — "
                "너 스스로 '문제가 명백한지' 다시 판단해 건너뛰지 말고, 아래 [5단계 체크리스트]에서 "
                "가장 가까운 카테고리를 찾아 검수자 지시사항을 반영한 교정 사항(findings)을 "
                "반드시 작성하라. 이 세그먼트를 findings에서 빼는 것은 금지된다.\n"
                "⚠️ 아래 target_text는 이전 검토에서 이미 한 번 고친 결과물이다 — 네가(또는 다른 "
                "모델이) 만들었다는 이유로 이미 맞다고 안일하게 판단하지 말고, korean_text와 처음부터 "
                "다시 대조해 검수자 지시사항 관점에서 재검토하라.\n\n"
            )
            skip_clean_line = (
                "   - (재질문 예외) 이 세그먼트는 검수자가 이미 지적했으므로, 위 규칙과 달리 "
                "반드시 findings에 포함하라.\n"
            )
        else:
            scope_intro = (
                "각 세그먼트를 먼저 전체적으로 읽고, 명백한 문제가 있다고 확신되는 경우에만 아래 [5단계 체크리스트]에서 해당하는 카테고리를 찾아 교정 사항(findings)을 작성하라. "
                "'혹시 여기도 어느 카테고리 하나쯤 해당되지 않을까' 하는 식으로 5개 카테고리를 억지로 하나씩 끼워 맞추려 하지 마라 — 명백한 문제가 없는 세그먼트는 그냥 건너뛰어라.\n\n"
            )
            skip_clean_line = "   - 수정할 오류가 없는 깨끗한 문장은 절대 응답 배열에 포함하지 마라.\n"

        system = (
            f"너는 한국어-{language_label} 자막의 전문 번역 검수자다. "
            "korean_text(한국어 원문)를 절대 기준(Source of Truth)으로 삼아 target_text(대상언어 번역문)를 검증하라. "
            + scope_intro +
            "⚠️ [우선순위] 아래 규칙들이 서로 충돌하면 이 순서를 따르라: "
            "오역/심의 정확성 > 정보 보존(고유명사·숫자·장소·행동 등 구체적 사실) > 씬 내 반복 표현 일관성 > 자연스러움. "
            "특히 자연스럽게 다듬는 과정에서 원문에 있는 구체적 사실을 생략·변경·추가하면 안 된다 — 단, 이런 사실이 아닌 부연 설명·수식어는 간결하게 줄여도 된다.\n\n"
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
            "     * 직역투로 인해 명백히 어색한 경우 (한국어 구조를 그대로 따라가 대상언어로서 부자연스러운 경우)\n"
            "     * 한국어 원문의 감정·톤(급함, 거침, 간결함, 여유로움 등)이 명확히 다르게 전달된 경우\n"
            "   - 이미 자연스러운 구어체 표현이면 건드리지 마라. 원문의 감정·톤을 정확히 전달하고 있으면 제안하지 마라.\n\n"

            "[5단계 순차 검증 체크리스트]\n"

            "1. 방송/미디어 심의 비속어 검수 (category: \"sensitivity\"):\n"
            "   - 기준: 영상 방영 및 미디어 심의(Broadcasting Rating)상 제재나 경고 대상이 될 수 있는 심한 비속어, 성적·인격모독적 표현이 포함되어 있는가?\n"
            "   - 교정 지침: 대사의 거친 뉘앙스는 유지하되, 방송 심의 기준에 적합한 수위가 약한 비속어나 자연스러운 순화 표현으로 교정(`corrected_text`)하라.\n"
            "2. 오역 및 핵심 의미 누락 (category: \"mistranslation\"):\n"
            "   - 기준: korean_text의 실제 의미와 target_text의 번역 의미가 다르게 와전되었거나, 문장의 핵심 의미가 생략되었는가?\n"
            "   - 교정 지침: 원문의 뜻을 왜곡 없이 정확하게 전달하도록 교정하라.\n"
            "3. 어색한 어조 및 직역투 (category: \"unnatural_style\" 또는 \"nuance_tone\"):\n"
            "   - 기준: 문법은 맞지만 한국어 어순/표현을 그대로 따라간 직역투라 대상언어로서 어색한가? 또는 한국어 원문의 감정·톤이 명확히 다르게 전달되었는가?\n"
            "   - 교정 지침: 원문의 감정·톤을 정확히 살리면서 대상언어권 현지인이 실제로 사용하는 자연스러운 구어체로 교정하라. 자막은 화면과 함께 순간적으로 읽는 매체이니 뜻이 통하는 선에서 최대한 간결하게 써라 — 화면으로 이미 전달되는 정보나 불필요한 부연 설명은 생략하라. 같은 씬 안에서 한국어 원문의 단어/표현이 반복되면, 문법적으로 다르게 써야 할 이유가 없는 한 같은 번역으로 통일하라.\n"
            "   - 주의: 원문이 이미 자연스러운 구어체로 한국어의 감정·톤을 잘 전달하고 있으면 nuance_tone 제안을 하지 마라.\n"
            "4. 문화 맥락 및 로컬라이제이션 (category: \"locale_convention\"):\n"
            "   - 기준: 대상언어권 문화 관습, 관용 표현, 단위 표기(미터법/화폐 등)에 안 맞는 번역이 있는가?\n"
            "   - 교정 지침: 해당 언어권의 문화적 관습과 로컬라이제이션 관례에 맞게 교정하라.\n"
            "5. 이미 반영된 성별/격식 형태 보존:\n"
            "   - 기준: target_text에 이미 특정 성별 어미(대상언어 문법상 형용사·분사·명사 어미)나 격식(존댓말/반말) 형태가 반영되어 있을 수 있다 — 그 형태가 대상언어 사전상 어색하거나 비표준으로 보여도, 검수 과정에서 의도적으로 맞춘 것이니 임의로 '자연스럽게' 되돌리지 마라.\n"
            "   - 교정 지침: 위 1~4번 문제를 고치기 위해 교정문(`corrected_text`)을 작성할 때도, target_text에 이미 있는 성별 어미·격식 형태는 그대로 유지하라 — 오직 그 카테고리의 문제만 고쳐라.\n\n"
            f"⚠️ [자막 형태 및 글자수 절대 제약 - HARD CONSTRAINT]\n"
            f"- 모든 교정문(corrected_text)은 반드시 다음 제약을 엄격히 지켜서 작성하라: {format_constraint}\n"
            "- 각 줄의 글자수를 실제로 세어보고 제약 글자수를 초과하면 절/쉼표 경계에서 자연스럽게 줄바꿈(\\n)을 넣거나 표현을 다듬어 글자수 한도 내로 들어오게 작성하라.\n\n"
            f"참고 지식베이스: {knowledge}\n"
        )

        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        if naturalness_instruction:
            system += f"자연스러움 지침: {naturalness_instruction}\n"
        json_instruction = _JSON_INSTRUCTION_REQUERY if extra_instruction else _JSON_INSTRUCTION
        schema_instruction = _VERIFY_SCHEMA_INSTRUCTION
        if extra_instruction:
            schema_instruction += "\n" + _BACK_TRANSLATION_FIELD_INSTRUCTION
        system += json_instruction + "\n" + schema_instruction
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        response_format = _FINDINGS_SCHEMA_REQUERY if extra_instruction else _FINDINGS_SCHEMA
        results = await self._call(system, user, seed=_SEED, response_format=response_format)
        return await self._retry_hangul_leaks(
            results, pairs, system, language_label, seed=_SEED, response_format=response_format)

    async def _retry_hangul_leaks(self, results: List[dict], pairs: List[dict], system: str,
                                   language_label: str, seed: int, response_format: dict) -> List[dict]:
        """지시(prompt)만으로는 못 막는 사례(design 논의: 배치 처리 중 모델이
        다른 항목의 korean_text를 착각해 corrected_text에 그대로 옮긴 실측
        사례)를 막는 마지막 방어선 — 한국어가 새어나온 항목만 원본 pair를
        다시 보내 한 번 더 묻고, 그래도 안 고쳐지면 검수자가 알아보게
        description에 경고를 남긴다(조용히 버리지 않는다 — 이 프로젝트는
        누락보다 과탐지를 선호한다)."""
        leaked_ids = {r["segment_id"] for r in results if contains_hangul(r.get("corrected_text"))}
        if not leaked_ids:
            return results
        retry_pairs = [p for p in pairs if p["id"] in leaked_ids]
        retry_system = system + (
            f"\n⚠️ 방금 응답에서 다음 segment_id의 corrected_text에 한국어가 섞여 있었다 — "
            f"금지 사항이다. 아래 항목만 다시 교정하되 corrected_text는 반드시 {language_label}"
            f"로만 작성하라 (한국어 단어를 절대 포함하지 마라): {sorted(leaked_ids)}"
        )
        retry_user = json.dumps(retry_pairs, ensure_ascii=False)
        retried = await self._call(retry_system, retry_user, seed=seed, response_format=response_format)
        retried_by_id = {r["segment_id"]: r for r in retried}
        fixed = []
        for r in results:
            if r["segment_id"] not in leaked_ids:
                fixed.append(r)
                continue
            replacement = retried_by_id.get(r["segment_id"], r)
            if contains_hangul(replacement.get("corrected_text")):
                replacement = dict(replacement)
                replacement["description"] = (
                    f"[⚠️ AI가 {language_label} 대신 한국어로 응답함 — 직접 재확인 필요] "
                    f"{replacement['description']}"
                )
            fixed.append(replacement)
        return fixed


    async def back_translate(self, texts: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(reference_korean), 교정 전 {language_label} 원본"
            f"(original_text), 교정 후 {language_label} 제안문(text) 목록이다. "
            "각 항목마다 두 가지를 하라.\n"
            "1. text와 original_text를 각각 자연스러운 한국어로 역번역하라"
            "(korean_text, original_korean_text) — 대상언어를 모르는 검수자가 "
            "교정 전/후 의미를 나란히 비교하기 위한 참고용이므로, 의미뿐 "
            "아니라 톤·뉘앙스(간결함, 거침, 급함, 존중, 여유로움 등)도 함께 "
            "전달하라. 원문이 짧고 직설적이면 역번역도 짧고 직설적으로, "
            "원문에 존댓말·격식이 있으면 그 격식도 살려서 옮겨라 — 단순히 "
            "의미만 통하는 매끄러운 한국어 문장으로 다듬지 마라.\n"
            "2. text가 original_text보다 reference_korean의 의미·톤을 더 잘 "
            f"살리는 자연스러운 {language_label} 표현인지 판단하라"
            "(is_improvement). 의미 왜곡 없이 이미 자연스러운데 단순히 어휘 "
            "취향만 다르다면 개선으로 보지 마라 — 동등하면 false. text가 "
            "아무리 자연스러워도 reference_korean에 있는 구체적 정보(인물·"
            "장소·숫자·행동)를 생략·변경·추가했다면 무조건 false로 판정하라 "
            "— 자연스러움은 정보 보존을 앞설 수 없다.\n"
            + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call(system, user, key="results", label="역번역",
                                 model_override=self._light_model, response_format=_BACK_TRANSLATE_SCHEMA)

    async def check_equivalence(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과, 그걸 {language_label}로 교정한 "
            "두 후보 문구(text_a, text_b) 목록이다. 각 항목마다 text_a와 "
            "text_b가 같은 문제를 같은 방식으로 고친 것인지 판단하라.\n"
            + _EQUIVALENCE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="동등성 확인",
                                 model_override=self._light_model, seed=_SEED)

    async def gloss_words(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 단어(word)와 그 단어가 들어간 문장"
            "(context) 목록이다. 검수자가 그 언어를 몰라서, 각 단어가 이 "
            "문맥에서 무슨 뜻인지 판단하지 못한다 — 간결한 한국어 뜻을 "
            "알려줘라.\n" + _GLOSS_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="단어 뜻풀이", model_override=self._light_model)

    async def apply_formality(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        formality_instruction = profile.get("formality_instruction") or _DEFAULT_FORMALITY_INSTRUCTION
        system = (
            f"다음은 {language_label} 문장(target_text) 목록과 각 문장이 "
            "존댓말(formal)이어야 하는지 반말(informal)이어야 하는지 확정된 "
            "값(formality)이다. 오직 그 문장의 격식(2인칭 대명사·동사 활용)"
            f"만 formality 값에 맞게 바꿔라 — {formality_instruction} "
            "이미 formality와 일치하면 그대로 둬라. 어휘 선택, "
            "의미, 어순 등 격식과 무관한 건 절대 바꾸지 마라 — 오직 격식만 "
            "조정하는 게 유일한 임무다.\n" + _FORMALITY_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="격식 반영", model_override=self._light_model)

    async def verify_gender_swap(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 문장(text) 목록이다. 이 문장들은 확정된 "
            "성별에 맞춰 형용사/분사/명사 어미를 문법 규칙으로 기계적으로 "
            "치환한 직후의 결과다. 각 문장에 그 치환 때문에 생긴 문법 오류"
            "(존재하지 않는 단어, 성별/수 불일치, 어간이 깨진 어형 등)가 "
            "있는지만 판단하라(has_error). 의미가 어색하거나 다른 어휘를 "
            "썼으면 더 나았겠다는 취향 판단은 하지 마라 — 오직 문법적으로 "
            "깨졌는지만 본다.\n" + _GENDER_SWAP_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="성별 치환 검증",
                                 model_override=self._light_model, seed=_SEED)

    async def split_scenes(self, pairs: List[dict], profile: dict) -> List[dict]:
        """씬 분할 전용 콜."""
        language_label = _language_label(profile)
        system = f"{_SCENE_SPLIT_SYSTEM_PREFIX} 대상언어는 {language_label}이다."
        user = json.dumps(pairs, ensure_ascii=False)
        response = await self._sdk_client.chat.completions.create(
            model=self._light_model,
            response_format=_SCENE_SPLIT_SCHEMA,
            seed=_SEED,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if not response.choices:
            raise ValueError("GPT 씬 분할 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            scenes = json.loads(text)["scenes"]
            if not isinstance(scenes, list):
                raise TypeError("scenes가 리스트가 아님")
            return scenes
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 씬 분할 응답이 기대한 JSON 형태가 아님: {preview}") from exc

    async def resolve_gender_from_context(self, items: List[dict], profile: dict) -> List[dict]:
        """성별 문맥 판단 전용 콜."""
        language_label = _language_label(profile)
        system = f"{_GENDER_RESOLUTION_SYSTEM_PREFIX} 대상언어는 {language_label}이다."
        user = json.dumps(items, ensure_ascii=False)
        response = await self._sdk_client.chat.completions.create(
            model=self._light_model,
            response_format=_GENDER_RESOLUTION_SCHEMA,
            seed=_SEED,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if not response.choices:
            raise ValueError("GPT 성별 문맥 판단 응답이 비어 있음")
        text = response.choices[0].message.content
        try:
            results = json.loads(text)["results"]
            if not isinstance(results, list):
                raise TypeError("results가 리스트가 아님")
            return results
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            preview = text[:200] if text else "<empty>"
            raise ValueError(f"GPT 성별 문맥 판단 응답이 기대한 JSON 형태가 아님: {preview}") from exc


    async def transcribe(self, audio_path: str) -> List[dict]:
        """audio_path 하나의 STT 결과를 단어 단위 타임코드 리스트로 반환한다
        (문장 단위 segment가 아니라 word). alignment.align()이 대상언어 SRT
        큐의 시간 구간 안에 들어오는 단어들을 직접 모아 그 큐의 한국어
        텍스트를 만들기 때문이다 — 위스퍼가 알아서 문장을 끊는 경계(침묵
        기준)와 SRT 큐 경계(사람이 손으로 자른 것)가 서로 달라 생기던
        "영상/한국어원문/대상언어가 안 맞는" 문제를, 애초에 문장 단위로
        정렬하지 않음으로써 해소한다. 반환 형태는 기존 segment 형태와 동일한
        {"start","end","text"} 딕셔너리라 호출자(_transcribe_in_chunks의
        오프셋 보정 등)는 세그먼트든 단어든 그대로 다룰 수 있다.

        호출자(pipeline._transcribe_in_chunks)가 긴 오디오를 여러 조각으로
        나눠 이 메서드를 조각당 한 번씩 호출하므로, 단어가 하나도 없는 것
        (무음 구간, 엔드크레딧 등)은 그 조각만 보면 지극히 정상이다 — 여기서
        실패로 처리하지 않고 빈 리스트를 반환한다. "에피소드 전체에 대사가
        없음"이라는 진짜 실패 판단은 모든 조각을 병합한 뒤 호출자 쪽에서
        한다."""
        with open(audio_path, "rb") as f:
            response = await self._sdk_client.audio.transcriptions.create(
                model=self._transcribe_model, file=f, language="ko",
                response_format="verbose_json", timestamp_granularities=["word"],
            )
        if not response.words:
            return []
        return [{"start": w.start, "end": w.end, "text": w.word}
                for w in response.words]

    async def get_embeddings(self, texts: List[str], model: str = "text-embedding-3-small") -> List[List[float]]:
        """OpenAI text-embedding-3-small 모델로 문장 배치 임베딩 벡터 목록을 반환한다."""
        if not texts:
            return []
        response = await self._sdk_client.embeddings.create(
            input=texts,
            model=model,
        )
        return [item.embedding for item in response.data]

