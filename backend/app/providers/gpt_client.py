"""GPT API로 2차 검증(원문 대조 verify+rewrite)을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from openai import AsyncOpenAI

_JSON_INSTRUCTION = (
    '반드시 {"findings": [...]} 형태의 JSON 객체만 출력하라. 수정이 필요 없는 '
    "세그먼트는 findings에 포함하지 마라."
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
    "corrected_text (문자열, 최종 교정된 전체 대상언어 텍스트), "
    "original_meaning (문자열, 지금 target_text(수정 전 원문)가 실제로 무슨 "
    "뜻인지 한국어로 간단히 설명 — \"~라는 뜻이다\"처럼. 대상언어를 모르는 "
    "검수자가 이 문장만 보고 원문이 뭘 말하는지 바로 알 수 있어야 한다), "
    "description (문자열, 무엇을 왜 그렇게 고쳤는지 한국어로 설명). "
    "이 키 이름을 정확히 그대로 사용하라 — 다른 이름이나 추가 키를 쓰지 마라. "
    "original_meaning과 description의 설명 문장 자체는 예외 없이 한국어로 "
    "써라 — 다른 언어로 설명하지 마라. 단, 대상언어 원문 표현을 예시로 "
    "인용하는 것은 괜찮다(예: \"'경비아저씨' 표현이 어색해 'el guardia'로 수정\")."
)

_BACK_TRANSLATE_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'id (문자열, 입력의 "id"와 반드시 일치), '
    "korean_text (문자열, 자연스러운 한국어 역번역)."
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
                                    },
                                    "required": ["index", "is_person", "group_id", "gender", "referent"],
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
    "다음은 스페인어 문장(target_text), 그 문장의 한국어 원문(korean_text), "
    "그리고 그 문장에서 이미 성별 표시 형태(형용사/분사 어미)가 있는 후보 "
    "단어 목록(candidate_words, 문장 속 등장 순서)이다. 각 후보 단어마다 "
    "다음을 판단하라: "
    "(1) is_person — 이 단어가 실제로 특정 인물을 묘사하는가(사람이 "
    "아니라 사물/추상 개념/상황을 가리키면 false, 예: \"tiempo "
    "compartido\"의 \"compartido\"). 애매하면(사람 얘기일 가능성이 있으면) "
    "true로 두고 gender를 null로 남겨 사람에게 확인받게 하라 — 확실히 "
    "사물/추상 개념일 때만 false로 하라. "
    "(2) group_id — 같은 인물을 가리키는 후보 단어들은 같은 정수를 써라 "
    "(문장 안에서만 의미 있는 임의의 값). "
    "(3) gender — \"male\" 또는 \"female\", 문맥(한국어 원문의 호칭·대명사, "
    "스페인어 문장의 주어/목적어 관계 등)으로 확신할 수 있을 때만 채우고, "
    "애매하면 null로 남겨라(사람에게 물어본다 — 억지로 추측하지 마라). "
    "(4) referent — 이 그룹이 누구를 가리키는지 검수자에게 보여줄 짧은 "
    "한국어 설명(예: \"화자 자신\", \"Juan\", \"제3자\", \"듣고 있는 "
    "상대방\"). is_person이 false면 group_id/gender/referent 값은 "
    "무시되니 아무 값이나 넣어도 된다(단, 스키마상 필드는 항상 채워야 "
    "한다). words 배열은 candidate_words와 정확히 같은 개수·순서로, "
    "각 원소의 index는 candidate_words에서의 위치(0부터)와 일치해야 한다."
)


def _language_label(profile: dict) -> str:
    language = profile.get("language") or "대상언어"
    variant = profile.get("variant")
    return f"{language}({variant})" if variant else language


class GptClient:
    def __init__(self, api_key: str, model: str, transcribe_model: str = "whisper-1"):
        self._model = model
        self._transcribe_model = transcribe_model
        self._sdk_client = AsyncOpenAI(api_key=api_key)

    async def _call(self, system: str, user: str, key: str = "findings", label: str = "") -> List[dict]:
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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
        """correct_primary(Claude)와 대칭적으로 원본을 처음부터 독립적으로
        검토한다. Claude가 이 텍스트를 이미 봤는지, 뭘 고쳤는지는 전혀 모른다
        — 이전 교정 여부를 알려주면 앵커링 편향으로 독립적 재판단보다 그냥
        승인하는 쪽으로 기울어 정확도가 떨어진다(파이프라인이 두 모델의
        일치/불일치를 이후 단계에서 병합해 신뢰도 신호로 쓴다)."""
        language_label = _language_label(profile)
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")

        system = (
            f"너는 한국어-{language_label} 자막의 검증자다. korean_text(원문)와 "
            "target_text를 나란히 놓고 다음 기준으로 처음부터 독립적으로 검증·"
            "교정하라: 사전에 없는 애매한 비속어, 번역정확성, 문화맥락, 뉘앙스어조, "
            "자연스러운흐름(직역 지양, 한국어 어순을 그대로 따라간 부분을 찾아 "
            "고칠 것), 함축의미, 로컬라이제이션(그 나라 문화에 맞는 표현인지). "
            "성별 표시 형용사/분사의 형태나 격식(존댓말·반말)은 이미 앞 단계"
            "(파이썬의 결정론적 문법 반영 + 격식 전담 호출)에서 확정되어 "
            "target_text에 반영된 상태다 — 이 값을 판단하거나 새로 바꾸는 "
            "건 네 역할이 아니다. 다른 문제(오역/뉘앙스/직역투 등)를 고칠 "
            "때 이미 반영된 성별 형태와 격식을 절대 건드리지 마라(그대로 "
            "유지). 새 인물 이름의 표기 통일도 다루지 마라(별도 사전으로 "
            "관리). "
            f"{format_constraint} 참고 지식베이스: {knowledge}\n"
        )
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        if naturalness_instruction:
            system += f"자연스러움 지침: {naturalness_instruction}\n"
        system += _JSON_INSTRUCTION + "\n" + _VERIFY_SCHEMA_INSTRUCTION
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        return await self._call(system, user)

    async def back_translate(self, texts: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 텍스트 목록이다. 각 항목을 자연스러운 "
            "한국어로 역번역하라 — 스페인어를 모르는 검수자가 원래 의미를 "
            "가늠하기 위한 참고용이므로, 의역보다 원문 의미를 최대한 그대로 "
            "전달하는 것을 우선하라.\n" + _BACK_TRANSLATE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(texts, ensure_ascii=False)
        return await self._call(system, user, key="results", label="역번역")

    async def check_equivalence(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과, 그걸 {language_label}로 교정한 "
            "두 후보 문구(text_a, text_b) 목록이다. 각 항목마다 text_a와 "
            "text_b가 같은 문제를 같은 방식으로 고친 것인지 판단하라.\n"
            + _EQUIVALENCE_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="동등성 확인")

    async def gloss_words(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 단어(word)와 그 단어가 들어간 문장"
            "(context) 목록이다. 검수자가 그 언어를 몰라서, 각 단어가 이 "
            "문맥에서 무슨 뜻인지 판단하지 못한다 — 간결한 한국어 뜻을 "
            "알려줘라.\n" + _GLOSS_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="단어 뜻풀이")

    async def apply_formality(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 {language_label} 문장(target_text) 목록과 각 문장이 "
            "존댓말(formal)이어야 하는지 반말(informal)이어야 하는지 확정된 "
            "값(formality)이다. 오직 그 문장의 격식(2인칭 대명사·동사 활용)"
            "만 formality 값에 맞게 바꿔라 — informal이면 tú 활용형(2인칭 "
            "단수 반말)으로, formal이면 usted 활용형(3인칭 단수 활용 기반 "
            "존댓말)으로. 이미 formality와 일치하면 그대로 둬라. 어휘 선택, "
            "의미, 어순 등 격식과 무관한 건 절대 바꾸지 마라 — 오직 격식만 "
            "조정하는 게 유일한 임무다.\n" + _FORMALITY_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="격식 반영")

    async def split_scenes(self, pairs: List[dict], profile: dict) -> List[dict]:
        """씬 분할 전용 콜. json_schema strict 모드로 출력 형식을 API 레벨에서
        강제한다 — correct_primary/verify_and_refine이 쓰는 json_object 모드보다
        한 단계 더 강한 보장이다(스키마를 벗어난 키/누락이 애초에 생성되지
        않음). Claude와 교차검증하지 않는 이유는 씬 경계 판단이 두 모델의
        일치 여부로 신뢰도를 매길 "정답 있는 판정"이 아니라 기계적 전처리라서다
        (호출자가 경계 유효성 자체는 별도로 검증한다)."""
        language_label = _language_label(profile)
        system = f"{_SCENE_SPLIT_SYSTEM_PREFIX} 대상언어는 {language_label}이다."
        user = json.dumps(pairs, ensure_ascii=False)
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format=_SCENE_SPLIT_SCHEMA,
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
        """성별 문맥 판단 전용 콜. split_scenes와 동일하게 json_schema strict
        모드로 출력 형식을 API 레벨에서 강제한다 — 후보 단어 개수만큼 정확히
        구조화된 배열이 필요해서, correct_primary/verify_and_refine이 쓰는
        느슨한 json_object 모드보다 이 형태가 더 안전하다."""
        language_label = _language_label(profile)
        system = f"{_GENDER_RESOLUTION_SYSTEM_PREFIX} 스페인어는 {language_label}이다."
        user = json.dumps(items, ensure_ascii=False)
        response = await self._sdk_client.chat.completions.create(
            model=self._model,
            response_format=_GENDER_RESOLUTION_SCHEMA,
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

