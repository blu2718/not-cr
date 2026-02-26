import base64
import json
import os

import requests
import prompts


def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def image_prep():
    images = []
    encoded = []

    for img in os.listdir("img/"):
        if img.endswith(".jpg"):
            images.append(img)

    for img in images:
        encoded[images.index(img)] = encode_image_to_base64("img/" + img)

    n = 20

    return [encoded[i : i + n] for i in range(0, len(encoded), n)]


def last_gen(output):
    last_content = output["choices"][0]["message"]["content"][-250:]
    return last_content


def message_build(image_batch, last_gen=None):
    print("├ Construyendo mensaje")
    prompt = prompts.OCR_PROMPT if last_gen is None else prompts.OCR_CONTINUATION_PROMPT

    if last_gen is not None:
        data.append({"type": "text", "text": last_gen})

    for img in image_batch:
        data.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})

    message = [{"role": "user", "content": data}]

    return message


def to_model(api_key, model, message, write_output=True):
    print("├ Enviando solicitud al modelo")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": message}
    response = requests.post(url, headers=headers, json=payload)
    print("├ Esperando respuesta del modelo")

    print("└ Respuesta recibida")
    if write_output:
        with open("output.json", "w", encoding="utf-8") as f:
            json.dump(response.json(), f, indent=4, ensure_ascii=False)

    return response.json()
