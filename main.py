import json
import shutil
import tempfile
from pathlib import Path

import ocr
import processing_text


CONFIG_PATH = Path("config.json")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def run_ai_ocr(
    pdf_path,
    api_key,
    model,
    base_url=None,
    reasoning=None,
    on_chunk=None,
    on_event=None,
    output_name=None,
) -> None:
    image_dir = tempfile.mkdtemp(prefix="not-cr-")
    try:
        kwargs = {"on_chunk": on_chunk}
        if base_url is not None:
            kwargs["base_url"] = base_url
        if reasoning is not None:
            kwargs["reasoning"] = reasoning
        if on_event is not None:
            kwargs["on_event"] = on_event

        content = ocr.process_pdf(pdf_path, image_dir, api_key, model, **kwargs)
        if on_event is not None:
            on_event("log", "└ Guardando a Markdown")
            on_event("status", {"step": "saving"})
        processing_text.to_md(content, output_name or Path(pdf_path).stem)
    finally:
        shutil.rmtree(image_dir, ignore_errors=True)
