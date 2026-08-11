# not-cr

not-cr convierte PDFs en Markdown mediante modelos multimodales. Procesa las páginas por lotes, conserva el contexto entre lotes y muestra el progreso en vivo mientras trabaja. Cada corrida queda guardada con el tag del modelo utilizado, sus metadatos y, cuando el proveedor lo permite, el uso de tokens y el coste estimado.

## Requisitos

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Poppler, incluyendo `pdftoppm` y `pdfinfo`

## Configuración

```bash
uv sync
cp config.example.json config.json
```

Edita `config.json` con la API key, la URL base y los valores por defecto de cada proveedor, o configura todo desde la página `/config` de la aplicación. `config.json` no forma parte del repositorio.

## Uso

Inicia la webapp:

```bash
python app.py
```

También puedes usar Flask:

```bash
flask --app app run --host 0.0.0.0
```

Abre `http://localhost:5000`, sube un PDF y previsualízalo en su página. Elige un modelo en el buscador, revisa los precios y capacidades que publique el proveedor, selecciona el nivel de razonamiento y procesa el documento. La pantalla de trabajo muestra el cronómetro, los lotes, el texto reconocido, los eventos y el uso de tokens en vivo.

Cada corrida crea un Markdown nuevo en `output/` y su sidecar `.meta.json`; reprocesar el mismo documento no sobrescribe los resultados anteriores. Los resultados se pueden ver en HTML o descargar como Markdown.

## Proveedores

Se soportan OpenRouter y OpenCode Zen/Go. Ambos se consultan con la librería oficial `openai`, cambiando `base_url`. OpenRouter usa `chat/completions`; en OpenCode los modelos GPT usan `responses` y los modelos compatibles usan `chat/completions`. Los precios, el contexto y las capacidades dependen de los metadatos que cada proveedor publique en `/models`.

Los niveles de razonamiento de OpenCode dependen del modelo: GPT usa `reasoning` en Responses, los modelos OpenAI-compatible que lo admiten usan `reasoning_effort`, y modelos como MiMo/Kimi pueden exponer solo el razonamiento por defecto del proveedor, sin niveles explícitos.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```
