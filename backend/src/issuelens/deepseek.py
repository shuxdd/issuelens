import json
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen

from issuelens.report import ModelResponse, ReportConfig


class DeepSeekChatModel:
    def __init__(self, api_key: str, *, urlopen: Callable[..., Any] = urlopen) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.api_key = api_key
        self.urlopen = urlopen

    def generate(self, messages: list[dict[str, str]], config: ReportConfig) -> ModelResponse:
        payload = json.dumps(
            {
                "model": config.model,
                "messages": messages,
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
                "max_tokens": 2048,
                "stream": False,
            }
        ).encode("utf-8")
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
        if result.get("model") != config.model:
            raise ValueError("DeepSeek returned an unexpected served model")
        finish_reason = result["choices"][0]["finish_reason"]
        if finish_reason != "stop":
            raise ValueError(f"DeepSeek structured output finish_reason={finish_reason}")
        content = result["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("DeepSeek response missing JSON content")
        usage = result.get("usage")
        if not isinstance(usage, dict) or any(
            field not in usage
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )
        ):
            raise ValueError("DeepSeek response missing token usage")
        if (
            usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
            != usage["prompt_tokens"]
        ):
            raise ValueError("DeepSeek cache usage is inconsistent")
        return ModelResponse(
            content,
            usage["prompt_tokens"],
            usage["completion_tokens"],
            usage["total_tokens"],
            usage["prompt_cache_hit_tokens"],
        )
