import base64
import json
import re
from pathlib import Path

from openai import OpenAI

import prompts


BATCH_SIZE = 20
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _is_opencode(base_url: str) -> bool:
    return "opencode.ai" in base_url.lower()


def _uses_responses_api(base_url: str, model: str) -> bool:
    if not _is_opencode(base_url):
        return False
    model_id = model.lower().rsplit("/", 1)[-1]
    if "/go/" in base_url.lower():
        return model_id.startswith("gpt-")
    return model_id.startswith(("gpt-", "grok-"))


def _opencode_chat_supports_effort(model: str) -> bool:
    model_id = model.lower().rsplit("/", 1)[-1]
    if model_id.startswith("grok-3-mini"):
        return True
    if any(
        family in model_id
        for family in ("deepseek", "minimax", "mimo", "kimi", "qwen", "big-pickle", "glm")
    ):
        return "glm-5.2" in model_id or "glm-5-2" in model_id or "glm-5p2" in model_id
    return True


def _responses_input(message: list[dict]) -> list[dict]:
    converted = []
    for item in message:
        content = item.get("content", "")
        if isinstance(content, str):
            content = [{"type": "input_text", "text": content}]
        else:
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url")
                    if image_url:
                        parts.append({"type": "input_image", "image_url": image_url})
            content = parts
        converted.append({"role": item.get("role", "user"), "content": content})
    return converted


def _responses_to_chat(completion) -> dict:
    raw = completion.model_dump()
    text = getattr(completion, "output_text", None)
    if not text:
        text_parts = []
        for output in raw.get("output", []):
            for part in output.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    text_parts.append(part["text"])
        text = "".join(text_parts)
    if not text:
        raise ValueError("El modelo no devolvió contenido de texto.")

    usage = raw.get("usage")
    if usage:
        usage = dict(usage)
        if "input_tokens" in usage:
            usage.setdefault("prompt_tokens", usage["input_tokens"])
        if "output_tokens" in usage:
            usage.setdefault("completion_tokens", usage["output_tokens"])
        if "total_tokens" not in usage and "prompt_tokens" in usage and "completion_tokens" in usage:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

    response = {
        "id": raw.get("id"),
        "object": "chat.completion",
        "created": raw.get("created"),
        "model": raw.get("model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    if usage:
        response["usage"] = usage
    return response


def encode_image_to_base64(image_path: str | Path) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")


def image_prep(images_dir: str) -> list[list[str]]:
    directory = Path(images_dir)
    images = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".jpg"]

    def page_key(path: Path) -> tuple[int, str]:
        matches = re.findall(r"\d+", path.stem)
        return (int(matches[-1]) if matches else float("inf"), path.name)

    encoded = [encode_image_to_base64(path) for path in sorted(images, key=page_key)]
    return [encoded[index : index + BATCH_SIZE] for index in range(0, len(encoded), BATCH_SIZE)]


def message_build(image_batch: list[str], context: str | None = None) -> list[dict]:
    prompt = prompts.OCR_PROMPT if context is None else prompts.OCR_CONTINUATION_PROMPT
    data = [{"type": "text", "text": prompt}]

    if context is not None:
        data.append({"type": "text", "text": context})

    for image in image_batch:
        data.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image}"},
            }
        )

    return [{"role": "user", "content": data}]


def to_model(
    api_key,
    model,
    message,
    base_url=DEFAULT_BASE_URL,
    reasoning=None,
    write_output=False,
) -> dict:
    client = OpenAI(api_key=api_key, base_url=base_url)

    if _uses_responses_api(base_url, model):
        request = {"model": model, "input": _responses_input(message)}
        if reasoning:
            request["reasoning"] = {"effort": reasoning}
        try:
            completion = client.responses.create(**request)
        except Exception as exc:
            if not reasoning or "reasoning" not in str(exc).lower():
                raise
            request.pop("reasoning", None)
            completion = client.responses.create(**request)
        response = _responses_to_chat(completion)
    else:
        request = {"model": model, "messages": message}
        if reasoning and _is_opencode(base_url):
            if _opencode_chat_supports_effort(model):
                request["reasoning_effort"] = reasoning
        elif reasoning:
            request["extra_body"] = {"reasoning": {"effort": reasoning}}
        try:
            completion = client.chat.completions.create(**request)
        except Exception as exc:
            message_text = str(exc).lower()
            if not reasoning or not _is_opencode(base_url) or not any(
                marker in message_text for marker in ("reasoning", "reasoning_effort", "extra inputs")
            ):
                raise
            request.pop("reasoning_effort", None)
            completion = client.chat.completions.create(**request)
        response = completion.model_dump()

    if write_output:
        Path("output.json").write_text(
            json.dumps(response, indent=4, ensure_ascii=False), encoding="utf-8"
        )

    return response
