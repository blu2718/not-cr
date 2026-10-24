import os
import tempfile
import unittest
from unittest.mock import patch

import main
import ocr
import processing_text


class MarkdownSeparatorTests(unittest.TestCase):
    def test_separates_adjacent_words(self):
        self.assertEqual(processing_text.markdown_separator("primer", "segundo"), " ")

    def test_keeps_hyphenated_word_together(self):
        self.assertEqual(processing_text.markdown_separator("inter-", "nacional"), "")

    def test_separates_markdown_blocks(self):
        self.assertEqual(processing_text.markdown_separator("# Título", "## Sección"), "\n\n")


class CleanMarkdownTests(unittest.TestCase):
    def test_removes_balanced_fence_wrapper(self):
        self.assertEqual(processing_text.clean_markdown("```markdown\nhola\n```"), "hola")
        self.assertEqual(processing_text.clean_markdown("```\nhola\n```"), "hola")

    def test_preserves_code_block_without_wrapper(self):
        self.assertEqual(
            processing_text.clean_markdown("```python\nprint('hi')\n```"),
            "```python\nprint('hi')\n```",
        )

    def test_preserves_trailing_backticks(self):
        self.assertEqual(processing_text.clean_markdown("texto ```"), "texto ```")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(processing_text.clean_markdown("hola mundo"), "hola mundo")


class OcrPipelineTests(unittest.TestCase):
    @patch("ocr.processing_images.to_model")
    @patch("ocr.processing_images.message_build")
    @patch("ocr.processing_images.image_prep", return_value=[["first"], ["second"]])
    @patch("ocr.processing_pdf.convert")
    def test_joins_chunks_and_uses_cleaned_context(
        self, convert, image_prep, message_build, to_model
    ):
        to_model.side_effect = [
            {"choices": [{"message": {"content": "```markdown\nprimer\n```"}}]},
            {"choices": [{"message": {"content": "segundo"}}]},
        ]

        chunks = []

        result = ocr.process_pdf(
            "document.pdf", "images", "key", "model", on_chunk=chunks.append
        )

        self.assertEqual(result, "primer segundo")
        self.assertEqual(chunks, ["primer", " segundo"])
        self.assertEqual(message_build.call_args_list[1].args[1], "primer")
        convert.assert_called_once_with("document.pdf", "images")
        image_prep.assert_called_once_with("images")

    @patch("main.processing_text.to_md")
    @patch("main.ocr.process_pdf", return_value="contenido")
    def test_cli_uses_and_cleans_an_isolated_image_directory(self, process_pdf, to_md):
        with tempfile.TemporaryDirectory() as work_dir:
            pdf_path = os.path.join(work_dir, "uno.pdf")
            with open(pdf_path, "wb") as pdf:
                pdf.write(b"%PDF-1.7")

            main.run_ai_ocr(pdf_path, "key", "model")

        image_dir = process_pdf.call_args.args[1]
        self.assertFalse(os.path.exists(image_dir))
        self.assertEqual(
            process_pdf.call_args.args[:4], (pdf_path, image_dir, "key", "model")
        )
        to_md.assert_called_once_with("contenido", "uno")


if __name__ == "__main__":
    unittest.main()
