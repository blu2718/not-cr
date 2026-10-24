"""Catálogo de modelos de OpenRouter (vía SDK openai) y helpers de presentación."""

import threading
import time

from openai import OpenAI

# Esfuerzos que se ofrecen cuando un modelo admite razonamiento pero no publica
# su lista (OpenRouter la publica en `reasoning.supported_efforts`; los valores
# posibles son minimal, low, medium, high, xhigh, max y none).
DEFAULT_EFFORTS = ["low", "medium", "high"]
CACHE_TTL = 5 * 60

_cache = None  # (timestamp, list[dict])
_cache_lock = threading.Lock()


def _normalize(raw: dict) -> dict:
    architecture = raw.get("architecture") or {}
    modalities = architecture.get("input_modalities")
    supported = raw.get("supported_parameters")
    reasoning_meta = raw.get("reasoning") or {}

    supports_images = None if modalities is None else "image" in modalities
    supports_reasoning = None if supported is None else "reasoning" in supported

    efforts = reasoning_meta.get("supported_efforts") or raw.get("reasoning_efforts")
    if not efforts and supports_reasoning is True:
        efforts = list(DEFAULT_EFFORTS)

    return {
        "id": raw["id"],
        "name": raw.get("name") or raw["id"],
        "supports_images": supports_images,
        "supports_reasoning": supports_reasoning,
        "reasoning_efforts": list(efforts or []),
        "pricing": raw.get("pricing") or {},
        "context_length": raw.get("context_length"),
    }


def list_models(api_key: str, base_url: str, use_cache: bool = True) -> list[dict]:
    global _cache
    if use_cache:
        with _cache_lock:
            if _cache and time.time() - _cache[0] < CACHE_TTL:
                return _cache[1]

    client = OpenAI(api_key=api_key, base_url=base_url)
    models = [_normalize(m.model_dump()) for m in client.models.list()]
    models.sort(key=lambda m: m["id"])

    with _cache_lock:
        _cache = (time.time(), models)
    return models


def check_key(api_key: str, base_url: str) -> dict:
    """Valida la key contra `GET /key` (el listado de modelos es público y no la valida)."""
    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    response = client.get("/key", cast_to=object)
    return (response or {}).get("data") or {}


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def get_cached_model(model_id: str) -> dict | None:
    with _cache_lock:
        models = _cache[1] if _cache else []
    for model in models:
        if model["id"] == model_id:
            return model
    return None


def resolve_reasoning(info: dict | None, requested: str, default: str) -> str | None:
    """Único sitio donde se decide qué `effort` se envía (None = no enviar nada)."""
    effort = (requested or "").strip() or (default or "").strip()
    if not effort or effort == "off":
        return None
    if info is None:
        return effort
    if info.get("supports_reasoning") is False:
        return None
    efforts = info.get("reasoning_efforts") or []
    if efforts and effort not in efforts:
        return None  # effort obsoleto para este modelo
    return effort


def format_price(per_token) -> str:
    try:
        value = float(per_token)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value == 0:
        return "gratis"
    return f"${value * 1_000_000:.2f} / M tokens"


def format_context(n) -> str:
    if not n:
        return ""
    n = int(n)
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return f"{n // 1_000_000}M tokens"
    if n >= 1000:
        return f"{round(n / 1000)}k tokens"
    return f"{n} tokens"
