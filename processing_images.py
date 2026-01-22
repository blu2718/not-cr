import base64
import json
import os

import requests

prompt = """
# Rol
Actúas como un experto en OCR (Reconocimiento Óptico de Caracteres), edición académica y formateo Markdown. Tu objetivo es transformar una secuencia de imágenes de un PDF en un documento de texto unificado, estructurado y fiel.

# Entrada
Recibirás múltiples imágenes que representan las páginas de un documento original en PDF.

# Instrucciones
1. **Transcripción Fiel:** Convierte el contenido visual en texto con la máxima exactitud posible.
2. **Reordenamiento Lógico:** Reconstruye el flujo lógico del documento. Si el PDF tiene columnas o diagramas complejos, asegúrate de que el texto leído siga el orden de lectura humano (no simplemente de izquierda a derecha por líneas de escaneo).
3. **Idioma:** Detecta si el documento está en español o inglés. Mantén el idioma original y la terminología específica utilizada. No traduzcas.
4. **Formato Markdown:** Utiliza Markdown para representar la estructura:
   - Usa `#`, `##` para títulos y subtítulos.
   - Usa `**negrita**` o `*cursiva*` cuando corresponda.
   - Usa listas con viñetas `-` o numeración `1.` cuando detectes elementos de lista.
5. **Matemáticas:** Estrictamente, usa sintaxis LaTeX:
   - Para fórmulas en línea (inline), usa delimitadores de dólar simple: $...$
   - Para fórmulas en bloque (display), usa delimitadores de dólar doble: $$...$$
6. **Imágenes y Gráficos:** Si encuentras una figura, tabla o gráfico que no es texto, NO intentes transcribir su contenido pixel a pixel. En su lugar, utiliza estrictamente este formato:
   (Imagen: <breve descripción del contenido visual de la imagen>)

# Restricciones
- No inventes texto que no sea claramente visible.
- Elimina números de página y encabezados/pies de página repetitivos a menos que sean relevantes para la estructura.
- El resultado final debe ser un único bloque de texto unificado.

# Ejemplos de formato esperado

## Entrada (descripción visual):
[Texto: "El área del círculo es"] [Símbolo matemático: A = pi r^2]

## Salida esperada:
El área del círculo es $A = \pi r^2$

## Entrada (descripción visual):
[Imagen: Gráfico de barras mostrando ventas]

## Salida esperada:
(Imagen: Gráfico de barras mostrando ventas anuales)
"""


def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def ocr(api_key, model, ocr_prompt=prompt):
    images = []

    for img in os.listdir("img/"):
        if img.endswith(".jpg"):
            images.append(img)

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    image_content = [{"type": "text", "text": ocr_prompt}]

    print("├ Construyendo mensaje")
    for img in images:
        base64_image = encode_image_to_base64("img/" + img)
        data_url = f"data:image/jpeg;base64,{base64_image}"
        image_content.append({"type": "image_url", "image_url": {"url": data_url}})

    messages = [{"role": "user", "content": image_content}]

    print("├ Enviando solicitud al modelo")

    payload = {"model": model, "messages": messages}

    print("├ Esperando respuesta del modelo")

    response = requests.post(url, headers=headers, json=payload)

    print("└ Respuesta recibida")

    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(response.json(), f, indent=4, ensure_ascii=False)
