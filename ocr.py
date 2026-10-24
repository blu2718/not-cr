import time

import processing_images
import processing_pdf
import processing_text

CONTEXT_CHARS = 250


def process_pdf(
    pdf_path,
    image_dir,
    api_key,
    model,
    on_chunk=None,
    base_url=processing_images.DEFAULT_BASE_URL,
    reasoning=None,
    on_event=None,
) -> str:
    """Transcribe un PDF página a página y devuelve el Markdown completo."""

    def emit(kind, payload):
        if on_event:
            on_event(kind, payload)

    emit("log", "Convirtiendo PDF a imágenes")
    emit("status", {"step": "converting"})
    processing_pdf.convert(pdf_path, image_dir)

    pages = processing_images.image_prep(image_dir)
    n = len(pages)
    emit("log", f"└ {n} páginas")
    emit("status", {"step": "page", "index": 0, "total": n})

    acumulado = ""

    for i, page in enumerate(pages, start=1):
        emit("log", f"├ Construyendo mensaje (página {i}/{n})")
        emit("status", {"step": "page", "index": i, "total": n})
        context = acumulado[-CONTEXT_CHARS:] if i > 1 else None
        message = processing_images.message_build(page, context)

        emit("log", "├ Enviando solicitud al modelo")
        emit("log", "├ Esperando respuesta del modelo")
        start = time.monotonic()
        response = processing_images.to_model(
            api_key, model, message, base_url=base_url, reasoning=reasoning
        )
        segundos = time.monotonic() - start
        emit("log", f"└ Respuesta recibida ({segundos:.1f} s)")
        emit("page_time", segundos)
        if response.get("usage"):
            emit("usage", response["usage"])

        choice = response["choices"][0]
        if choice.get("finish_reason") == "length":
            emit(
                "warning",
                {
                    "page": i,
                    "total": n,
                    "message": f"├ ⚠ Respuesta truncada por límite de tokens (página {i}/{n})",
                },
            )

        cleaned = processing_text.clean_markdown(choice["message"]["content"] or "")
        if not cleaned:
            continue

        if acumulado:
            piece = processing_text.markdown_separator(acumulado, cleaned) + cleaned
        else:
            piece = cleaned

        acumulado += piece
        if on_chunk:
            on_chunk(piece)

    return acumulado
