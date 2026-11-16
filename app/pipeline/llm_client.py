"""Thin wrapper around the OpenAI API.

Every failure mode (bad key, no quota, timeout, network, malformed JSON) is
converted into one `LLMError` with a plain-language message, so the pipeline
can fall back to rule-based results instead of crashing.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """AI step failed. The message is safe to show to the user."""


class LLMClient(Protocol):
    model_name: str

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict: ...


class OpenAIClient:
    def __init__(self, settings: Settings):
        # Imported here so the app (and tests) can start even if openai isn't configured.
        import openai

        self._openai = openai
        self._client = openai.OpenAI(
            api_key=settings.openai_api_key.strip(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,  # SDK retries 429/5xx with backoff
        )
        self.model_name = settings.openai_model
        self._temperature = settings.openai_temperature

    def _create(self, messages: list[dict], with_temperature: bool):
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},  # forces valid JSON output
        }
        if with_temperature and self._temperature is not None:
            kwargs["temperature"] = self._temperature
        return self._client.chat.completions.create(**kwargs)

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        o = self._openai
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            try:
                response = self._create(messages, with_temperature=True)
            except o.BadRequestError as exc:
                # Some models reject custom temperature values. Retry once without it.
                if "temperature" in str(exc).lower() and self._temperature is not None:
                    logger.warning("Model %s rejected temperature; retrying without it.", self.model_name)
                    response = self._create(messages, with_temperature=False)
                else:
                    raise
        except o.AuthenticationError as exc:
            raise LLMError("OpenAI rejected the API key. Check OPENAI_API_KEY in your .env file.") from exc
        except o.PermissionDeniedError as exc:
            raise LLMError(f"This API key can't use the model '{self.model_name}'.") from exc
        except o.NotFoundError as exc:
            raise LLMError(f"Model '{self.model_name}' was not found. Check OPENAI_MODEL.") from exc
        except o.RateLimitError as exc:
            raise LLMError("OpenAI rate limit or quota reached. Try again shortly or check billing.") from exc
        except o.APITimeoutError as exc:
            raise LLMError("OpenAI took too long to respond.") from exc
        except o.APIConnectionError as exc:
            raise LLMError("Couldn't reach OpenAI. Check your internet connection.") from exc
        except o.APIError as exc:
            logger.exception("OpenAI API error")
            raise LLMError("OpenAI returned an error.") from exc

        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice and choice.message else None
        if not content:
            raise LLMError("OpenAI returned an empty response.")
        if choice.finish_reason == "length":
            raise LLMError("OpenAI response was cut off.")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("OpenAI returned malformed JSON.") from exc
        if not isinstance(data, dict):
            raise LLMError("OpenAI returned an unexpected format.")
        return data


def build_llm_client(settings: Settings) -> LLMClient | None:
    if not settings.llm_enabled:
        logger.warning("OPENAI_API_KEY is not set: running in rule-based mode (no AI).")
        return None
    return OpenAIClient(settings)
