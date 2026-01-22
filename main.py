import os

import processing_images
import processing_pdf
import processing_text


def run_ai_ocr(pdf_file, api_key, model):
    print("Convirtiendo PDF a imágenes")
    processing_pdf.convert(pdf_file)
    print("Pasando imágenes a la IA")
    processing_images.ocr(api_key, model)
    print("Procesando salida de la IA")
    processing_text.to_md(
        "output.json", pdf_file.removesuffix(".pdf").removeprefix("docs/")
    )
    print("Listo!")


pdfs = []

for file in os.listdir("docs/"):
    if file.endswith(".pdf"):
        pdfs.append(file)
 
key = str(input("Ingresa tu API de OpenRouter: "))
model = str(input("Ingresa el modelo a utilizar (Qwen3 VL 235B A22B Instruct por defecto): "))

if model == "":
    model = "qwen/qwen3-vl-235b-a22b-instruct"

for doc in pdfs:
    print(f"[ Procesando {doc} ]")
    run_ai_ocr("docs/" + doc, key, model)
