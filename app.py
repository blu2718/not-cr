import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import markdown as markdown_lib
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)
from werkzeug.utils import secure_filename

import main
import processing_images
import providers


CONFIG_PATH = Path("config.json")
DOCS_DIR = Path("docs")
OUTPUT_DIR = Path("output")

DEFAULT_PROVIDERS = {
    "openrouter": {
        "api-url": "https://openrouter.ai/api/v1",
        "api-key": "",
        "model": "qwen/qwen3-vl-235b-a22b-instruct",
        "reasoning": "low",
    },
    "opencode": {
        "api-url": "https://opencode.ai/zen/go/v1",
        "api-key": "",
        "model": "",
        "reasoning": "low",
    },
}


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("NOT_CR_SECRET", "not-cr-local")

jobs: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()


@app.template_filter("filesize")
def format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


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


def get_active_provider(config: dict) -> dict:
    try:
        active = config["active"]
    except KeyError as exc:
        raise KeyError("Configuration is missing the active provider") from exc

    try:
        return config[active]
    except KeyError as exc:
        raise KeyError(f"Configuration has no provider named {active!r}") from exc


def _provider_names(config: dict) -> list[str]:
    names = list(DEFAULT_PROVIDERS)
    names.extend(
        name
        for name, value in config.items()
        if name not in names and name != "active" and isinstance(value, dict)
    )
    return names


def _config_for_form(config: dict) -> dict:
    result = dict(config)
    result.setdefault("active", "openrouter")
    for name, defaults in DEFAULT_PROVIDERS.items():
        provider = dict(defaults)
        provider.update(config.get(name, {}))
        result[name] = provider
    return result


def _active_provider(config: dict) -> tuple[str, dict]:
    active = config.get("active", "openrouter")
    provider = config.get(active)
    if not isinstance(provider, dict):
        raise KeyError(f"Configuration has no provider named {active!r}")
    return active, provider


def _ensure_dirs() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_stem(name: str) -> str:
    candidate = secure_filename(name or "")
    if candidate.lower().endswith(".pdf"):
        candidate = candidate[:-4]
    if not candidate or candidate in {".", ".."} or "/" in candidate:
        abort(404)
    return candidate


def _safe_output_name(name: str) -> str:
    candidate = secure_filename(name or "")
    if candidate.lower().endswith(".md"):
        candidate = candidate[:-3]
    if not candidate or candidate in {".", ".."} or "/" in candidate:
        abort(404)
    return candidate


def _pdf_path(stem: str) -> Path:
    return DOCS_DIR / f"{stem}.pdf"


def _document_has_running_job(stem: str) -> bool:
    with JOBS_LOCK:
        return any(
            job.get("status") == "running" and job.get("doc") == stem
            for job in jobs.values()
        )


def _result_meta_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _read_meta(path: Path) -> dict | None:
    try:
        value = json.loads(_result_meta_path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


RESULT_PATTERN = re.compile(
    r"^(?P<doc>.+)__(?P<tag>.+)__(?P<timestamp>\d{8}-\d{6})$"
)


def _result_date(value: str | None) -> tuple[str, float]:
    if not value:
        return "", 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M"), parsed.timestamp()
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y%m%d-%H%M%S")
        except ValueError:
            return value, 0.0
        return parsed.strftime("%Y-%m-%d %H:%M"), parsed.timestamp()


def list_results() -> list[dict]:
    _ensure_dirs()
    results = []
    for path in OUTPUT_DIR.glob("*.md"):
        parsed = RESULT_PATTERN.match(path.stem)
        meta = _read_meta(path)
        parsed_doc = parsed.group("doc") if parsed else path.stem
        parsed_tag = parsed.group("tag") if parsed else None
        parsed_timestamp = parsed.group("timestamp") if parsed else None

        doc = (meta or {}).get("doc") or parsed_doc
        model = (meta or {}).get("model") or parsed_tag
        finished = (meta or {}).get("finished") or parsed_timestamp
        date, sort_value = _result_date(finished)
        if not sort_value:
            try:
                sort_value = path.stat().st_mtime
            except OSError:
                sort_value = 0.0

        results.append(
            {
                "name": path.stem,
                "doc": doc,
                "model": model,
                "tag": model or "sin etiqueta",
                "provider": (meta or {}).get("provider"),
                "finished": finished,
                "date": date,
                "meta": meta,
                "sort_value": sort_value,
            }
        )

    return sorted(results, key=lambda result: result["sort_value"], reverse=True)


def _result_belongs_to(result: dict, stem: str) -> bool:
    return result["doc"] == stem or result["name"].startswith(f"{stem}__")


def _model_pricing(provider: dict, model: str) -> dict | None:
    base_url = provider.get("api-url", "")
    cached = providers.MODEL_CACHE.get(base_url)
    if cached is None or time.monotonic() - cached[0] >= providers.CACHE_TTL:
        return None
    for entry in cached[1]:
        if entry.get("id") == model:
            pricing = entry.get("pricing")
            return pricing if isinstance(pricing, dict) else None
    return None


def _model_reasoning_efforts(provider: dict, model: str) -> set[str] | None:
    base_url = provider.get("api-url", "")
    cached = providers.MODEL_CACHE.get(base_url)
    if cached is None or time.monotonic() - cached[0] >= providers.CACHE_TTL:
        return None
    for entry in cached[1]:
        if entry.get("id") == model:
            if entry.get("supports_reasoning") is False:
                return set()
            efforts = entry.get("reasoning_efforts")
            if isinstance(efforts, list):
                return set(efforts)
            return None
    return None


def _estimated_cost(usage: list[dict], pricing: dict | None) -> float | None:
    if not usage or not isinstance(pricing, dict):
        return None
    try:
        prompt_price = float(pricing["prompt"])
        completion_price = float(pricing["completion"])
    except (KeyError, TypeError, ValueError):
        return None

    total = 0.0
    for item in usage:
        try:
            prompt_tokens = int(item["prompt_tokens"])
            completion_tokens = int(item["completion_tokens"])
        except (KeyError, TypeError, ValueError):
            return None
        total += prompt_tokens * prompt_price + completion_tokens * completion_price
    return total


def write_meta(job: dict, provider: dict, reasoning) -> dict:
    finished_at = datetime.now(timezone.utc)
    started_at = datetime.fromtimestamp(job["started"], timezone.utc)
    provider_name = (
        job.get("provider")
        or provider.get("name")
        or provider.get("provider")
        or "desconocido"
    )
    with JOBS_LOCK:
        usage = list(job.get("usage", []))
        pricing = job.get("pricing")
        job["finished"] = finished_at.timestamp()

    metadata = {
        "doc": job["doc"],
        "model": job["model"],
        "provider": provider_name,
        "reasoning": reasoning,
        "started": started_at.isoformat(),
        "finished": finished_at.isoformat(),
        "usage": usage,
        "estimated_cost": _estimated_cost(usage, pricing),
    }
    _ensure_dirs()
    (_result_meta_path(OUTPUT_DIR / f'{job["output_name"]}.md')).write_text(
        json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    return metadata


def _run_job(job: dict, pdf_path, provider: dict, model: str, reasoning) -> None:
    def emit(kind, payload) -> None:
        serialized = json.dumps(payload, ensure_ascii=False)
        with JOBS_LOCK:
            job.setdefault("events", []).append((kind, serialized))

    def handle_event(kind, payload) -> None:
        if kind == "usage":
            with JOBS_LOCK:
                job.setdefault("usage", []).append(payload)
        emit(kind, payload)

    with JOBS_LOCK:
        job["status"] = "running"
        job.setdefault("usage", [])
        job.setdefault("events", [])

    try:
        main.run_ai_ocr(
            str(pdf_path),
            provider.get("api-key", ""),
            model,
            base_url=provider.get("api-url", processing_images.DEFAULT_BASE_URL),
            reasoning=reasoning,
            on_chunk=lambda piece: emit("chunk", piece),
            on_event=handle_event,
            output_name=job["output_name"],
        )
        write_meta(job, provider, reasoning)
        emit("log", "Listo!")
        emit("done", {"output": job["output_name"]})
        with JOBS_LOCK:
            job["status"] = "done"
    except Exception as exc:
        emit("error", f"{type(exc).__name__}: {exc}")
        with JOBS_LOCK:
            job["status"] = "error"


def _usage_totals(usage: list[dict]) -> dict:
    prompt = 0
    completion = 0
    for item in usage:
        try:
            prompt += int(item.get("prompt_tokens", 0))
            completion += int(item.get("completion_tokens", 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return {"prompt_tokens": prompt, "completion_tokens": completion}


@app.get("/")
def index():
    _ensure_dirs()
    results = list_results()
    documents = []
    for path in sorted(DOCS_DIR.glob("*.pdf"), key=lambda item: item.name.lower()):
        stem = path.stem
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        documents.append(
            {
                "name": stem,
                "filename": path.name,
                "size": size,
                "results_count": sum(
                    1 for result in results if _result_belongs_to(result, stem)
                ),
            }
        )

    grouped = {}
    for result in results:
        grouped.setdefault(result["doc"], []).append(result)
    document_names = {item["name"] for item in documents}
    result_groups = [
        {
            "doc": doc,
            "results": grouped[doc],
            "has_document": doc in document_names,
        }
        for doc in sorted(grouped, key=str.casefold)
    ]
    return render_template(
        "index.html", documents=documents, result_groups=result_groups
    )


@app.post("/upload")
def upload():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        flash("Selecciona un archivo PDF.", "error")
        return redirect(url_for("index"))
    if not uploaded.filename.lower().endswith(".pdf"):
        return "Solo se aceptan archivos PDF.", 400

    filename = secure_filename(uploaded.filename)
    stem = Path(filename).stem
    if not filename or not stem:
        return "Nombre de archivo no válido.", 400

    _ensure_dirs()
    target = _pdf_path(stem)
    uploaded.save(target)
    flash(f"PDF subido: {target.name}", "success")
    return redirect(url_for("document", name=stem))


@app.post("/docs/<name>/delete")
def delete_document(name):
    stem = _safe_stem(name)
    path = _pdf_path(stem)
    if not path.is_file():
        abort(404)
    if _document_has_running_job(stem):
        flash("No se puede borrar un documento mientras se está procesando.", "error")
        return redirect(url_for("document", name=stem))

    path.unlink()
    flash("Documento eliminado. Los resultados anteriores se conservaron.", "success")
    return redirect(url_for("index"))


@app.get("/docs/<name>")
def document(name):
    stem = _safe_stem(name)
    if not _pdf_path(stem).is_file():
        abort(404)
    config = _config_for_form(load_config())
    active_name, provider = _active_provider(config)
    results = [result for result in list_results() if _result_belongs_to(result, stem)]
    return render_template(
        "doc.html",
        document=stem,
        provider_name=active_name,
        provider=provider,
        results=results,
    )


@app.get("/docs/<name>/file")
def document_file(name):
    stem = _safe_stem(name)
    if not _pdf_path(stem).is_file():
        abort(404)
    return send_from_directory(
        str(DOCS_DIR), f"{stem}.pdf", as_attachment=False, mimetype="application/pdf"
    )


@app.get("/api/models")
def api_models():
    try:
        config = load_config()
        active_name = request.args.get("provider") or config.get("active", "openrouter")
        provider = config.get(active_name)
        if not isinstance(provider, dict):
            return jsonify({"error": f"Proveedor desconocido: {active_name}"}), 400
        api_key = provider.get("api-key", "")
        if not api_key:
            return jsonify({"error": "No hay API key configurada."}), 502
        models = providers.list_models(api_key, provider.get("api-url", ""))
        response = jsonify(models)
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


def _output_name(stem: str, model: str) -> str:
    timestamp = datetime.now()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    slug = slug.strip("_") or "model"
    candidate = f'{stem}__{slug}__{timestamp.strftime("%Y%m%d-%H%M%S")}'
    _ensure_dirs()
    while (OUTPUT_DIR / f"{candidate}.md").exists():
        timestamp += timedelta(seconds=1)
        candidate = f'{stem}__{slug}__{timestamp.strftime("%Y%m%d-%H%M%S")}'
    return candidate


@app.post("/process/<name>")
def process(name):
    stem = _safe_stem(name)
    pdf_path = _pdf_path(stem)
    if not pdf_path.is_file():
        abort(404)

    config = load_config()
    active_name, provider = _active_provider(config)
    model = request.form.get("model", "").strip() or str(provider.get("model", "")).strip()
    if not model:
        return "No hay un modelo seleccionado.", 400

    metadata_efforts = _model_reasoning_efforts(provider, model)
    allowed_efforts = metadata_efforts if metadata_efforts is not None else set(providers.REASONING_EFFORTS)
    reasoning_selection = request.form.get("reasoning", "")
    if metadata_efforts == set():
        reasoning = None
    elif reasoning_selection in {"off", "none"}:
        reasoning = None
    elif reasoning_selection in allowed_efforts:
        reasoning = reasoning_selection
    elif reasoning_selection == "":
        configured_reasoning = provider.get("reasoning") or None
        reasoning = None if configured_reasoning in {"off", "none"} else configured_reasoning
    else:
        return "Nivel de razonamiento no válido.", 400

    with JOBS_LOCK:
        for job_id, job in jobs.items():
            if (
                job.get("status") == "running"
                and job.get("doc") == stem
                and job.get("model") == model
            ):
                return redirect(url_for("job", job_id=job_id))

        output_name = _output_name(stem, model)
        job_id = uuid.uuid4().hex
        job = {
            "doc": stem,
            "model": model,
            "output_name": output_name,
            "status": "running",
            "events": [],
            "started": time.time(),
            "usage": [],
            "pricing": _model_pricing(provider, model),
            "provider": active_name,
            "reasoning": reasoning,
        }
        jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, pdf_path, {**provider, "name": active_name}, model, reasoning),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("job", job_id=job_id))


@app.get("/jobs/<job_id>")
def job(job_id):
    with JOBS_LOCK:
        current_job = jobs.get(job_id)
    if current_job is None:
        abort(404)
    with JOBS_LOCK:
        usage = list(current_job.get("usage", []))
    totals = _usage_totals(usage)
    return render_template("job.html", job_id=job_id, job=current_job, totals=totals)


@app.get("/jobs/<job_id>/stream")
def job_stream(job_id):
    with JOBS_LOCK:
        current_job = jobs.get(job_id)
    if current_job is None:
        abort(404)

    @stream_with_context
    def generate():
        event_index = 0
        last_ping = time.monotonic()
        while True:
            with JOBS_LOCK:
                new_events = list(current_job.get("events", [])[event_index:])
                event_index += len(new_events)
                status = current_job.get("status")

            for kind, payload in new_events:
                if not isinstance(payload, str):
                    payload = json.dumps(payload, ensure_ascii=False)
                yield f"event: {kind}\ndata: {payload}\n\n"

            with JOBS_LOCK:
                event_count = len(current_job.get("events", []))
                status = current_job.get("status")
            if status in {"done", "error"} and event_index >= event_count:
                break

            if not new_events:
                now = time.monotonic()
                if now - last_ping >= 15:
                    yield ": ping\n\n"
                    last_ping = now
                time.sleep(0.2)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/output/<name>/delete")
def delete_output(name):
    output_name = _safe_output_name(name)
    path = OUTPUT_DIR / f"{output_name}.md"
    if not path.is_file():
        abort(404)

    result = next(
        (item for item in list_results() if item["name"] == output_name), None
    )
    path.unlink()
    _result_meta_path(path).unlink(missing_ok=True)
    flash("Resultado eliminado.", "success")

    if request.form.get("back") == "document" and result and result.get("doc"):
        document_name = secure_filename(str(result["doc"]))
        if document_name.lower().endswith(".pdf"):
            document_name = document_name[:-4]
        if document_name and _pdf_path(document_name).is_file():
            return redirect(url_for("document", name=document_name))
    return redirect(url_for("index"))


@app.get("/output/<name>/raw")
def raw_output(name):
    output_name = _safe_output_name(name)
    path = OUTPUT_DIR / f"{output_name}.md"
    if not path.is_file():
        abort(404)
    return send_from_directory(str(OUTPUT_DIR), path.name, as_attachment=True)


@app.get("/output/<name>")
def output_view(name):
    output_name = _safe_output_name(name)
    path = OUTPUT_DIR / f"{output_name}.md"
    if not path.is_file():
        abort(404)
    content = path.read_text(encoding="utf-8")
    rendered = markdown_lib.markdown(
        content, extensions=["extra", "fenced_code", "tables"]
    )
    result = next(
        (item for item in list_results() if item["name"] == output_name),
        {"tag": "sin etiqueta", "provider": None, "date": "", "meta": None},
    )
    meta = result.get("meta") or {}
    return render_template(
        "output.html",
        output_name=output_name,
        rendered=rendered,
        result=result,
        meta=meta,
        totals=_usage_totals(meta.get("usage", [])),
    )


def _form_provider_value(provider_name: str, field: str, fallback: str = "") -> str:
    value = request.form.get(f"{provider_name}-{field}")
    if value is None:
        value = request.form.get(field)
    return fallback if value is None else value


@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        config = load_config()
        names = _provider_names(config)
        for provider_name in names:
            section = config.setdefault(provider_name, {})
            for field in ("api-url", "api-key", "model", "reasoning"):
                key = f"{provider_name}-{field}"
                if key in request.form:
                    section[field] = request.form[key]
        active = request.form.get("active")
        if active in names:
            config["active"] = active
        save_config(config)
        flash("Configuración guardada.", "success")
        return redirect(url_for("config_page"))

    config = _config_for_form(load_config())
    return render_template(
        "config.html", config=config, provider_names=_provider_names(config)
    )


@app.post("/config/test/<provider>")
def test_config(provider):
    config = _config_for_form(load_config())
    if provider not in _provider_names(config):
        abort(404)
    current = config.get(provider, {})
    api_url = _form_provider_value(provider, "api-url", current.get("api-url", ""))
    api_key = _form_provider_value(provider, "api-key", current.get("api-key", ""))
    try:
        models = providers.list_models(api_key, api_url)
        flash(f"Conexión OK: {len(models)} modelos disponibles.", "success")
    except Exception as exc:
        flash(f"Error: {exc}", "error")
    return redirect(url_for("config_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, threaded=True)
