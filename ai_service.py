"""Separate structured prompts for reports and route recommendations."""
import json
import logging

from google import genai
from google.genai import types

from auth_manager import get_config_value, get_credentials
from domain import (HAZARD_INSTRUCTION, HAZARD_SCHEMA, TRAVEL_INSTRUCTION,
                    eligible_reserves, validate_hazard, validate_recommendations,
                    unknown_hazard)

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self):
        credentials, _ = get_credentials()
        self.client = genai.Client(vertexai=True,
            project=get_config_value("GCP_PROJECT_ID"),
            location=get_config_value("GCP_LOCATION", "global"),
            credentials=credentials, http_options={"timeout": 20000})
        self.model = get_config_value("GEMINI_MODEL_ID", "gemini-3.8-flash")

    def _generate(self, instruction, contents, schema):
        return self.client.models.generate_content(model=self.model, contents=contents,
            config={"system_instruction": instruction, "response_mime_type": "application/json",
                    "response_schema": schema, "temperature": 0.2,
                    "max_output_tokens": 3000,
                    "thinking_config": {"thinking_level": "LOW"}}).text

    def hazard(self, text, image=None):
        parts = [json.dumps({"report": text}, ensure_ascii=False)]
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
        try:
            return validate_hazard(self._generate(HAZARD_INSTRUCTION, parts, HAZARD_SCHEMA)), True
        except Exception:
            logger.exception("hazard_analysis_failed")
            return unknown_hazard(), False

    def recommend(self, text, reserves, vehicle, skill, history):
        candidates = eligible_reserves(reserves, vehicle)
        if not candidates:
            return [], True
        schema = {"type": "OBJECT", "properties": {"recommendations": {"type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {
                "id": {"type": "INTEGER"},
                "reason": {"type": "STRING"}}, "required": ["id", "reason"]}}},
            "required": ["recommendations"]}
        payload = {"question": text, "vehicle": vehicle, "skill": skill,
                   "history": history[-6:], "candidates": candidates,
                   "data_type": "historical_demo"}
        try:
            raw = self._generate(TRAVEL_INSTRUCTION,
                                 json.dumps(payload, default=str, ensure_ascii=False), schema)
            return validate_recommendations(raw, candidates), True
        except Exception:
            logger.exception("recommendation_failed")
            return [], False
