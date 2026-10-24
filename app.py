import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import uuid
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import markdown
import nh3
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
    url_for,
)
from pdf2image import convert_from_path, pdfinfo_from_path
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

import main
import processing_text
import providers

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DOCS_DIR = BASE_DIR / "docs"
OUTPUT_DIR = BASE_DIR / "output"


# ── Configuración ────────────────────────────────────────────────────────────


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


def load_config_or_empty() -> dict:
    """Como load_config, pero un config.json ausente o roto no tumba la página."""
    try:
        return load_config()
    except (OSError, ValueError):
        return {}


# ── Flask ────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("NOT_CR_SECRET") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


# ── Utilidades ───────────────────────────────────────────────────────────────


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def iso_from_timestamp(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def parse_iso(value) -> datetime | None:
    """ISO 8601 → datetime local *naive* (para comparar con datetime.now())."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return best == "application/json"


def safe_next(default: str) -> str:
    target = request.form.get("next") or request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def model_short(model_id: str) -> str:
    return (model_id or "").split("/", 1)[-1]


def model_hue(model_id: str) -> int:
    return zlib.crc32((model_id or "").encode("utf-8")) % 360


def usage_totals(usages: list[dict]) -> dict:
    totals = {"prompt": 0, "completion": 0, "total": 0}
    for usage in usages or []:
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        totals["prompt"] += prompt
        totals["completion"] += completion
        totals["total"] += usage.get("total_tokens") or (prompt + completion)
    return totals


def estimate_cost(usages: list[dict], pricing: dict | None) -> float | None:
    """Suma de `usage.cost` si OpenRouter lo incluye; si no, tokens × precio del catálogo."""
    if not usages:
        return None
    if all(isinstance(u.get("cost"), (int, float)) for u in usages):
        return float(sum(u["cost"] for u in usages))
    try:
        prompt_price = float(pricing["prompt"])
        completion_price = float(pricing["completion"])
    except (TypeError, KeyError, ValueError):
        return None
    totals = usage_totals(usages)
    return totals["prompt"] * prompt_price + totals["completion"] * completion_price


# ── Trabajos (cola en memoria con hilos en background) ───────────────────────

MAX_CONCURRENT_JOBS = 2
JOB_SLOTS = threading.Semaphore(MAX_CONCURRENT_JOBS)
JOB_RECENT_SECONDS = 10 * 60
ACTIVE_STATUSES = ("queued", "running")

jobs: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
META_LOCK = threading.Lock()  # todas las escrituras de sidecars y _folders.json

OUTPUT_TS_FORMAT = "%Y%m%d-%H%M%S"


def model_slug(model: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    slug = re.sub(r"_{2,}", "_", slug)  # nunca "__" dentro del slug
    return slug.strip("_") or "modelo"


def make_output_name(stem: str, model: str) -> str:
    """`{stem}__{slug}__{timestamp}`; nunca reutiliza un nombre existente."""
    slug = model_slug(model)
    moment = datetime.now()
    taken = {job["output_name"] for job in jobs.values()}
    while True:
        name = f"{stem}__{slug}__{moment.strftime(OUTPUT_TS_FORMAT)}"
        if name not in taken and not (OUTPUT_DIR / f"{name}.md").exists():
            return name
        moment += timedelta(seconds=1)


def meta_path(name: str) -> Path:
    return OUTPUT_DIR / f"{name}.meta.json"


def write_meta(job: dict, reasoning: str | None, status: str, error: str | None = None) -> None:
    meta = {
        "doc": job["doc"],
        "model": job["model"],
        "reasoning": reasoning,
        "started": iso_from_timestamp(job["started"]),
        "finished": iso_from_timestamp(time.time()),
        "status": status,
        "pages": job["progress"].get("total") or len(job["page_times"]),
        "truncated_pages": sorted({w["page"] for w in job["warnings"]}),
        "usage": job["usage"],
        "estimated_cost": estimate_cost(job["usage"], job["pricing"]),
        "title": "",
        "note": "",
        "folder": "",
        "primary": False,
    }
    if error:
        meta["error"] = error
    with META_LOCK:
        OUTPUT_DIR.mkdir(exist_ok=True)
        write_json_atomic(meta_path(job["output_name"]), meta)


def _run_job(job: dict, pdf_path: Path, provider: dict, model: str, reasoning: str | None) -> None:
    def emit(kind, payload):
        job["events"].append((kind, json.dumps(payload, ensure_ascii=False)))

    def on_chunk(piece):
        job["chunks"].append(piece)
        emit("chunk", piece)

    def on_event(kind, payload):
        if kind == "status":
            job["progress"] = payload
        elif kind == "page_time":
            job["page_times"].append(payload)
        elif kind == "usage":
            job["usage"].append(payload)
        elif kind == "warning":
            job["warnings"].append(payload)
        emit(kind, payload)

    with JOB_SLOTS:
        job["status"] = "running"
        job["started"] = time.time()
        emit("status", {"step": "starting"})
        try:
            main.run_ai_ocr(
                str(pdf_path),
                provider["api-key"],
                model,
                base_url=provider["api-url"],
                reasoning=reasoning,
                on_chunk=on_chunk,
                on_event=on_event,
                output_name=job["output_name"],
            )
            write_meta(job, reasoning, status="done")
            emit("done", {"output": job["output_name"]})
            job["status"] = "done"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            job["error"] = error
            partial = bool(job["chunks"])
            if partial:
                # No perder las páginas que sí terminaron.
                try:
                    processing_text.to_md("".join(job["chunks"]), job["output_name"])
                    write_meta(job, reasoning, status="partial", error=error)
                except OSError:
                    partial = False
            job["partial"] = partial
            emit(
                "error",
                {
                    "message": error,
                    "partial": partial,
                    "output": job["output_name"] if partial else None,
                },
            )
            job["status"] = "error"
        finally:
            job["finished"] = time.time()


def start_job(doc: str, model: str, reasoning: str | None, provider: dict) -> dict:
    """Crea y lanza un job; si ya hay uno activo para (doc, modelo), lo reutiliza."""
    with JOBS_LOCK:
        for job in jobs.values():
            if job["doc"] == doc and job["model"] == model and job["status"] in ACTIVE_STATUSES:
                return job

        info = providers.get_cached_model(model) or {}
        job = {
            "id": uuid.uuid4().hex,
            "doc": doc,
            "model": model,
            "reasoning": reasoning,
            "output_name": make_output_name(doc, model),
            "status": "queued",
            "events": [],
            "created": time.time(),
            "started": None,
            "finished": None,
            "progress": {"step": "queued"},
            "page_times": [],
            "usage": [],
            "pricing": info.get("pricing") or None,
            "chunks": [],
            "warnings": [],
            "error": None,
            "partial": False,
        }
        jobs[job["id"]] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, DOCS_DIR / f"{doc}.pdf", provider, model, reasoning),
        daemon=True,
    )
    thread.start()
    return job


def job_summary(job: dict) -> dict:
    progress = job["progress"] or {}
    total = progress.get("total") or 0
    done_pages = len(job["page_times"])
    status = job["status"]

    if status == "done":
        percent = 100
    elif total:
        percent = round(100 * min(done_pages, total) / total)
    else:
        percent = 0

    eta = None
    if status == "running" and job["page_times"] and total:
        average = sum(job["page_times"]) / len(job["page_times"])
        eta = round(average * max(total - done_pages, 0))

    elapsed = 0
    if job["started"]:
        elapsed = round((job["finished"] or time.time()) - job["started"])

    output = None
    if status == "done" or (status == "error" and job["partial"]):
        output = job["output_name"]

    return {
        "id": job["id"],
        "doc": job["doc"],
        "model": job["model"],
        "model_short": model_short(job["model"]),
        "hue": model_hue(job["model"]),
        "status": status,
        "step": progress.get("step"),
        "index": progress.get("index") or 0,
        "total": total,
        "done_pages": done_pages,
        "percent": percent,
        "elapsed": elapsed,
        "eta_seconds": eta,
        "warnings": len(job["warnings"]),
        "output": output,
        "error": job["error"],
        "url": f"/jobs/{job['id']}",
    }


def visible_jobs(doc: str | None = None) -> list[dict]:
    """Jobs activos y terminados hace menos de 10 min, más recientes primero."""
    now = time.time()
    with JOBS_LOCK:
        selected = [
            job
            for job in jobs.values()
            if (doc is None or job["doc"] == doc)
            and (
                job["status"] in ACTIVE_STATUSES
                or (job["finished"] and now - job["finished"] < JOB_RECENT_SECONDS)
            )
        ]
    selected.sort(key=lambda job: job["created"], reverse=True)
    return selected


def doc_has_active_job(doc: str) -> bool:
    with JOBS_LOCK:
        return any(j["doc"] == doc and j["status"] in ACTIVE_STATUSES for j in jobs.values())


def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        abort(404)
    return job


def prepare_run(form) -> tuple[str, str | None, dict]:
    """Resuelve modelo y razonamiento del formulario. Lanza ValueError con el motivo."""
    provider = get_provider(load_config_or_empty())
    model = (form.get("model") or "").strip() or (provider.get("model") or "").strip()
    if not model:
        raise ValueError("Falta el modelo")

    info = providers.get_cached_model(model)
    if info and info.get("supports_images") is False:
        raise ValueError(f"El modelo {model} no acepta imágenes; no sirve para OCR")

    reasoning = providers.resolve_reasoning(
        info, form.get("reasoning") or "", provider.get("reasoning") or ""
    )
    return model, reasoning, provider


@app.post("/process/<name>")
def process_doc(name):
    valid_doc(name)
    try:
        model, reasoning, provider = prepare_run(request.form)
    except ValueError as exc:
        return str(exc), 400
    job = start_job(name, model, reasoning, provider)
    return redirect(url_for("job_page", job_id=job["id"]))


@app.get("/jobs/<job_id>")
def job_page(job_id):
    job = get_job(job_id)
    return render_template(
        "job.html",
        job=job,
        summary=job_summary(job),
        doc=doc_info(job["doc"]) if doc_exists(job["doc"]) else None,
    )


@app.get("/jobs/<job_id>/stream")
def job_stream(job_id):
    job = get_job(job_id)

    def generate():
        sent = 0
        last_ping = time.monotonic()
        while True:
            events = job["events"]
            while sent < len(events):
                kind, payload = events[sent]
                sent += 1
                yield f"event: {kind}\ndata: {payload}\n\n"
                last_ping = time.monotonic()
            if job["status"] in ("done", "error") and sent >= len(job["events"]):
                return
            if time.monotonic() - last_ping > 15:
                yield ": ping\n\n"
                last_ping = time.monotonic()
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs")
def api_jobs():
    doc = request.args.get("doc") or None
    response = jsonify([job_summary(job) for job in visible_jobs(doc)])
    response.headers["Cache-Control"] = "no-store"
    return response


# ── Documentos ───────────────────────────────────────────────────────────────

UPLOAD_LOCK = threading.Lock()
THUMB_WIDTH = 360


def cache_dir() -> Path:
    return DOCS_DIR / ".cache"


def is_safe_name(name: str) -> bool:
    return bool(name) and secure_filename(name) == name


def doc_exists(name: str) -> bool:
    return is_safe_name(name) and (DOCS_DIR / f"{name}.pdf").is_file()


def valid_doc(name: str) -> Path:
    if not doc_exists(name):
        abort(404)
    return DOCS_DIR / f"{name}.pdf"


def read_page_count(path: Path) -> int | None:
    try:
        return int(pdfinfo_from_path(str(path))["Pages"])
    except Exception:
        return None


def doc_info(name: str) -> dict:
    path = DOCS_DIR / f"{name}.pdf"
    stat = path.stat()
    info_path = cache_dir() / f"{name}.json"
    data = read_json(info_path) or {}
    if "pages" not in data:
        # PDF copiado a mano en docs/: se calcula una vez y se guarda.
        data = {
            "pages": read_page_count(path),
            "uploaded": data.get("uploaded") or iso_from_timestamp(stat.st_mtime),
        }
        try:
            cache_dir().mkdir(parents=True, exist_ok=True)
            write_json_atomic(info_path, data)
        except OSError:
            pass
    uploaded = parse_iso(data.get("uploaded")) or datetime.fromtimestamp(stat.st_mtime)
    return {
        "name": name,
        "pages": data.get("pages"),
        "size": stat.st_size,
        "uploaded": uploaded,
        "version": int(stat.st_mtime),  # rompe la caché del navegador de la miniatura
    }


def list_docs() -> list[dict]:
    docs = [doc_info(p.stem) for p in DOCS_DIR.glob("*.pdf") if is_safe_name(p.stem)]
    docs.sort(key=lambda d: d["uploaded"], reverse=True)
    return docs


def unique_stem(stem: str) -> str:
    candidate, n = stem, 2
    while (DOCS_DIR / f"{candidate}.pdf").exists():
        candidate = f"{stem}-{n}"
        n += 1
    return candidate


def save_upload(file) -> str:
    """Valida y guarda un PDF subido. Devuelve el stem final; ValueError con el motivo."""
    original = file.filename or ""
    label = original or "archivo sin nombre"
    name = secure_filename(original)
    if not name.lower().endswith(".pdf"):
        raise ValueError(f"{label} no es un PDF")
    # "__" separa las partes de los nombres de resultado.
    stem = re.sub(r"_{2,}", "_", name[:-4])
    if not stem.strip("_."):
        raise ValueError(f"{label}: nombre de archivo no válido")

    head = file.stream.read(5)
    file.stream.seek(0)
    if head != b"%PDF-":
        raise ValueError(f"{label} no es un PDF")

    with UPLOAD_LOCK:
        DOCS_DIR.mkdir(exist_ok=True)
        stem = unique_stem(stem)
        path = DOCS_DIR / f"{stem}.pdf"
        file.save(path)

    cache_dir().mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        cache_dir() / f"{stem}.json",
        {"pages": read_page_count(path), "uploaded": iso_from_timestamp(time.time())},
    )
    return stem


def remove_doc_cache(name: str) -> None:
    for suffix in (".json", ".jpg"):
        (cache_dir() / f"{name}{suffix}").unlink(missing_ok=True)


def delete_doc(name: str, mode: str) -> int:
    """Borra el PDF (y con mode="all" también sus resultados). Devuelve cuántos resultados borró."""
    (DOCS_DIR / f"{name}.pdf").unlink(missing_ok=True)
    remove_doc_cache(name)
    removed = 0
    if mode == "all":
        for entry in results_for_doc(name):
            delete_result(entry["name"])
            removed += 1
    return removed


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"


@app.post("/upload")
def upload():
    files = [f for f in request.files.getlist("files") if f and f.filename]

    if wants_json():
        if not files:
            return jsonify({"ok": False, "error": "No se recibió ningún archivo"}), 400
        try:
            return jsonify({"ok": True, "name": save_upload(files[0])})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    if not files:
        flash("No se seleccionó ningún archivo", "error")
        return redirect(url_for("index"))

    saved, rejected = [], []
    for file in files:
        try:
            saved.append(save_upload(file))
        except ValueError as exc:
            rejected.append(str(exc))

    message = plural(len(saved), "subido")
    if rejected:
        message += f" · {plural(len(rejected), 'rechazado')}: " + "; ".join(rejected)
    flash(message, "error" if rejected else "success")

    if len(saved) == 1 and len(files) == 1:
        return redirect(url_for("doc_page", name=saved[0]))
    return redirect(url_for("index"))


@app.errorhandler(RequestEntityTooLarge)
def too_large(_exc):
    message = "La subida supera el límite de 500 MB"
    if wants_json():
        return jsonify({"ok": False, "error": message}), 413
    flash(message, "error")
    return redirect(url_for("index"))


@app.get("/docs/<name>")
def doc_page(name):
    valid_doc(name)
    provider = get_provider(load_config_or_empty())
    results = results_for_doc(name)
    return render_template(
        "doc.html",
        doc=doc_info(name),
        results=sort_group(results, primary_first=True),
        jobs=[job_summary(j) for j in visible_jobs(name)],
        provider=provider,
        default_info=providers.get_cached_model(provider.get("model") or ""),
        folders=all_folders(),
    )


@app.get("/docs/<name>/file")
def doc_file(name):
    valid_doc(name)
    return send_from_directory(DOCS_DIR, f"{name}.pdf", mimetype="application/pdf")


@app.get("/docs/<name>/thumb")
def doc_thumb(name):
    path = valid_doc(name)
    thumb = cache_dir() / f"{name}.jpg"
    if not thumb.exists():
        try:
            images = convert_from_path(
                str(path), first_page=1, last_page=1, size=(THUMB_WIDTH, None)
            )
        except Exception:
            abort(404)
        if not images:
            abort(404)
        cache_dir().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir(), suffix=".jpg")
        os.close(fd)
        try:
            images[0].convert("RGB").save(tmp, "JPEG", quality=80)
            os.replace(tmp, thumb)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return send_file(thumb, mimetype="image/jpeg", max_age=86400)


def confirmed() -> bool:
    return request.form.get("confirm") in ("1", "on", "yes")


@app.post("/docs/<name>/delete")
def doc_delete(name):
    valid_doc(name)
    mode = request.form.get("mode")
    if mode not in ("pdf", "all"):
        return "Modo de borrado no válido", 400
    if not confirmed():
        flash("Marca «Confirmo el borrado» para borrar", "error")
        return redirect(url_for("doc_page", name=name))
    if doc_has_active_job(name):
        flash(f"{name} tiene un trabajo en curso; no se puede borrar todavía", "error")
        return redirect(url_for("doc_page", name=name))

    removed = delete_doc(name, mode)
    if mode == "all":
        flash(f"Borrado {name}.pdf y {plural(removed, 'resultado')}", "success")
    else:
        flash(f"Borrado {name}.pdf (se conservan sus resultados)", "success")
    return redirect(url_for("index"))


@app.post("/docs/bulk")
def docs_bulk():
    names = [n for n in dict.fromkeys(request.form.getlist("names")) if doc_exists(n)]
    action = request.form.get("action")
    if not names:
        flash("No hay documentos seleccionados", "error")
        return redirect(url_for("index"))

    if action == "process":
        try:
            model, reasoning, provider = prepare_run(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("index"))
        for name in names:
            start_job(name, model, reasoning, provider)
        flash(f"{plural(len(names), 'documento')} en cola con {model}", "success")
        return redirect(url_for("index"))

    if action in ("delete_pdf", "delete_all"):
        if not confirmed():
            flash("Marca «Confirmo el borrado» para borrar", "error")
            return redirect(url_for("index"))
        mode = "all" if action == "delete_all" else "pdf"
        deleted, skipped, removed = [], [], 0
        for name in names:
            if doc_has_active_job(name):
                skipped.append(name)
                continue
            removed += delete_doc(name, mode)
            deleted.append(name)
        message = f"{plural(len(deleted), 'PDF borrado', 'PDFs borrados')}"
        if mode == "all":
            message += f" y {plural(removed, 'resultado')}"
        if skipped:
            message += f" · omitidos por tener un trabajo en curso: {', '.join(skipped)}"
        flash(message, "error" if skipped else "success")
        return redirect(url_for("index"))

    return "Acción no válida", 400
