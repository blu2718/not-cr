import os
import json

import processing_images
import processing_pdf
import processing_text


def run_ai_ocr(pdf_file, api_key, model):
    print("Convirtiendo PDF a imágenes")
    processing_pdf.convert(pdf_file)

    # TODO: agregar lógica para procesar el documento entero por lotes.

    processing_text.to_md(
        "output.json", pdf_file.removesuffix(".pdf").removeprefix("docs/")
    )

    print("Listo!")


pdfs = []

for file in os.listdir("docs/"):
    if file.endswith(".pdf"):
        pdfs.append(file)

with open("config.json", "r") as file:
    config = json.load(file)

key = config["openrouter"]["api-key"]
model = config["openrouter"]["model"]

for doc in pdfs:
    print(f"[ Procesando {doc} ]")
    run_ai_ocr("docs/" + doc, key, model)
