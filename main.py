import json
import shutil
import tempfile
from pathlib import Path

import ocr
import processing_text

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


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
    if on_event:
        on_event("log", f"[ Procesando {Path(pdf_path).name} ]")

    image_dir = tempfile.mkdtemp(prefix="not-cr-")

    extra = {}
    if base_url is not None:
        extra["base_url"] = base_url
    if reasoning is not None:
        extra["reasoning"] = reasoning
    if on_event is not None:
        extra["on_event"] = on_event

    try:
        contenido = ocr.process_pdf(
            pdf_path, image_dir, api_key, model, on_chunk=on_chunk, **extra
        )

        if on_event:
            on_event("log", "└ Guardando a Markdown")
            on_event("status", {"step": "saving"})

        processing_text.to_md(contenido, output_name or Path(pdf_path).stem)

        if on_event:
            on_event("log", "Listo!")
    finally:
        shutil.rmtree(image_dir, ignore_errors=True)
