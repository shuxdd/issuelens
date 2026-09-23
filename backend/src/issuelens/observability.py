import re
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Any


def _record_degraded(result: dict[str, Any], stage: str, error: Exception) -> bool:
    previous = result["events"][-1]
    sequence = len(result["events"]) + 1
    result["events"].append({
        "kind": "observation",
        "status": "degraded",
        "failure_stage": stage,
        "error_type": type(error).__name__,
        "sequence": sequence,
        "run_id": result["run_id"],
        "trace_id": result["trace_id"],
        "event_id": sha256(
            f"{result['trace_id']}:{sequence}:observation".encode()
        ).hexdigest()[:16],
        "parent_id": result["trace_id"],
        "started_at": previous["finished_at"],
        "finished_at": previous["finished_at"],
        "duration_ns": 0,
    })
    return False


def _sanitize(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            sensitive_key = re.search(
                r"api[_-]?key|authorization|secret|password|cookie|(^|_)token$",
                str(key),
                re.IGNORECASE,
            )
            sanitized[key] = "[REDACTED]" if sensitive_key else _sanitize(item, secrets)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item, secrets) for item in value)
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", value)
        return re.sub(
            r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            value,
        )
    return value


def record_evaluation_score(
    result: dict[str, Any],
    *,
    experiment_run_id: str,
    name: str,
    value: float,
    evaluator_version: str,
) -> dict[str, Any]:
    if (
        not experiment_run_id
        or not name
        or not evaluator_version
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise ValueError("evaluation score requires identifiers and a numeric value")
    reported = next(
        event
        for event in reversed(result["events"])
        if event["kind"] == "stage" and event["stage"] == "reported"
    )
    sequence = len(result["events"]) + 1
    event = {
        "kind": "score",
        "name": name,
        "value": float(value),
        "experiment_run_id": experiment_run_id,
        "evaluator_version": evaluator_version,
        "sequence": sequence,
        "run_id": result["run_id"],
        "trace_id": result["trace_id"],
        "event_id": sha256(
            f"{result['trace_id']}:{sequence}:score:{name}".encode()
        ).hexdigest()[:16],
        "parent_id": reported["event_id"],
        "started_at": reported["finished_at"],
        "finished_at": reported["finished_at"],
        "duration_ns": 0,
    }
    result["events"].append(event)
    return event


def _send_events(client: Any, result: dict[str, Any], secrets: tuple[str, ...]) -> None:
    root = client.start_observation(
        name="investigation",
        as_type="agent",
        trace_context={"trace_id": result["trace_id"]},
        metadata=_sanitize(
            {"run_id": result["run_id"], "case_id": result["case_id"]}, secrets
        ),
    )
    stages: dict[str, Any] = {}
    for event in result["events"]:
        if event["kind"] == "stage":
            stages[event["stage"]] = root.start_observation(
                name=event["stage"],
                as_type="span",
                metadata=_sanitize({
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "duration_ns": event["duration_ns"],
                }, secrets),
            )
    children = []
    for event in result["events"]:
        if event["kind"] == "tool":
            children.append(stages["retrieved"].start_observation(
                name=event["tool"],
                as_type="tool",
                metadata=_sanitize({
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "status": event["status"],
                    "duration_ns": event["duration_ns"],
                }, secrets),
            ))
        elif event["kind"] == "model":
            children.append(stages["hypotheses_formed"].start_observation(
                name="model",
                as_type="generation",
                model=_sanitize(event["model"], secrets),
                usage_details={
                    "input": event["input_tokens"],
                    "output": event["output_tokens"],
                    "total": event["total_tokens"],
                },
                cost_details={"total": float(event["cost"])},
                metadata=_sanitize({
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "prompt_version": event["prompt_version"],
                    "duration_ns": event["duration_ns"],
                }, secrets),
            ))
    for child in children:
        child.end()
    for stage in stages.values():
        stage.end()
    root.end()
    for event in result["events"]:
        if event["kind"] == "score":
            client.create_score(
                name=event["name"],
                value=event["value"],
                trace_id=event["trace_id"],
                dataset_run_id=event["experiment_run_id"],
                score_id=event["event_id"],
                data_type="NUMERIC",
                metadata=_sanitize(
                    {"evaluator_version": event["evaluator_version"]}, secrets
                ),
            )


def export_to_langfuse(
    result: dict[str, Any],
    *,
    environ: Mapping[str, str],
    client_factory: Callable[..., Any] | None = None,
) -> bool:
    if environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        return False
    try:
        if client_factory is None:
            from langfuse import Langfuse

            if not environ.get("LANGFUSE_PUBLIC_KEY") or not environ.get(
                "LANGFUSE_SECRET_KEY"
            ):
                raise ValueError("Langfuse credentials are required when enabled")
            client_factory = Langfuse
        client = client_factory(
            public_key=environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=environ.get("LANGFUSE_SECRET_KEY"),
            base_url=environ.get("LANGFUSE_BASE_URL") or environ.get("LANGFUSE_HOST"),
        )
    except Exception as error:
        return _record_degraded(result, "initialization", error)
    try:
        secrets = tuple(
            value
            for name in ("LANGFUSE_SECRET_KEY",)
            if (value := environ.get(name))
        )
        _send_events(client, result, secrets)
    except Exception as error:
        return _record_degraded(result, "send", error)
    try:
        client.flush()
    except Exception as error:
        return _record_degraded(result, "flush", error)
    return True
