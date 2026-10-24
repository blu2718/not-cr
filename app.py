import json
import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def save_config(config: dict) -> None:
    write_json_atomic(CONFIG_PATH, config)


PROVIDER_DEFAULTS = {
    "api-url": "https://openrouter.ai/api/v1",
    "api-key": "",
    "model": "qwen/qwen3-vl-235b-a22b-instruct",
    "reasoning": "",
}


def get_provider(config: dict) -> dict:
    """config["openrouter"] con los defaults rellenados para las claves que falten."""
    return PROVIDER_DEFAULTS | (config.get("openrouter") or {})
