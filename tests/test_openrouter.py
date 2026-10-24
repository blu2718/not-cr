import unittest
from unittest.mock import MagicMock, patch

import ocr
import processing_images
import providers


def fake_model(data):
    model = MagicMock()
    model.model_dump.return_value = data
    return model


CATALOG = [
    {
        "id": "vendor/vision-reasoner",
        "name": "Vision Reasoner",
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["max_tokens", "reasoning"],
        "reasoning": {"supported_efforts": ["high", "medium", "low", "minimal"]},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        "context_length": 128000,
    },
    {
        "id": "vendor/text-only",
        "name": "Text Only",
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["max_tokens"],
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": 32000,
    },
    {
        "id": "vendor/bare",
        "supported_parameters": ["reasoning"],
    },
]


class ToModelTests(unittest.TestCase):
    @patch("processing_images.OpenAI")
    def test_sends_reasoning_only_when_given(self, openai):
        create = openai.return_value.chat.completions.create
        create.return_value.model_dump.return_value = {"choices": []}

        result = processing_images.to_model("key", "model", [{"role": "user"}], base_url="http://x/v1")
        self.assertEqual(result, {"choices": []})
        openai.assert_called_with(api_key="key", base_url="http://x/v1")
        self.assertNotIn("extra_body", create.call_args.kwargs)

        processing_images.to_model("key", "model", [], reasoning="high")
        self.assertEqual(create.call_args.kwargs["extra_body"], {"reasoning": {"effort": "high"}})

        processing_images.to_model("key", "model", [], reasoning="")
        self.assertNotIn("extra_body", create.call_args.kwargs)


class ResolveReasoningTests(unittest.TestCase):
    info = {"supports_reasoning": True, "reasoning_efforts": ["low", "high"]}

    def test_uses_default_when_not_requested(self):
        self.assertEqual(providers.resolve_reasoning(self.info, "", "low"), "low")

    def test_request_overrides_default(self):
        self.assertEqual(providers.resolve_reasoning(self.info, "high", "low"), "high")

    def test_off_sends_nothing(self):
        self.assertIsNone(providers.resolve_reasoning(self.info, "off", "low"))
        self.assertIsNone(providers.resolve_reasoning(self.info, "", ""))

    def test_model_without_reasoning(self):
        info = {"supports_reasoning": False, "reasoning_efforts": []}
        self.assertIsNone(providers.resolve_reasoning(info, "high", "low"))

    def test_obsolete_effort(self):
        self.assertIsNone(providers.resolve_reasoning(self.info, "medium", ""))

    def test_model_not_cached_passes_effort(self):
        self.assertEqual(providers.resolve_reasoning(None, "", "medium"), "medium")


class ListModelsTests(unittest.TestCase):
    def setUp(self):
        providers.invalidate_cache()
        self.addCleanup(providers.invalidate_cache)

    @patch("providers.OpenAI")
    def test_normalizes_catalog(self, openai):
        openai.return_value.models.list.return_value = [fake_model(m) for m in CATALOG]
        models = {m["id"]: m for m in providers.list_models("key", "http://x/v1")}

        vision = models["vendor/vision-reasoner"]
        self.assertIs(vision["supports_images"], True)
        self.assertIs(vision["supports_reasoning"], True)
        self.assertEqual(vision["reasoning_efforts"], ["high", "medium", "low", "minimal"])
        self.assertEqual(vision["pricing"], {"prompt": "0.000001", "completion": "0.000002"})
        self.assertEqual(vision["context_length"], 128000)

        text = models["vendor/text-only"]
        self.assertIs(text["supports_images"], False)
        self.assertIs(text["supports_reasoning"], False)
        self.assertEqual(text["reasoning_efforts"], [])

        bare = models["vendor/bare"]
        self.assertEqual(bare["name"], "vendor/bare")
        self.assertIsNone(bare["supports_images"])
        self.assertEqual(bare["reasoning_efforts"], providers.DEFAULT_EFFORTS)

        self.assertEqual(providers.get_cached_model("vendor/text-only"), text)
        self.assertIsNone(providers.get_cached_model("vendor/missing"))

    @patch("providers.OpenAI")
    def test_use_cache_false_skips_cache(self, openai):
        openai.return_value.models.list.return_value = [fake_model(CATALOG[0])]
        providers.list_models("key", "http://x/v1")
        providers.list_models("key", "http://x/v1")
        self.assertEqual(openai.return_value.models.list.call_count, 1)

        providers.list_models("key", "http://x/v1", use_cache=False)
        self.assertEqual(openai.return_value.models.list.call_count, 2)

    @patch("providers.OpenAI")
    def test_errors_propagate(self, openai):
        openai.return_value.models.list.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            providers.list_models("key", "http://x/v1")


class FormatTests(unittest.TestCase):
    def test_format_price(self):
        self.assertEqual(providers.format_price("0.0000003"), "$0.30 / M tokens")
        self.assertEqual(providers.format_price(0), "gratis")
        self.assertEqual(providers.format_price(None), "")
        self.assertEqual(providers.format_price("abc"), "")

    def test_format_context(self):
        self.assertEqual(providers.format_context(128000), "128k tokens")
        self.assertEqual(providers.format_context(None), "")


class TruncationWarningTests(unittest.TestCase):
    @patch("ocr.processing_images.to_model")
    @patch("ocr.processing_images.message_build")
    @patch("ocr.processing_images.image_prep", return_value=[["a"], ["b"]])
    @patch("ocr.processing_pdf.convert")
    def test_emits_warning_when_finish_reason_is_length(self, convert, image_prep, message_build, to_model):
        to_model.side_effect = [
            {"choices": [{"message": {"content": "uno"}, "finish_reason": "stop"}]},
            {
                "choices": [{"message": {"content": "dos"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ]
        events = []
        result = ocr.process_pdf("d.pdf", "imgs", "key", "model", on_event=lambda k, p: events.append((k, p)))

        self.assertEqual(result, "uno dos")
        warnings = [p for k, p in events if k == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["page"], 2)
        self.assertEqual(warnings[0]["total"], 2)
        self.assertIn("truncada", warnings[0]["message"])
        self.assertIn(("usage", {"prompt_tokens": 10, "completion_tokens": 5}), events)
        self.assertEqual(len([k for k, _ in events if k == "page_time"]), 2)
        self.assertIn(("log", "└ 2 páginas"), events)


if __name__ == "__main__":
    unittest.main()
