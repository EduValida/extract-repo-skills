"""DeepSeek Chat Completions adapter using the compatible OpenAI Python client."""

import os

from .contracts import ValidationError, parse_json


BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def assess(prepared: dict, model: str, *, max_output_tokens: int = 8_000) -> tuple[dict, dict]:
    if not model or not model.strip():
        raise ValidationError("Choose a model with --model or DEEPSEEK_MODEL")
    if max_output_tokens < 1:
        raise ValidationError("max_output_tokens must be positive")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValidationError("Set DEEPSEEK_API_KEY in your environment before analyzing")
    try:
        from openai import APIError, OpenAI
    except ImportError as exc:
        raise ValidationError("Install the LLM extra: uv sync --extra llm") from exc

    request = prepared["request"]
    try:
        with OpenAI(api_key=api_key, base_url=BASE_URL, timeout=60.0, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model,
                messages=request["messages"],
                response_format=request["response_format"],
                max_tokens=max_output_tokens,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
    except APIError as exc:
        # Provider errors can contain submitted content; do not echo their bodies.
        status = getattr(exc, "status_code", None)
        detail = f" (HTTP {status})" if status else ""
        raise ValidationError(f"DeepSeek request failed{detail}: {type(exc).__name__}") from exc
    if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
        raise ValidationError("DeepSeek response did not complete normally; no report was accepted")
    message = response.choices[0].message
    if getattr(message, "refusal", None) or message.tool_calls:
        raise ValidationError("DeepSeek did not return an assessment; no report was accepted")
    if not message.content or not message.content.strip():
        raise ValidationError("DeepSeek returned no assessment text")
    result = parse_json(message.content)
    run = {
        "provider": "deepseek", "base_url": BASE_URL,
        "requested_model": model, "returned_model": response.model,
        "thinking": "disabled", "max_output_tokens": max_output_tokens,
        "response_id": response.id,
        "usage": response.usage.model_dump() if response.usage else None,
        "prompt_version": prepared["prompt_version"], "input_sha256": prepared["input_sha256"],
        "commit": prepared["commit"], "catalog": prepared["report_context"]["catalog"],
        "human_review_required": True,
    }
    return result, run
