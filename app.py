import json
import os
import re
import secrets
import socket
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
# Anclados al proyecto; en Docker se apuntan al volumen de datos por entorno.
CONFIG_PATH = Path(os.environ.get("NOT_CR_CONFIG_PATH") or BASE_DIR / "config.json")
DOCS_DIR = Path(os.environ.get("NOT_CR_DOCS_DIR") or BASE_DIR / "docs")
OUTPUT_DIR = Path(os.environ.get("NOT_CR_OUTPUT_DIR") or BASE_DIR / "output")


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


@app.url_defaults
def static_version(endpoint, values):
    """`?v=<mtime>` en los estáticos: el proxy y el navegador los cachean por URL."""
    if endpoint == "static" and "filename" in values:
        try:
            values["v"] = int((Path(app.static_folder) / values["filename"]).stat().st_mtime)
        except OSError:
            pass


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
            # «saving» no trae total/índice: se conservan los de la última página.
            job["progress"] = {**job["progress"], **payload}
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


# ── Resultados ───────────────────────────────────────────────────────────────

OUTPUT_TS_RE = re.compile(r"^\d{8}-\d{6}$")
NO_FOLDER = "/"  # valor de filtro «Sin carpeta» (una carpeta nunca contiene "/")
SORTS = {
    "date_desc": "Más recientes",
    "date_asc": "Más antiguos",
    "doc": "Documento",
    "model": "Modelo",
    "title": "Título",
}


def parse_output_name(name: str) -> tuple[str, str, str | None]:
    """`{doc}__{slug}__{ts}` → (doc, slug, ts); un nombre legacy → (name, "", None)."""
    parts = name.rsplit("__", 2)
    if len(parts) == 3 and parts[0] and OUTPUT_TS_RE.match(parts[2]):
        return parts[0], parts[1], parts[2]
    return name, "", None


def result_entry(name: str) -> dict:
    md_path = OUTPUT_DIR / f"{name}.md"
    meta = read_json(meta_path(name)) or {}
    doc, slug, ts = parse_output_name(name)
    doc = meta.get("doc") or doc
    model = meta.get("model") or slug

    date = parse_iso(meta.get("finished"))
    if date is None and ts:
        date = datetime.strptime(ts, OUTPUT_TS_FORMAT)
    if date is None:
        date = datetime.fromtimestamp(md_path.stat().st_mtime)

    return {
        "name": name,
        "doc": doc,
        "model": model,
        "date": date,
        "title": meta.get("title") or "",
        "note": meta.get("note") or "",
        "folder": meta.get("folder") or "",
        "primary": bool(meta.get("primary")),
        "status": meta.get("status") or "done",
        "truncated": len(meta.get("truncated_pages") or []),
        "doc_exists": doc_exists(doc),
        "meta": meta,
    }


def list_results() -> list[dict]:
    entries = []
    for path in OUTPUT_DIR.glob("*.md"):
        name = path.name[:-3]
        if is_safe_name(name):
            entries.append(result_entry(name))
    return entries


def results_for_doc(doc: str) -> list[dict]:
    # Filtro exacto: «uno» no debe ver los resultados de «uno_dos».
    return [entry for entry in list_results() if entry["doc"] == doc]


def valid_result(name: str) -> Path:
    path = OUTPUT_DIR / f"{name}.md"
    if not is_safe_name(name) or not path.is_file():
        abort(404)
    return path


def minimal_meta(name: str) -> dict:
    doc, slug, _ts = parse_output_name(name)
    return {"doc": doc, "model": slug, "title": "", "note": "", "folder": "", "primary": False}


def update_meta(name: str, **changes) -> dict:
    """Actualiza (o crea, para resultados sin sidecar) el sidecar de un resultado."""
    with META_LOCK:
        meta = read_json(meta_path(name)) or minimal_meta(name)
        meta.update(changes)
        write_json_atomic(meta_path(name), meta)
    return meta


def delete_result(name: str) -> None:
    (OUTPUT_DIR / f"{name}.md").unlink(missing_ok=True)
    meta_path(name).unlink(missing_ok=True)


def sort_entries(entries: list[dict], sort: str) -> list[dict]:
    if sort == "date_asc":
        return sorted(entries, key=lambda e: e["date"])
    if sort == "doc":
        return sorted(entries, key=lambda e: (e["doc"].lower(), -e["date"].timestamp()))
    if sort == "model":
        return sorted(entries, key=lambda e: (e["model"].lower(), -e["date"].timestamp()))
    if sort == "title":
        return sorted(entries, key=lambda e: ((e["title"] or e["doc"]).lower(), -e["date"].timestamp()))
    return sorted(entries, key=lambda e: e["date"], reverse=True)


def sort_group(entries: list[dict], primary_first: bool, sort: str = "date_desc") -> list[dict]:
    ordered = sort_entries(entries, sort)
    if primary_first:
        ordered.sort(key=lambda e: not e["primary"])  # sort estable: el resto conserva su orden
    return ordered


# ── Carpetas ─────────────────────────────────────────────────────────────────


def folders_path() -> Path:
    return OUTPUT_DIR / "_folders.json"


def load_folder_list() -> list[str]:
    data = read_json(folders_path()) or {}
    return [f for f in data.get("folders") or [] if isinstance(f, str)]


def all_folders(results: list[dict] | None = None) -> list[str]:
    """Carpetas de _folders.json más las que solo aparecen en sidecars."""
    if results is None:
        results = list_results()
    folders = set(load_folder_list()) | {e["folder"] for e in results if e["folder"]}
    return sorted(folders, key=str.lower)


def save_folder_list(folders: list[str]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    known = set(folders) | {e["folder"] for e in list_results() if e["folder"]}
    write_json_atomic(folders_path(), {"folders": sorted(known, key=str.lower)})


def clean_folder_name(value: str) -> str | None:
    name = (value or "").strip()
    if 1 <= len(name) <= 60 and "/" not in name:
        return name
    return None


def ensure_folder(name: str) -> None:
    if name and name not in load_folder_list():
        with META_LOCK:
            save_folder_list(load_folder_list() + [name])


# ── Portada ──────────────────────────────────────────────────────────────────


def filter_results(entries: list[dict], q: str, model: str, folder: str) -> list[dict]:
    if q:
        needle = q.lower()
        entries = [
            e
            for e in entries
            if needle in " ".join((e["title"], e["name"], e["doc"], e["note"])).lower()
        ]
    if model:
        entries = [e for e in entries if e["model"] == model]
    if folder == NO_FOLDER:
        entries = [e for e in entries if not e["folder"]]
    elif folder:
        entries = [e for e in entries if e["folder"] == folder]
    return entries


def group_results(entries: list[dict], view: str, sort: str) -> list[dict]:
    ordered = sort_entries(entries, sort)
    groups: dict[str, dict] = {}
    for entry in ordered:
        if view == "folder":
            key = entry["folder"] or NO_FOLDER
            label = entry["folder"] or "Sin carpeta"
            group = groups.setdefault(key, {"key": key, "label": label, "doc": None, "results": []})
        else:
            key = entry["doc"]
            group = groups.setdefault(
                key,
                {"key": key, "label": key, "doc": key, "doc_exists": entry["doc_exists"], "results": []},
            )
        group["results"].append(entry)

    result = list(groups.values())
    if view == "folder":
        # «Sin carpeta» al final, el resto por nombre.
        result.sort(key=lambda g: (g["key"] == NO_FOLDER, g["label"].lower()))
    else:
        for group in result:
            group["results"].sort(key=lambda e: not e["primary"])
    return result


@app.get("/")
def index():
    q = (request.args.get("q") or "").strip()
    model = request.args.get("model") or ""
    folder = request.args.get("folder") or ""
    sort = request.args.get("sort") if request.args.get("sort") in SORTS else "date_desc"
    view = "folder" if request.args.get("view") == "folder" else "doc"

    results = list_results()
    docs = list_docs()
    counts: dict[str, int] = {}
    for entry in results:
        counts[entry["doc"]] = counts.get(entry["doc"], 0) + 1
    for doc in docs:
        doc["results"] = counts.get(doc["name"], 0)

    filtered = filter_results(results, q, model, folder)
    provider = get_provider(load_config_or_empty())
    return render_template(
        "index.html",
        docs=docs,
        groups=group_results(filtered, view, sort),
        results_total=len(results),
        results_shown=len(filtered),
        models=sorted({e["model"] for e in results if e["model"]}, key=str.lower),
        folders=all_folders(results),
        filters={"q": q, "model": model, "folder": folder, "sort": sort, "view": view},
        sorts=SORTS,
        no_folder=NO_FOLDER,
        jobs=[job_summary(j) for j in visible_jobs()],
        provider=provider,
        total_pages=sum(d["pages"] or 0 for d in docs),
    )


# ── Vista de resultado ───────────────────────────────────────────────────────

MD_EXTENSIONS = ["extra", "pymdownx.arithmatex"]
MD_EXTENSION_CONFIGS = {"pymdownx.arithmatex": {"generic": True}}
SANITIZE_TAGS = (set(nh3.ALLOWED_TAGS) - {"img"}) | {"sup", "sub", "hr", "dl", "dt", "dd", "abbr"}
SANITIZE_ATTRIBUTES = {tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()}
for _tag, _attrs in {
    "span": {"class"},
    "div": {"class"},
    "code": {"class"},
    "pre": {"class"},
    "a": {"class", "href", "title"},
    "sup": {"id"},
    "li": {"id"},
    "ol": {"start"},
    "th": {"style"},
    "td": {"style"},
    "abbr": {"title"},
}.items():
    SANITIZE_ATTRIBUTES.setdefault(_tag, set()).update(_attrs)
SANITIZE_ATTRIBUTES.pop("img", None)
IMAGE_NOTE_RE = re.compile(r"\(Imagen:([^()<]*)\)")


def render_markdown(text: str) -> str:
    """Markdown → HTML saneado; el LaTeX queda intacto para MathJax (.arithmatex)."""
    html = markdown.markdown(
        text, extensions=MD_EXTENSIONS, extension_configs=MD_EXTENSION_CONFIGS
    )
    # Marcadores «(Imagen: …)» del prompt como nota discreta.
    html = IMAGE_NOTE_RE.sub(r'<span class="img-note">(Imagen:\1)</span>', html)
    return nh3.clean(
        html,
        tags=SANITIZE_TAGS,
        attributes=SANITIZE_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        filter_style_properties={"text-align"},
    )


@app.get("/output/<name>")
def output_page(name):
    path = valid_result(name)
    entry = result_entry(name)
    text = path.read_text(encoding="utf-8")
    meta = entry["meta"]
    started, finished = parse_iso(meta.get("started")), parse_iso(meta.get("finished"))
    return render_template(
        "output.html",
        entry=entry,
        meta=meta,
        source=text,
        html=render_markdown(text),
        tokens=usage_totals(meta.get("usage") or []),
        duration=(finished - started).total_seconds() if started and finished else None,
        folders=all_folders(),
        doc=doc_info(entry["doc"]) if entry["doc_exists"] else None,
    )


@app.get("/output/<name>/raw")
def output_raw(name):
    valid_result(name)
    return send_from_directory(OUTPUT_DIR, f"{name}.md", as_attachment=True)


@app.post("/output/<name>/edit")
def output_edit(name):
    valid_result(name)
    changes = {}
    if "title" in request.form:
        changes["title"] = request.form["title"].strip()[:200]
    if "note" in request.form:
        changes["note"] = request.form["note"].strip()[:1000]
    if "folder" in request.form:
        folder = request.form["folder"].strip()
        if folder and clean_folder_name(folder) is None:
            message = "Nombre de carpeta no válido (1–60 caracteres, sin «/»)"
            if wants_json():
                return jsonify({"ok": False, "error": message}), 400
            flash(message, "error")
            return redirect(url_for("output_page", name=name))
        changes["folder"] = folder
        ensure_folder(folder)
    update_meta(name, **changes)

    if wants_json():
        return jsonify({"ok": True, **changes})
    flash("Cambios guardados", "success")
    return redirect(safe_next(url_for("output_page", name=name)))


@app.post("/output/<name>/primary")
def output_primary(name):
    valid_result(name)
    entry = result_entry(name)
    make_primary = not entry["primary"]
    for other in results_for_doc(entry["doc"]):
        if other["name"] != name and other["primary"]:
            update_meta(other["name"], primary=False)
    update_meta(name, primary=make_primary)
    if wants_json():
        return jsonify({"ok": True, "primary": make_primary})
    return redirect(safe_next(url_for("output_page", name=name)))


@app.post("/output/<name>/delete")
def output_delete(name):
    valid_result(name)
    if not confirmed():
        flash("Marca «Confirmo el borrado» para borrar", "error")
        return redirect(url_for("output_page", name=name))
    delete_result(name)
    flash("Resultado borrado", "success")
    default = url_for("index") + "#resultados"
    target = safe_next(default)
    # Volver a la página del resultado borrado daría 404.
    if target.split("?")[0].split("#")[0] == url_for("output_page", name=name):
        target = default
    return redirect(target)


@app.post("/output/bulk")
def output_bulk():
    names = [n for n in dict.fromkeys(request.form.getlist("names")) if is_safe_name(n)]
    names = [n for n in names if (OUTPUT_DIR / f"{n}.md").is_file()]
    action = request.form.get("action")
    target = safe_next(url_for("index") + "#resultados")
    if not names:
        flash("No hay resultados seleccionados", "error")
        return redirect(target)

    if action == "move":
        folder = (request.form.get("folder") or "").strip()
        if folder and clean_folder_name(folder) is None:
            flash("Nombre de carpeta no válido", "error")
            return redirect(target)
        ensure_folder(folder)
        for name in names:
            update_meta(name, folder=folder)
        where = f"«{folder}»" if folder else "«Sin carpeta»"
        flash(f"{plural(len(names), 'resultado')} movidos a {where}", "success")
        return redirect(target)

    if action == "delete":
        if not confirmed():
            flash("Marca «Confirmo el borrado» para borrar", "error")
            return redirect(target)
        for name in names:
            delete_result(name)
        flash(f"{plural(len(names), 'resultado borrado', 'resultados borrados')}", "success")
        return redirect(target)

    return "Acción no válida", 400


@app.post("/folders")
def folder_create():
    target = safe_next(url_for("index") + "#resultados")
    name = clean_folder_name(request.form.get("name"))
    if name is None:
        flash("Nombre de carpeta no válido (1–60 caracteres, sin «/»)", "error")
        return redirect(target)
    if name in all_folders():
        flash(f"La carpeta «{name}» ya existe", "error")
        return redirect(target)
    with META_LOCK:
        save_folder_list(load_folder_list() + [name])
    flash(f"Carpeta «{name}» creada", "success")
    return redirect(target)


@app.post("/folders/rename")
def folder_rename():
    target = safe_next(url_for("index") + "#resultados")
    old = (request.form.get("old") or "").strip()
    new = clean_folder_name(request.form.get("new"))
    folders = all_folders()
    if old not in folders:
        flash(f"La carpeta «{old}» no existe", "error")
        return redirect(target)
    if new is None:
        flash("Nombre de carpeta no válido (1–60 caracteres, sin «/»)", "error")
        return redirect(target)
    if new != old and new in folders:
        flash(f"La carpeta «{new}» ya existe", "error")
        return redirect(target)

    for entry in list_results():
        if entry["folder"] == old:
            update_meta(entry["name"], folder=new)
    with META_LOCK:
        save_folder_list([f for f in load_folder_list() if f != old] + [new])
    flash(f"Carpeta «{old}» renombrada a «{new}»", "success")
    if "folder=" in target:
        target = url_for("index", folder=new) + "#resultados"
    return redirect(target)


@app.post("/folders/delete")
def folder_delete():
    target = safe_next(url_for("index") + "#resultados")
    name = (request.form.get("name") or "").strip()
    if name not in all_folders():
        flash(f"La carpeta «{name}» no existe", "error")
        return redirect(target)
    # Nunca borra resultados: pasan a «Sin carpeta».
    moved = 0
    for entry in list_results():
        if entry["folder"] == name:
            update_meta(entry["name"], folder="")
            moved += 1
    with META_LOCK:
        OUTPUT_DIR.mkdir(exist_ok=True)
        write_json_atomic(folders_path(), {"folders": [f for f in load_folder_list() if f != name]})
    flash(f"Carpeta «{name}» eliminada · {plural(moved, 'resultado')} sin carpeta", "success")
    if "folder=" in target:
        target = url_for("index") + "#resultados"
    return redirect(target)


# ── Catálogo de modelos y configuración ──────────────────────────────────────


def provider_error_message(exc: Exception) -> str:
    text = str(exc)
    if "Missing credentials" in text or "api_key" in text:
        return "Falta la API key de OpenRouter (configúrala en Configuración)"
    return f"{type(exc).__name__}: {text}"


def key_hint(api_key: str) -> str:
    if not api_key:
        return "sin key"
    return f"guardada (…{api_key[-4:]})"


@app.get("/api/models")
def api_models():
    provider = get_provider(load_config_or_empty())
    try:
        models = providers.list_models(provider["api-key"], provider["api-url"])
    except Exception as exc:
        response = jsonify({"error": provider_error_message(exc)})
        response.status_code = 502
    else:
        response = jsonify(
            [
                model
                | {
                    "price_prompt": providers.format_price(model["pricing"].get("prompt")),
                    "price_completion": providers.format_price(model["pricing"].get("completion")),
                    "context": providers.format_context(model["context_length"]),
                }
                for model in models
            ]
        )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/config")
def config_page():
    provider = get_provider(load_config_or_empty())
    return render_template(
        "config.html",
        provider={k: v for k, v in provider.items() if k != "api-key"},
        key_hint=key_hint(provider.get("api-key") or ""),
        has_key=bool(provider.get("api-key")),
        config_exists=CONFIG_PATH.exists(),
    )


@app.post("/config")
def config_save():
    config = load_config_or_empty()
    current = dict(config.get("openrouter") or {})  # conserva claves ajenas
    provider = get_provider(config)

    current["api-url"] = (request.form.get("api-url") or "").strip() or PROVIDER_DEFAULTS["api-url"]
    current["model"] = (request.form.get("model") or "").strip() or provider["model"]
    reasoning = (request.form.get("reasoning") or "").strip()
    current["reasoning"] = "" if reasoning == "off" else reasoning

    new_key = (request.form.get("api-key") or "").strip()
    if request.form.get("clear_key"):
        current["api-key"] = ""
    elif new_key:
        current["api-key"] = new_key
    else:
        current["api-key"] = provider.get("api-key") or ""

    config["openrouter"] = current
    save_config(config)
    providers.invalidate_cache()
    flash("Configuración guardada", "success")
    return redirect(url_for("config_page"))


@app.post("/config/test")
def config_test():
    provider = get_provider(load_config_or_empty())
    base_url = (request.form.get("api-url") or "").strip() or provider["api-url"]
    api_key = (request.form.get("api-key") or "").strip() or provider["api-key"]

    try:
        # Sin caché: una key incorrecta no puede dar «OK» con datos cacheados.
        models = providers.list_models(api_key, base_url, use_cache=False)
        # /models es público; /key es lo que valida la key de verdad.
        key_data = providers.check_key(api_key, base_url)
    except Exception as exc:
        ok, message = False, f"Error: {provider_error_message(exc)}"
    else:
        ok = True
        message = f"Conexión OK: {len(models)} modelos disponibles"
        label = key_data.get("label")
        # En keys sin nombre, OpenRouter usa como etiqueta un trozo de la propia key.
        if label and not label.startswith("sk-"):
            message += f" · key «{label}»"

    if wants_json():
        return jsonify({"ok": ok, "message": message})
    flash(message, "success" if ok else "error")
    return redirect(url_for("config_page"))


# ── Seguridad ────────────────────────────────────────────────────────────────

DEFAULT_ALLOWED_HOSTS = {"arenarium", "localhost", "127.0.0.1"}


def load_allowed_hosts() -> set[str]:
    extra = os.environ.get("NOT_CR_ALLOWED_HOSTS", "")
    return DEFAULT_ALLOWED_HOSTS | {h.strip().lower().rstrip(".") for h in extra.split(",") if h.strip()}


ALLOWED_HOSTS = load_allowed_hosts()


def host_without_port(host: str) -> str:
    host = (host or "").lower()
    if host.startswith("["):  # IPv6 literal
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0].rstrip(".")


@app.before_request
def check_host_and_origin():
    # Host permitido: protege contra DNS rebinding.
    if host_without_port(request.host) not in ALLOWED_HOSTS:
        abort(400)
    # Origen en POST: una web cualquiera no puede enviar formularios a la app.
    if request.method == "POST":
        source = request.headers.get("Origin") or request.headers.get("Referer")
        if source and urlparse(source).netloc.lower() != request.host.lower():
            abort(403)


# ── Plantillas ───────────────────────────────────────────────────────────────

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def humanize_date(value, now: datetime | None = None) -> str:
    if isinstance(value, str):
        value = parse_iso(value)
    if value is None:
        return ""
    now = now or datetime.now()
    seconds = (now - value).total_seconds()
    days = (now.date() - value.date()).days
    if seconds < 60:
        return "ahora mismo"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if days == 0:
        return f"hace {int(seconds // 3600)} h"
    if days == 1:
        return "ayer"
    if days < 7:
        return f"hace {days} días"
    if value.year == now.year:
        return f"{value.day} {MONTHS[value.month - 1]}"
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def full_date(value) -> str:
    if isinstance(value, str):
        value = parse_iso(value)
    if value is None:
        return ""
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}, {value:%H:%M}"


def filesize(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            text = f"{n:.0f}" if unit == "B" else f"{n:.1f}"
            return f"{text.replace('.', ',')} {unit}"
        n /= 1024
    return ""


def duration(seconds) -> str:
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def money(value) -> str:
    if value is None:
        return ""
    if value < 0.01:
        return f"≈ ${value:.4f}"
    return f"≈ ${value:.2f}"


def thousands(n) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


app.jinja_env.filters.update(
    humanize=humanize_date,
    full_date=full_date,
    filesize=filesize,
    duration=duration,
    money=money,
    thousands=thousands,
    model_short=model_short,
    model_hue=model_hue,
    price=providers.format_price,
    context=providers.format_context,
)


@app.context_processor
def sidebar_context():
    """Datos de la barra lateral: carpetas con contador y trabajos activos."""
    if request.endpoint in (None, "static") or request.endpoint.startswith("api_"):
        return {}
    results = list_results()
    counts: dict[str, int] = {}
    for entry in results:
        if entry["folder"]:
            counts[entry["folder"]] = counts.get(entry["folder"], 0) + 1
    with JOBS_LOCK:
        active = [j for j in jobs.values() if j["status"] in ACTIVE_STATUSES]
    return {
        "sidebar_folders": [(f, counts.get(f, 0)) for f in all_folders(results)],
        "sidebar_active": len(active),
        "sidebar_percent": (
            round(sum(job_summary(j)["percent"] for j in active) / len(active)) if active else 0
        ),
        "no_folder": NO_FOLDER,
    }


# ── Arranque (tailnet) ───────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("NOT_CR_HOST", "arenarium")
    port = int(os.environ.get("NOT_CR_PORT", "5000"))
    try:
        # Werkzeug captura los errores de bind y sale sin explicar nada, así que
        # se comprueba antes que el nombre resuelve (MagicDNS requiere Tailscale).
        socket.getaddrinfo(host, port)
        app.run(host=host, port=port, debug=False, threaded=True)
    except OSError as exc:  # incluye socket.gaierror
        sys.exit(
            f"No se pudo escuchar en {host}:{port} ({exc}). "
            "¿Está Tailscale levantado? Para uso solo local: "
            "NOT_CR_HOST=127.0.0.1"
        )
