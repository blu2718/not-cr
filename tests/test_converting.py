import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

import app
import converting
import processing_text

PDF = b"%PDF-1.4\n%fake\n"


def office_bytes(kind):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(converting.OFFICE_MARKERS[kind], "<document/>")
    return buffer.getvalue()


def png_bytes(size=(40, 30), mode="RGB", color="white"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def fake_soffice(command, **kwargs):
    """Imita `soffice --convert-to pdf --outdir <dir> <src>`."""
    outdir = Path(command[command.index("--outdir") + 1])
    source = Path(command[-1])
    (outdir / f"{source.stem}.pdf").write_bytes(PDF)
    return subprocess.CompletedProcess(command, 0, "", "")


class ImageConversionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def convert(self, name, image_or_bytes, **save):
        src = self.dir / name
        if isinstance(image_or_bytes, bytes):
            src.write_bytes(image_or_bytes)
        else:
            image_or_bytes.save(src, **save)
        converting.validate(src, "image")
        dest = self.dir / "out.pdf"
        converting.to_pdf(src, "image", dest)
        return dest

    def test_transparent_png_becomes_one_white_page_at_original_size(self):
        dest = self.convert("t.png", png_bytes((400, 300), "RGBA", (255, 0, 0, 0)))
        self.assertEqual(pdfinfo_from_path(str(dest))["Pages"], 1)
        page = convert_from_path(str(dest), dpi=150)[0]
        self.assertEqual(page.size, (400, 300))
        self.assertEqual(page.getpixel((0, 0)), (255, 255, 255))

    def test_multi_frame_tiff_gives_one_page_per_frame(self):
        first, second = Image.new("RGB", (50, 50), "red"), Image.new("RGB", (50, 50), "blue")
        dest = self.convert("m.tiff", first, save_all=True, append_images=[second])
        self.assertEqual(pdfinfo_from_path(str(dest))["Pages"], 2)

    def test_huge_image_is_capped(self):
        dest = self.convert("big.jpg", Image.new("RGB", (6000, 1500), "green"))
        page = convert_from_path(str(dest), dpi=150)[0]
        self.assertLessEqual(max(page.size), converting.MAX_IMAGE_SIDE)

    def test_invalid_image_is_rejected(self):
        src = self.dir / "x.png"
        src.write_bytes(b"not an image")
        with self.assertRaises(converting.ConversionError):
            converting.validate(src, "image")


class OfficeConversionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.src = self.dir / "informe.docx"
        self.src.write_bytes(office_bytes("docx"))

    def test_kinds(self):
        self.assertEqual(converting.kind_of(".PDF"), "pdf")
        self.assertEqual(converting.kind_of(".docx"), "docx")
        self.assertEqual(converting.kind_of(".pptx"), "pptx")
        self.assertEqual(converting.kind_of(".JPG"), "image")
        self.assertIsNone(converting.kind_of(".doc"))

    def test_zip_that_is_not_docx_is_rejected(self):
        converting.validate(self.src, "docx")
        with self.assertRaises(converting.ConversionError):
            converting.validate(self.src, "pptx")
        plain = self.dir / "x.docx"
        plain.write_bytes(b"hola")
        with self.assertRaises(converting.ConversionError):
            converting.validate(plain, "docx")

    @patch("converting.shutil.which", return_value="/usr/bin/soffice")
    @patch("converting.subprocess.run", side_effect=fake_soffice)
    def test_runs_soffice_headless_with_private_profile(self, run, which):
        dest = self.dir / "out.pdf"
        converting.to_pdf(self.src, "docx", dest)
        self.assertEqual(dest.read_bytes(), PDF)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/soffice")
        self.assertIn("--headless", command)
        self.assertEqual(command[command.index("--convert-to") + 1], "pdf")
        self.assertTrue(any(arg.startswith("-env:UserInstallation=file://") for arg in command))
        self.assertEqual(run.call_args.kwargs["timeout"], converting.CONVERT_TIMEOUT)

    @patch("converting.shutil.which", return_value="/usr/bin/soffice")
    @patch("converting.subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 180))
    def test_timeout_gives_clear_error(self, run, which):
        with self.assertRaisesRegex(converting.ConversionError, "180 s"):
            converting.to_pdf(self.src, "docx", self.dir / "out.pdf")

    @patch("converting.shutil.which", return_value="/usr/bin/soffice")
    @patch("converting.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "Error: source file could not be loaded"))
    def test_failed_conversion_reports_reason(self, run, which):
        with self.assertRaisesRegex(converting.ConversionError, "could not be loaded"):
            converting.to_pdf(self.src, "docx", self.dir / "out.pdf")

    @patch("converting.shutil.which", return_value=None)
    def test_missing_soffice_gives_clear_error(self, which):
        with self.assertRaisesRegex(converting.ConversionError, "LibreOffice"):
            converting.to_pdf(self.src, "docx", self.dir / "out.pdf")


class ConvertedUploadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.docs = Path(tmp.name) / "docs"
        self.output = Path(tmp.name) / "output"
        for p in [
            patch.object(app, "DOCS_DIR", self.docs),
            patch.object(app, "OUTPUT_DIR", self.output),
            patch.object(processing_text, "OUTPUT_DIR", self.output),
            patch.object(app, "CONFIG_PATH", Path(tmp.name) / "config.json"),
            patch.object(app, "pdfinfo_from_path", return_value={"Pages": 1}),
            patch("converting.shutil.which", return_value="/usr/bin/soffice"),
            patch("converting.subprocess.run", side_effect=fake_soffice),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.client = app.app.test_client()

    def upload(self, name, content, json_response=True):
        headers = {"Accept": "application/json"} if json_response else {}
        return self.client.post(
            "/upload", data={"files": [(io.BytesIO(content), name)]},
            headers=headers, content_type="multipart/form-data",
        )

    def test_docx_upload_creates_pdf_and_keeps_original(self):
        response = self.upload("Informe final.docx", office_bytes("docx"))
        self.assertEqual(response.json, {"ok": True, "name": "Informe_final"})
        self.assertEqual((self.docs / "Informe_final.pdf").read_bytes(), PDF)
        self.assertTrue((self.docs / ".originals" / "Informe_final.docx").exists())
        info = json.loads((self.docs / ".cache" / "Informe_final.json").read_text())
        self.assertEqual((info["source"], info["original"]), ("docx", "Informe final.docx"))
        self.assertEqual(list((self.docs / ".incoming").iterdir()), [])

        page = self.client.get("/docs/Informe_final").get_data(as_text=True)
        self.assertIn("DOCX", page)
        self.assertIn("/docs/Informe_final/original", page)
        original = self.client.get("/docs/Informe_final/original")
        self.assertEqual(original.data, office_bytes("docx"))
        self.assertIn("attachment", original.headers["Content-Disposition"])
        original.close()

    def test_image_upload_through_form_and_same_stem_as_pdf(self):
        self.upload("foto.pdf", PDF)
        response = self.upload("foto.png", png_bytes(), json_response=False)
        self.assertEqual(response.headers["Location"], "/docs/foto-2")
        self.assertTrue((self.docs / "foto-2.pdf").read_bytes().startswith(b"%PDF-"))
        self.assertTrue((self.docs / ".originals" / "foto-2.png").exists())
        self.assertFalse((self.docs / ".originals" / "foto.pdf").exists())
        self.assertEqual(self.client.get("/docs/foto/original").status_code, 404)

    def test_pptx_upload(self):
        self.assertTrue(self.upload("clase.pptx", office_bytes("pptx")).json["ok"])
        info = json.loads((self.docs / ".cache" / "clase.json").read_text())
        self.assertEqual(info["source"], "pptx")

    def test_deleting_document_removes_original(self):
        self.upload("clase.pptx", office_bytes("pptx"))
        self.client.post("/docs/clase/delete", data={"mode": "pdf", "confirm": "1"})
        self.assertFalse((self.docs / "clase.pdf").exists())
        self.assertFalse((self.docs / ".originals" / "clase.pptx").exists())

    def test_unsupported_and_invalid_files_are_rejected(self):
        response = self.upload("viejo.doc", b"\xd0\xcf\x11\xe0")
        self.assertEqual(response.status_code, 400)
        self.assertIn("formato no soportado", response.json["error"])
        response = self.upload("falso.docx", b"hola")
        self.assertIn("no es un archivo DOCX válido", response.json["error"])
        self.assertEqual(list(self.docs.glob("*.pdf")), [])
        self.assertEqual(list((self.docs / ".incoming").iterdir()), [])

    def test_conversion_failure_is_reported_in_summary(self):
        with patch("converting.shutil.which", return_value=None):
            response = self.upload("a.docx", office_bytes("docx"), json_response=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("LibreOffice", self.client.get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
