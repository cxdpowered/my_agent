"""DeepSeek client wrapper (OpenAI-compatible Chat Completions).

Handles retry policy, request params (thinking mode / reasoning_effort) and
usage extraction. The runtime depends only on the small `complete()` interface,
so tests can inject a fake client with the same signature.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .. import config


@dataclass
class LLMResponse:
    message: dict            # OpenAI assistant message dict (content/reasoning_content/tool_calls)
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    cache: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class LLMError(Exception):
    pass


def _to_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise LLMError("无法将 LLM 响应转换为 dict")


class LLMClient:
    """Wraps an OpenAI-compatible client for DeepSeek thinking mode."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        client: Any = None,
        max_retries: int = config.LLM_MAX_RETRIES,
    ):
        self.model = model or config.DEEPSEEK_MODEL
        self.reasoning_effort = reasoning_effort or config.DEEPSEEK_REASONING_EFFORT
        self.max_tokens = max_tokens or config.LLM_MAX_TOKENS
        self.max_retries = max_retries
        self.base_url = base_url or config.DEEPSEEK_BASE_URL
        self._api_key = api_key
        self._client = client  # may be injected (tests) or lazily created

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        import os

        from openai import OpenAI  # imported lazily so tests don't need it

        key = self._api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise LLMError("缺少 DEEPSEEK_API_KEY 环境变量")
        self._client = OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def _is_retryable(self, exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429 or (isinstance(status, int) and 500 <= status < 600):
            return True
        # network / timeout style errors
        name = type(exc).__name__.lower()
        return "timeout" in name or "connection" in name

    def complete(self, *, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = client.chat.completions.create(**kwargs)
                return self._parse_response(resp)
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001 - classify below
                last_exc = e
                if attempt < self.max_retries and self._is_retryable(e):
                    time.sleep(2 ** attempt)  # 1s, 2s
                    continue
                raise LLMError(f"LLM 调用失败: {e}") from e
        raise LLMError(f"LLM 调用失败: {last_exc}")  # pragma: no cover

    def _parse_response(self, resp: Any) -> LLMResponse:
        data = _to_dict(resp)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM 响应缺少 choices")
        choice = choices[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        cache = {}
        # DeepSeek may return prompt cache hit/miss fields under usage
        for k in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            if k in usage:
                cache[k] = usage[k]
        return LLMResponse(
            message=message,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            cache=cache,
            raw=data,
        )
