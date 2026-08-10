import time

import processing_images
import processing_pdf
import processing_text


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
    def emit(kind, payload):
        if on_event is not None:
            on_event(kind, payload)

    emit("log", "Convirtiendo PDF a imágenes")
    emit("status", {"step": "converting"})
    processing_pdf.convert(pdf_path, image_dir)

    batches = processing_images.image_prep(image_dir)
    total_batches = len(batches)
    total_pages = sum(len(batch) for batch in batches)
    emit("log", f"{total_pages} páginas en {total_batches} lotes")
    emit("status", {"step": "batch", "index": 0, "total": total_batches})

    accumulated = ""
    for index, batch in enumerate(batches, start=1):
        emit("log", f"├ Construyendo mensaje (lote {index}/{total_batches})")
        emit(
            "status",
            {
                "step": "batch",
                "index": index,
                "total": total_batches,
                "pages": len(batch),
            },
        )
        context = None if index == 1 else accumulated[-250:]
        message = processing_images.message_build(batch, context)

        emit("log", "├ Enviando solicitud al modelo")
        started = time.monotonic()
        response = processing_images.to_model(
            api_key,
            model,
            message,
            base_url=base_url,
            reasoning=reasoning,
        )
        elapsed = time.monotonic() - started
        emit("log", f"└ Respuesta recibida ({elapsed:.1f} s)")
        usage = response.get("usage")
        if usage is not None:
            emit("usage", usage)

        content = response["choices"][0]["message"]["content"]
        cleaned = processing_text.clean_markdown(content)
        if index == 1:
            piece = cleaned
        else:
            piece = processing_text.markdown_separator(accumulated, cleaned) + cleaned

        accumulated += piece
        if on_chunk is not None:
            on_chunk(piece)

    return accumulated
