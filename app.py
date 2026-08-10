import json
import os
import tempfile
from pathlib import Path


CONFIG_PATH = Path("config.json")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config: dict) -> None:
    file_descriptor, temporary_path = tempfile.mkstemp(dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, indent=4, ensure_ascii=False)
        os.replace(temporary_path, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
