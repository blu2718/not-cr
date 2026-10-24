import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import main


class ConfigTests(unittest.TestCase):
    def test_app_save_config_is_readable_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config = {"openrouter": {"model": "provider/model", "reasoning": "low"}}

            with patch.object(app, "CONFIG_PATH", config_path):
                app.save_config(config)
                self.assertEqual(app.load_config(), config)

            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), config)
            self.assertEqual(list(Path(directory).glob("tmp*")), [])

    def test_main_load_config_uses_its_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                '{"openrouter": {"model": "provider/model"}}', encoding="utf-8"
            )

            with patch.object(main, "CONFIG_PATH", config_path):
                self.assertEqual(main.load_config()["openrouter"]["model"], "provider/model")


if __name__ == "__main__":
    unittest.main()
