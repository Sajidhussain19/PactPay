"""Structured-output LLM interface.

Agents depend on the `StructuredLLM` protocol, not on a vendor SDK, so tests use a fake and
the provider can change without touching agent code.
"""

import base64
from collections.abc import Sequence
from typing import Protocol, TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The model refused, returned nothing parseable, or the API call failed."""


class StructuredLLM(Protocol):
    async def parse(
        self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()
    ) -> T: ...


class OpenAIStructuredLLM:
    def __init__(
        self, api_key: str, model: str, *, client: AsyncOpenAI | None = None, timeout: float = 90
    ) -> None:
        self._client = client or AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self._model = model

    async def parse(
        self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()
    ) -> T:
        content: list[dict[str, object]] = [{"type": "text", "text": user}]
        content += [
            {"type": "image_url", "image_url": {"url": _data_url(image), "detail": "high"}}
            for image in images
        ]
        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                response_format=schema,
                temperature=0,
            )
        except OpenAIError as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc

        message = completion.choices[0].message
        if message.refusal:
            raise LLMError(f"Model refused: {message.refusal}")
        if message.parsed is None:
            raise LLMError("Model returned no parseable output")
        return message.parsed


def build_llm(settings: Settings) -> StructuredLLM | None:
    api_key = settings.openai_api_key.get_secret_value()
    return OpenAIStructuredLLM(api_key, settings.llm_model) if api_key else None


def _data_url(image: bytes) -> str:
    if image.startswith(b"\x89PNG"):
        mime = "image/png"
    elif image.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise LLMError("Unsupported image format (use PNG, JPEG or WebP)")
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"
