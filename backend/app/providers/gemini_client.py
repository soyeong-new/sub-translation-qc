"""Gemini API로 STT(음성인식)와 인물/관계 식별을 수행하는 얇은 SDK 래퍼."""

import json
from typing import List
from google import genai


class GeminiClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._sdk_client = genai.Client(api_key=api_key)

    async def transcribe(self, audio_path: str) -> List[dict]:
        uploaded = self._sdk_client.files.upload(file=audio_path)
        response = await self._sdk_client.aio.models.generate_content(
            model=self._model,
            contents=[
                uploaded,
                "이 오디오의 한국어 발화를 타임코드와 함께 세그먼트로 나눠 받아 적어라.",
            ],
            config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "start": {"type": "NUMBER"},
                            "end": {"type": "NUMBER"},
                            "text": {"type": "STRING"},
                        },
                        "required": ["start", "end", "text"],
                    },
                },
            },
        )
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Gemini STT 응답이 JSON이 아님: {response.text[:200]}") from exc

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        prompt = (
            "다음은 정렬된 자막 세그먼트 목록이다. 등장인물을 식별하고, "
            f"활성화된 체크 항목({profile.get('checks_enabled', {})})에 해당하는 "
            "세그먼트 id를 태깅하라.\n" + json.dumps(pairs, ensure_ascii=False)
        )
        response = await self._sdk_client.aio.models.generate_content(
            model=self._model,
            contents=[prompt],
            config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "OBJECT",
                    "properties": {
                        "characters": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "label": {"type": "STRING"},
                                    "gendered_segment_ids": {
                                        "type": "ARRAY", "items": {"type": "STRING"},
                                    },
                                },
                                "required": ["label", "gendered_segment_ids"],
                            },
                        },
                        "relationships": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "speaker_label": {"type": "STRING"},
                                    "addressee_label": {"type": "STRING"},
                                    "formality_segment_ids": {
                                        "type": "ARRAY", "items": {"type": "STRING"},
                                    },
                                },
                                "required": [
                                    "speaker_label", "addressee_label", "formality_segment_ids",
                                ],
                            },
                        },
                    },
                    "required": ["characters", "relationships"],
                },
            },
        )
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Gemini 인물식별 응답이 JSON이 아님: {response.text[:200]}") from exc
