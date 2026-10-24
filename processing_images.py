import base64
import json
import os
import re
from pathlib import Path

from openai import OpenAI

import prompts

BATCH_SIZE = 1  # una página por petición
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OUTPUT_JSON = Path(__file__).resolve().parent / "output.json"


def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def page_sort_key(filename):
    """Orden natural de página: última secuencia de dígitos del nombre, y luego el nombre."""
    stem = os.path.splitext(filename)[0]
    numbers = re.findall(r"\d+", stem)
    return (int(numbers[-1]) if numbers else -1, filename)


def image_prep(images_dir: str) -> list[list[str]]:
    images = sorted(
        (name for name in os.listdir(images_dir) if name.endswith(".jpg")),
        key=page_sort_key,
    )

    encoded = []
    for img in images:
        encoded.append(encode_image_to_base64(os.path.join(images_dir, img)))

    return [encoded[i : i + BATCH_SIZE] for i in range(0, len(encoded), BATCH_SIZE)]


def message_build(image_batch: list[str], context: str | None = None) -> list[dict]:
    prompt = prompts.OCR_PROMPT if context is None else prompts.OCR_CONTINUATION_PROMPT
    data = [{"type": "text", "text": prompt}]

    if context is not None:
        data.append({"type": "text", "text": context})

    for img in image_batch:
        data.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})

    return [{"role": "user", "content": data}]


def to_model(
    api_key, model, message, base_url=DEFAULT_BASE_URL, reasoning=None, write_output=False
) -> dict:
    client = OpenAI(api_key=api_key, base_url=base_url)

    extra = {}
    if reasoning:
        extra = {"extra_body": {"reasoning": {"effort": reasoning}}}

    completion = client.chat.completions.create(model=model, messages=message, **extra)
    response = completion.model_dump()

    if write_output:
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=4, ensure_ascii=False)

    return response
