import base64
import json
import re
from pathlib import Path

from openai import OpenAI

import prompts


BATCH_SIZE = 20
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


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
    request = {"model": model, "messages": message}
    if reasoning:
        request["extra_body"] = {"reasoning": {"effort": reasoning}}

    completion = client.chat.completions.create(**request)
    response = completion.model_dump()

    if write_output:
        Path("output.json").write_text(
            json.dumps(response, indent=4, ensure_ascii=False), encoding="utf-8"
        )

    return response
