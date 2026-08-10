import math
import time

from openai import OpenAI


REASONING_EFFORTS = ["low", "medium", "high"]
CACHE_TTL = 5 * 60
MODEL_CACHE: dict[str, tuple[float, list[dict]]] = {}


def list_models(api_key: str, base_url: str) -> list[dict]:
    now = time.monotonic()
    cached = MODEL_CACHE.get(base_url)
    if cached is not None and now - cached[0] < CACHE_TTL:
        return cached[1]

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.models.list()
    normalized = []
    for model in response.data:
        raw = model.model_dump() if hasattr(model, "model_dump") else dict(model)
        model_id = raw.get("id")
        if not model_id:
            continue

        item = {"id": model_id, "name": raw.get("name") or model_id}
        if raw.get("pricing") is not None:
            item["pricing"] = raw["pricing"]

        if raw.get("context_length") is not None:
            try:
                item["context_length"] = int(raw["context_length"])
            except (TypeError, ValueError):
                pass

        if "supported_parameters" in raw:
            supported = raw["supported_parameters"] or []
            item["supports_reasoning"] = "reasoning" in supported

        normalized.append(item)

    normalized.sort(key=lambda model: model["id"])
    MODEL_CACHE[base_url] = (now, normalized)
    return normalized


def format_price(per_token: str | float | None) -> str:
    try:
        price = float(per_token)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(price):
        return ""
    if price == 0:
        return "gratis"
    return f"${price * 1_000_000:.2f} / M tokens"


def format_context(n: int | None) -> str:
    if n is None:
        return ""
    try:
        context = int(n)
    except (TypeError, ValueError):
        return ""

    if context >= 1_000_000:
        value = context / 1_000_000
        suffix = "m"
    elif context >= 1_000:
        value = context / 1_000
        suffix = "k"
    else:
        return f"{context} tokens"

    formatted = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{formatted}{suffix} tokens"
