import io
import json
from urllib.request import Request


def test_deepseek_adapter_uses_fixed_json_mode_and_real_usage() -> None:
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    observed: list[Request] = []

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        observed.append(request)
        assert timeout == 60
        return io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 40,
                        "total_tokens": 140,
                        "prompt_cache_hit_tokens": 60,
                        "prompt_cache_miss_tokens": 40,
                    },
                }
            ).encode()
        )

    config = ReportConfig(
        "deepseek-flash",
        "one-shot-v1",
        "0.3",
        "1.2",
        "USD",
        cached_input_price_per_million="0.006",
    )
    response = DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
        [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "Issue"}],
        config,
    )

    assert response.content == "{}"
    assert (response.input_tokens, response.output_tokens, response.total_tokens) == (100, 40, 140)
    assert response.cached_input_tokens == 60
    assert len(observed) == 1
    assert observed[0].full_url == "https://api.deepseek.com/chat/completions"
    assert observed[0].get_header("Authorization") == "Bearer fixed-key"
    assert isinstance(observed[0].data, bytes)
    assert json.loads(observed[0].data) == {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": "Return JSON"},
            {"role": "user", "content": "Issue"},
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 2048,
        "stream": False,
    }


def test_deepseek_adapter_rejects_unexpected_served_model() -> None:
    import pytest
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        return io.BytesIO(
            json.dumps(
                {
                    "model": "another-model",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "prompt_cache_hit_tokens": 0,
                    },
                }
            ).encode()
        )

    with pytest.raises(ValueError, match="unexpected served model"):
        DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
            [{"role": "user", "content": "Issue"}],
            ReportConfig("deepseek-flash", "one-shot-v1", "0.3", "1.2", "USD"),
        )


def test_deepseek_adapter_reports_truncated_structured_output() -> None:
    import pytest
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        return io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "prompt_cache_hit_tokens": 0,
                    },
                }
            ).encode()
        )

    with pytest.raises(ValueError, match="finish_reason=length"):
        DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
            [{"role": "user", "content": "Issue"}],
            ReportConfig("deepseek-flash", "one-shot-v1", "0.3", "1.2", "USD"),
        )


def test_deepseek_adapter_requires_provider_usage() -> None:
    import pytest
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        return io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                }
            ).encode()
        )

    with pytest.raises(ValueError, match="DeepSeek response missing token usage"):
        DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
            [{"role": "user", "content": "Issue"}],
            ReportConfig("deepseek-flash", "one-shot-v1", "0.3", "1.2", "USD"),
        )


def test_deepseek_adapter_rejects_inconsistent_cache_breakdown() -> None:
    import pytest
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        return io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 1,
                        "total_tokens": 101,
                        "prompt_cache_hit_tokens": 60,
                        "prompt_cache_miss_tokens": 20,
                    },
                }
            ).encode()
        )

    with pytest.raises(ValueError, match="DeepSeek cache usage is inconsistent"):
        DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
            [{"role": "user", "content": "Issue"}],
            ReportConfig("deepseek-flash", "one-shot-v1", "0.3", "1.2", "USD"),
        )


def test_deepseek_adapter_diagnoses_missing_json_content() -> None:
    import pytest
    from issuelens.deepseek import DeepSeekChatModel
    from issuelens.report import ReportConfig

    def fixed_response(request: Request, *, timeout: int) -> io.BytesIO:
        return io.BytesIO(json.dumps({
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": None}}],
            "usage": {
                "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1,
            },
        }).encode())

    with pytest.raises(ValueError, match="DeepSeek response missing JSON content"):
        DeepSeekChatModel("fixed-key", urlopen=fixed_response).generate(
            [{"role": "user", "content": "Issue"}],
            ReportConfig("deepseek-flash", "one-shot-v1", "0.3", "1.2", "USD"),
        )
