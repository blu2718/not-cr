import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app
import processing_text
import providers

PDF = b"%PDF-1.4\n%fake\n"
PROJECT_DIR = Path(app.__file__).resolve().parent


class WebappTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.docs = self.tmp / "docs"
        self.output = self.tmp / "output"
        self.docs.mkdir()
        self.output.mkdir()
        self.patches = [
            patch.object(app, "DOCS_DIR", self.docs),
            patch.object(app, "OUTPUT_DIR", self.output),
            patch.object(processing_text, "OUTPUT_DIR", self.output),
            patch.object(app, "CONFIG_PATH", self.tmp / "config.json"),
            patch.object(app, "pdfinfo_from_path", return_value={"Pages": 3}),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        app.jobs.clear()
        self.addCleanup(app.jobs.clear)
        providers.invalidate_cache()
        self.addCleanup(providers.invalidate_cache)
        self.client = app.app.test_client()

    # ── helpers ──
    def upload(self, *files, json_response=False):
        headers = {"Accept": "application/json"} if json_response else {}
        data = {"files": [(io.BytesIO(content), name) for name, content in files]}
        return self.client.post("/upload", data=data, headers=headers, content_type="multipart/form-data")

    def add_doc(self, name):
        (self.docs / f"{name}.pdf").write_bytes(PDF)

    def add_result(self, name, meta=None, text="texto"):
        (self.output / f"{name}.md").write_text(text, encoding="utf-8")
        if meta is not None:
            app.write_json_atomic(self.output / f"{name}.meta.json", meta)

    def meta(self, name):
        return json.loads((self.output / f"{name}.meta.json").read_text(encoding="utf-8"))

    def fake_job(self, doc, status="running"):
        job = {
            "id": f"job-{doc}", "doc": doc, "model": "vendor/model", "reasoning": None,
            "output_name": f"{doc}__vendor_model__20261024-120000", "status": status,
            "events": [], "created": time.time(), "started": time.time() - 10, "finished": None,
            "progress": {"step": "page", "index": 2, "total": 4}, "page_times": [3.0],
            "usage": [], "pricing": None, "chunks": [], "warnings": [], "error": None, "partial": False,
        }
        app.jobs[job["id"]] = job
        return job


class UploadTests(WebappTestCase):
    def test_rejects_name_that_becomes_bare_pdf(self):
        response = self.upload(("日本.pdf", PDF), json_response=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json["ok"])
        self.assertEqual(list(self.docs.glob("*.pdf")), [])

    def test_rejects_non_pdf_content(self):
        response = self.upload(("foto.pdf", b"\x89PNG...."), json_response=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no es un PDF", response.json["error"])

    def test_duplicate_gets_suffix_and_double_underscore_collapses(self):
        self.assertEqual(self.upload(("uno.pdf", PDF), json_response=True).json["name"], "uno")
        self.assertEqual(self.upload(("uno.pdf", PDF), json_response=True).json["name"], "uno-2")
        self.assertEqual(self.upload(("a__b.pdf", PDF), json_response=True).json["name"], "a_b")
        self.assertTrue((self.docs / "uno-2.pdf").exists())
        info = json.loads((self.docs / ".cache" / "uno.json").read_text())
        self.assertEqual(info["pages"], 3)

    def test_multiple_upload_with_one_invalid(self):
        response = self.upload(("uno.pdf", PDF), ("dos.pdf", PDF), ("foto.png", b"x"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(sorted(p.name for p in self.docs.glob("*.pdf")), ["dos.pdf", "uno.pdf"])
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("2 subidos · 1 rechazado", page)

    def test_single_upload_redirects_to_document(self):
        response = self.upload(("uno.pdf", PDF))
        self.assertEqual(response.headers["Location"], "/docs/uno")


class ThumbnailTests(WebappTestCase):
    def test_thumbnail_is_generated_once_and_deleted_with_pdf(self):
        self.add_doc("uno")
        with patch.object(app, "convert_from_path", return_value=[Image.new("RGB", (360, 480))]) as convert:
            first = self.client.get("/docs/uno/thumb")
            second = self.client.get("/docs/uno/thumb")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.mimetype, "image/jpeg")
        self.assertIn("max-age=86400", first.headers["Cache-Control"])
        self.assertEqual(second.status_code, 200)
        first.close()
        second.close()
        self.assertEqual(convert.call_count, 1)
        self.assertTrue((self.docs / ".cache" / "uno.jpg").exists())

        self.client.post("/docs/uno/delete", data={"mode": "pdf", "confirm": "1"})
        self.assertFalse((self.docs / ".cache" / "uno.jpg").exists())


class DocumentTests(WebappTestCase):
    def setUp(self):
        super().setUp()
        self.add_doc("uno")
        self.add_doc("uno_dos")
        self.add_result("uno__vendor_model__20261020-101010", {"doc": "uno", "model": "vendor/model"})
        self.add_result("uno__vendor_other__20261021-101010", None)
        self.add_result("uno_dos__vendor_model__20261022-101010", {"doc": "uno_dos", "model": "vendor/model"})

    def test_exact_document_filter(self):
        names = [e["name"] for e in app.results_for_doc("uno")]
        self.assertEqual(sorted(names), ["uno__vendor_model__20261020-101010", "uno__vendor_other__20261021-101010"])
        page = self.client.get("/docs/uno").get_data(as_text=True)
        self.assertNotIn("uno_dos__vendor_model", page)

    def test_delete_pdf_only_keeps_results(self):
        response = self.client.post("/docs/uno/delete", data={"mode": "pdf", "confirm": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse((self.docs / "uno.pdf").exists())
        self.assertTrue((self.output / "uno__vendor_model__20261020-101010.md").exists())
        self.assertIn("PDF eliminado", self.client.get("/").get_data(as_text=True))

    def test_delete_all_removes_results_and_sidecars(self):
        self.client.post("/docs/uno/delete", data={"mode": "all", "confirm": "1"})
        self.assertFalse((self.docs / "uno.pdf").exists())
        self.assertFalse((self.output / "uno__vendor_model__20261020-101010.md").exists())
        self.assertFalse((self.output / "uno__vendor_model__20261020-101010.meta.json").exists())
        self.assertFalse((self.output / "uno__vendor_other__20261021-101010.md").exists())
        self.assertTrue((self.output / "uno_dos__vendor_model__20261022-101010.md").exists())

    def test_delete_requires_confirmation(self):
        self.client.post("/docs/uno/delete", data={"mode": "all"})
        self.assertTrue((self.docs / "uno.pdf").exists())

    def test_delete_refused_with_active_job(self):
        self.fake_job("uno")
        self.client.post("/docs/uno/delete", data={"mode": "pdf", "confirm": "1"})
        self.assertTrue((self.docs / "uno.pdf").exists())

    def test_bulk_delete_skips_documents_with_active_job(self):
        self.fake_job("uno")
        self.client.post("/docs/bulk", data={"names": ["uno", "uno_dos"], "action": "delete_all", "confirm": "1"})
        self.assertTrue((self.docs / "uno.pdf").exists())
        self.assertFalse((self.docs / "uno_dos.pdf").exists())
        self.assertFalse((self.output / "uno_dos__vendor_model__20261022-101010.md").exists())
        self.assertIn("omitidos", self.client.get("/").get_data(as_text=True))

    def test_bulk_process_creates_one_job_per_document(self):
        with patch.object(app, "_run_job"):
            self.client.post("/docs/bulk", data={"names": ["uno", "uno_dos"], "action": "process", "model": "vendor/model"})
        self.assertEqual(sorted(j["doc"] for j in app.jobs.values()), ["uno", "uno_dos"])

    def test_process_reuses_active_job_and_rejects_models_without_vision(self):
        with patch.object(app, "_run_job"):
            first = self.client.post("/process/uno", data={"model": "vendor/model"})
            second = self.client.post("/process/uno", data={"model": "vendor/model"})
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(len(app.jobs), 1)

        providers._cache = (time.time(), [{"id": "vendor/text", "supports_images": False, "supports_reasoning": False, "reasoning_efforts": [], "pricing": {}}])
        response = self.client.post("/process/uno", data={"model": "vendor/text"})
        self.assertEqual(response.status_code, 400)

    def test_output_name_never_overwrites(self):
        name = app.make_output_name("uno", "qwen/qwen3-vl")
        doc, slug, ts = app.parse_output_name(name)
        self.assertEqual((doc, slug), ("uno", "qwen_qwen3-vl"))
        (self.output / f"{name}.md").write_text("x")
        self.assertNotEqual(app.make_output_name("uno", "qwen/qwen3-vl"), name)


class OrganizationTests(WebappTestCase):
    def setUp(self):
        super().setUp()
        self.add_doc("uno")
        self.a = "uno__vendor_model__20261020-101010"
        self.b = "uno__vendor_other__20261021-101010"
        self.add_result(self.a, {"doc": "uno", "model": "vendor/model", "title": "Alfa", "folder": "Tesis"})
        self.add_result(self.b, None)
        self.add_result("legacy")

    def test_edit_creates_minimal_sidecar_for_results_without_one(self):
        self.client.post(f"/output/{self.b}/edit", data={"title": "Beta", "note": "una nota"})
        meta = self.meta(self.b)
        self.assertEqual((meta["doc"], meta["model"], meta["title"], meta["note"]), ("uno", "vendor_other", "Beta", "una nota"))
        self.client.post("/output/legacy/edit", data={"title": "Viejo"})
        self.assertEqual((self.meta("legacy")["doc"], self.meta("legacy")["model"]), ("legacy", ""))

    def test_only_one_primary_per_document(self):
        self.client.post(f"/output/{self.a}/primary")
        self.client.post(f"/output/{self.b}/primary")
        self.assertFalse(self.meta(self.a)["primary"])
        self.assertTrue(self.meta(self.b)["primary"])
        self.client.post(f"/output/{self.b}/primary")
        self.assertFalse(self.meta(self.b)["primary"])

    def test_bulk_move_and_delete(self):
        self.client.post("/output/bulk", data={"names": [self.a, self.b], "action": "move", "folder": "Nueva"})
        self.assertEqual(self.meta(self.a)["folder"], "Nueva")
        self.assertEqual(self.meta(self.b)["folder"], "Nueva")
        self.assertIn("Nueva", app.load_folder_list())

        self.client.post("/output/bulk", data={"names": [self.a], "action": "delete", "confirm": "1"})
        self.assertFalse((self.output / f"{self.a}.md").exists())
        self.assertFalse((self.output / f"{self.a}.meta.json").exists())

    def test_folders_rename_updates_sidecars_and_delete_keeps_results(self):
        self.client.post("/folders", data={"name": "Vacía"})
        self.assertIn("Vacía", app.all_folders())
        self.client.post("/folders", data={"name": "a/b"})
        self.assertNotIn("a/b", app.all_folders())

        self.client.post("/folders/rename", data={"old": "Tesis", "new": "Tesis final"})
        self.assertEqual(self.meta(self.a)["folder"], "Tesis final")
        self.assertNotIn("Tesis", app.all_folders())

        self.client.post("/folders/delete", data={"name": "Tesis final"})
        self.assertTrue((self.output / f"{self.a}.md").exists())
        self.assertEqual(self.meta(self.a)["folder"], "")
        self.assertNotIn("Tesis final", app.all_folders())

    def test_index_filters_and_sort(self):
        page = self.client.get("/?q=alfa").get_data(as_text=True)
        self.assertIn(f"/output/{self.a}", page)
        self.assertNotIn(f"/output/{self.b}", page)

        page = self.client.get("/?folder=Tesis").get_data(as_text=True)
        self.assertIn(f"/output/{self.a}", page)
        self.assertNotIn("/output/legacy", page)

        page = self.client.get("/?folder=%2F").get_data(as_text=True)
        self.assertNotIn(f"/output/{self.a}\"", page)
        self.assertIn("/output/legacy", page)

        page = self.client.get("/?model=vendor/model").get_data(as_text=True)
        self.assertIn(f"/output/{self.a}", page)
        self.assertNotIn(f"/output/{self.b}\"", page)

        entries = app.list_results()
        ordered = [e["name"] for e in app.sort_entries(entries, "title")]
        self.assertEqual(ordered[0], self.a)  # «Alfa» antes que «legacy» y «uno»
        dates = [e["name"] for e in app.sort_entries([e for e in entries if e["doc"] == "uno"], "date_asc")]
        self.assertEqual(dates, [self.a, self.b])

        groups = app.group_results(entries, "folder", "date_desc")
        self.assertEqual(groups[-1]["label"], "Sin carpeta")


class JobsTests(WebappTestCase):
    def test_api_jobs_lists_active_job(self):
        self.add_doc("uno")
        self.fake_job("uno")
        response = self.client.get("/api/jobs")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        [summary] = response.json
        self.assertEqual((summary["doc"], summary["status"], summary["total"], summary["percent"]), ("uno", "running", 4, 25))
        self.assertEqual(summary["eta_seconds"], 9)

    def test_partial_result_is_saved_when_run_fails_midway(self):
        self.add_doc("uno")
        job = self.fake_job("uno", status="queued")
        job.update(progress={}, page_times=[], started=None)

        def failing_run(pdf_path, api_key, model, on_chunk, on_event, **kwargs):
            on_event("status", {"step": "page", "index": 1, "total": 2})
            on_chunk("primera página")
            on_event("page_time", 1.0)
            raise RuntimeError("se cayó la red")

        with patch.object(app.main, "run_ai_ocr", side_effect=failing_run):
            app._run_job(job, self.docs / "uno.pdf", app.get_provider({}), "vendor/model", None)

        self.assertEqual(job["status"], "error")
        name = job["output_name"]
        self.assertEqual((self.output / f"{name}.md").read_text(encoding="utf-8"), "primera página")
        meta = self.meta(name)
        self.assertEqual(meta["status"], "partial")
        self.assertIn("se cayó la red", meta["error"])
        kind, payload = job["events"][-1]
        self.assertEqual(kind, "error")
        self.assertTrue(json.loads(payload)["partial"])

    def test_successful_run_writes_sidecar(self):
        self.add_doc("uno")
        job = self.fake_job("uno", status="queued")
        job.update(progress={}, page_times=[], started=None)

        def ok_run(pdf_path, api_key, model, on_chunk, on_event, output_name, **kwargs):
            on_event("status", {"step": "page", "index": 1, "total": 1})
            on_event("usage", {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.5})
            on_event("warning", {"page": 1, "total": 1, "message": "x"})
            processing_text.to_md("hola", output_name)

        with patch.object(app.main, "run_ai_ocr", side_effect=ok_run):
            app._run_job(job, self.docs / "uno.pdf", app.get_provider({}), "vendor/model", "low")

        meta = self.meta(job["output_name"])
        self.assertEqual((meta["status"], meta["reasoning"], meta["pages"]), ("done", "low", 1))
        self.assertEqual(meta["truncated_pages"], [1])
        self.assertEqual(meta["estimated_cost"], 0.5)
        self.assertEqual(job["events"][-1][0], "done")


class RenderMarkdownTests(unittest.TestCase):
    def test_strips_script_and_keeps_latex(self):
        html = app.render_markdown("<script>alert(1)</script>\n\nSea $\\{x\\}$ y $a*b*c$.")
        self.assertNotIn("<script", html)
        self.assertNotIn("alert(1)", html)
        self.assertIn("\\{x\\}", html)
        self.assertIn("a*b*c", html)
        self.assertNotIn("<em>", html)
        self.assertIn('class="arithmatex"', html)


class SecurityTests(WebappTestCase):
    def test_unknown_host_is_rejected(self):
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.client.get("/", headers={"Host": "arenarium:5000"}).status_code, 200)

    def test_post_with_foreign_origin_is_rejected(self):
        response = self.client.post("/folders", data={"name": "x"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/folders", data={"name": "x"}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 302)

    def test_config_page_never_contains_the_key(self):
        app.save_config({"openrouter": {"api-key": "sk-or-secret-9876", "model": "vendor/model"}})
        page = self.client.get("/config").get_data(as_text=True)
        self.assertNotIn("sk-or-secret-9876", page)
        self.assertIn("…9876", page)

    def test_empty_key_on_save_keeps_stored_key(self):
        app.save_config({"openrouter": {"api-key": "sk-or-secret", "model": "vendor/model"}, "other": 1})
        self.client.post("/config", data={"api-url": "https://openrouter.ai/api/v1", "api-key": "", "model": "vendor/new", "reasoning": "low"})
        config = app.load_config()
        self.assertEqual(config["openrouter"]["api-key"], "sk-or-secret")
        self.assertEqual(config["openrouter"]["model"], "vendor/new")
        self.assertEqual(config["openrouter"]["reasoning"], "low")
        self.assertEqual(config["other"], 1)

        self.client.post("/config", data={"api-url": "https://openrouter.ai/api/v1", "api-key": "", "clear_key": "1"})
        self.assertEqual(app.load_config()["openrouter"]["api-key"], "")

    def test_api_models_without_key_is_502(self):
        response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json)

    def test_works_without_config_file(self):
        self.assertFalse(app.CONFIG_PATH.exists())
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/config").status_code, 200)

    def test_import_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-c", "import app"],
                cwd=cwd, env={**os.environ, "PYTHONPATH": str(PROJECT_DIR)},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.listdir(cwd), [])


if __name__ == "__main__":
    unittest.main()
