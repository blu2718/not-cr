# Plan de implementación: not-cr → webapp Flask de OCR con LLMs

> **Audiencia:** este documento es la especificación completa para que un agente
> implemente los cambios. Léelo entero antes de tocar código. Las Fases 0–1 son
> un contrato **exacto** (provienen de tests compilados recuperados del bytecode);
> las Fases 2–4 dejan libertad de implementación dentro del comportamiento descrito.
>
> Única excepción permitida al contrato: **añadir parámetros opcionales al final**
> de las firmas (marcados como «extensión opcional»). Los tests restaurados en la
> Fase 0 deben seguir pasando sin tocarlos, porque nunca pasan esos parámetros.

---

## 0. Reglas de trabajo (obligatorias)

1. **Commits frecuentes y atómicos.** Cada paso numerado de este plan (o cada
   cambio lógico autocontenido) termina en un commit. Estilo *conventional
   commits*, coherente con el historial del repo (`feat:`, `fix:`, `chore:`,
   `refactor:`, `test:`, `docs:`). Mensajes en inglés, como el historial.
2. **NUNCA hacer `git push`.** Solo commits locales. Tampoco `git rebase`,
   `git reset --hard` ni force-push.
3. **Nunca commitear secretos.** Por eso el paso 1.3 saca `config.json` del
   tracking *antes* de que la webapp permita escribir API keys en él.
4. **Los tests son la especificación.** No modificar los archivos de test salvo
   error tipográfico evidente. La Fase 1 termina cuando todos pasan.
5. **Verificación continua:** `.venv/bin/python -m unittest discover -s tests -v`
   tras cada fase (o cuando un paso afecte a código cubierto por tests).
6. Entorno: Python 3.13, gestor `uv`, venv en `.venv/`. Instalar deps con
   `uv add <paquete>` (nunca `pip install` directo).
7. Estilo del proyecto: código simple, sin clases innecesarias; mensajes de
   feedback en español con el estilo de árbol existente (`├`, `└`) — en la
   webapp se emiten como **eventos** (ver 3.4 y 5.6), no como `print`; sin
   sobre-ingeniería. `prompts.py` **no se modifica**.
8. No crear documentación extra más allá de lo indicado en la Fase 4.
9. Las features nuevas de la Fase 3 (combobox de modelos, tags por corrida,
   eventos de progreso, preview) se apoyan **solo** en parámetros opcionales
   añadidos a las firmas del contrato. Prohibido cambiar el comportamiento
   que los tests fijan.

---

## 1. Contexto y estado actual

`not-cr` es una herramienta OCR: convierte PDFs a imágenes (pdf2image +
poppler), las envía a un LLM multimodal vía HTTP (formato chat/completions de
OpenAI) y guarda el resultado como Markdown.

**Está rota/incompleta.** Problemas conocidos del código actual:

- `main.py`: el pipeline nunca llama al modelo (quedó un TODO de procesamiento
  por lotes); `to_md` lee un `output.json` que nadie escribe; todo el código
  corre al importar el módulo (no hay guarda `__main__`).
- `processing_images.image_prep()`: `encoded[images.index(img)] = ...` →
  IndexError garantizado (lista vacía); no ordena las páginas; directorio
  `img/` hardcodeado.
- `processing_images.message_build()`: usa la variable `data` sin definirla →
  NameError; el parámetro `last_gen` sombrea la función homónima.
- `processing_images.to_model()`: URL hardcodeada (ignora `api-url` del
  config); sin manejo de errores HTTP.
- `processing_pdf.convert()`: destruye el directorio compartido `img/` en cada
  corrida; rutas hardcodeadas.
- `processing_text.to_md()`: lee de un archivo JSON; el strip de fences con
  `removesuffix("```")` mutila texto que termina en backticks sueltos.
- `pyproject.toml`: sin dependencias declaradas (el venv tiene `pdf2image`,
  `requests`, `pillow`, pero el proyecto no las declara).
- `tests/`: solo quedan `.pyc` compilados; las fuentes se perdieron. **Su
  contenido exacto se restaura en la Fase 0.**

**Feedback actual de la herramienta** (los `print` que la Fase 3 itera y
convierte en eventos — conservar redacción y estilo de árbol):

- `main.py`: `[ Procesando {doc} ]`, `Convirtiendo PDF a imágenes`, `Listo!`
- `processing_pdf.py`: `├ Creando directorio`, `└ Convirtiendo`
- `processing_images.py`: `├ Construyendo mensaje`, `├ Enviando solicitud al
  modelo`, `├ Esperando respuesta del modelo`, `└ Respuesta recibida`
- `processing_text.py`: `└ Guardando a Markdown`

**Decisiones de producto ya tomadas con el usuario (no re-debatir):**

- La aplicación final es una **webapp Flask** (`app.py`); será la **única**
  interfaz. `main.py` queda como librería (sin loop CLI).
- Multi-proveedor: `openrouter` y `opencode` (OpenCode Zen/Go). No todos los
  modelos de OpenCode usan el mismo transporte: OpenRouter usa
  `chat/completions`; en OpenCode los modelos GPT usan `/responses`, mientras
  que los modelos OpenAI-compatible (por ejemplo MiMo, Kimi y GLM) usan
  `/chat/completions`. Los modelos que solo publican `/messages` o una API
  específica de Google quedan fuera del flujo OCR hasta implementar un
  adaptador explícito para ese transporte.
  - Nota: la URL documentada de Zen es `https://opencode.ai/zen/v1/...`, pero
    el usuario indicó `https://opencode.ai/zen/go/v1/`. Se usa la del usuario
    como valor por defecto y queda editable en config. OpenCode Go documenta
    por modelo si usa `/responses`, `/chat/completions` o `/messages`.
- **Todo el llamado HTTP a OpenRouter/OpenCode y el listado de modelos se hace
  con la librería oficial `openai`** (`from openai import OpenAI`, cliente con
  `OpenAI(api_key=..., base_url=...)`), **no** con `requests` directos. El SDK
  se usa para `client.models.list()`, `client.chat.completions.create()` y,
  para modelos OpenCode Responses, `client.responses.create()`.
  Las URLs del config son URLs **base** (p.ej.
  `https://openrouter.ai/api/v1`); el SDK añade la ruta correspondiente.
- El campo `reasoning` del config se considera un default, pero solo se envía
  cuando el transporte y el modelo lo soportan. El formato depende del
  proveedor: OpenRouter usa `extra_body={"reasoning": {"effort": ...}}`,
  OpenCode Responses usa `reasoning={"effort": ...}` y OpenCode
  OpenAI-compatible usa `reasoning_effort` en la raíz del payload.
  Modelos de modo `toggle` (como MiMo) no reciben un effort explícito.
- El volcado de respuestas crudas a `output.json` se **mantiene opcional**
  (flag `write_output`), por defecto desactivado.
- El OCR desde la web corre en **hilo en background** con **progreso en vivo**
  por SSE (aprovechando los callbacks `on_chunk` y `on_event`).
- **Selector de modelo = combobox buscador**: un campo de texto con desplegable;
  con el campo vacío se listan **todos** los modelos del proveedor; al escribir
  se filtra por subcadena (case-insensitive) sobre id y nombre (escribir "mini"
  muestra todos los modelos con "mini"). El catálogo se obtiene del endpoint
  `/models` del proveedor vía SDK `openai`, aprovechando los metadatos que
  publique (OpenRouter devuelve precios, `context_length`,
  `supported_parameters`; lo que no exista simplemente no se muestra).
- **Un documento se puede procesar varias veces** (con el mismo o con distinto
  modelo). Ninguna corrida sobrescribe otra: cada resultado se guarda como
  `output/{doc}__{modelo}__{timestamp}.md` + un sidecar
  `output/{doc}__{modelo}__{timestamp}.meta.json`, y la UI muestra el **tag
  del modelo** usado en cada resultado (ver 5.5).
- **La portada tiene dos secciones**: «Archivos subidos» (PDFs en `docs/`) y
  «Resultados anteriores» (corridas con su tag de modelo y fecha).
- **La pantalla de procesado itera sobre el feedback por consola actual**:
  los mismos mensajes (misma redacción, mismo estilo `├`/`└`) se convierten en
  un registro en vivo, enriquecido con detalle constante: nº de páginas, lote
  i/N, tiempos por lote, tokens de uso cuando el proveedor los devuelve, barra
  de progreso y cronómetro (ver 5.6).
- **Nivel de razonamiento seleccionable por corrida** cuando el modelo lo
  soporta. Los valores se obtienen de los metadatos del modelo cuando están
  disponibles; como fallback se usan los esfuerzos estándar del transporte.
  Los modelos `toggle` solo muestran el razonamiento por defecto del
  proveedor y no inventan niveles. El valor por defecto viene del config; la
  selección de la corrida lo sobrescribe (ver 5.4).
- **Previsualización del PDF** subido, embebida en la página del documento.
- **Página de configuración** (`/config`) donde se editan la URL base y la API
  key de cada proveedor, el modelo y razonamiento por defecto, y se puede
  probar la conexión.
- Webapp con: subir y procesar PDFs, listar/ver/descargar resultados, editar
  configuración, progreso en vivo.

---

## 2. Fase 0 — Base y contrato

### 2.1 Restaurar la suite de tests

Crear **exactamente** estos dos archivos (reconstruidos del bytecode de los
`.pyc`; las aserciones son el contrato del diseño):

**`tests/test_config.py`**

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import main


class ConfigTests(unittest.TestCase):
    def test_app_save_config_is_readable_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config = {"openrouter": {"model": "provider/model", "reasoning": "low"}}

            with patch.object(app, "CONFIG_PATH", config_path):
                app.save_config(config)
                self.assertEqual(app.load_config(), config)

            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), config)
            self.assertEqual(list(Path(directory).glob("tmp*")), [])

    def test_main_load_config_uses_its_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                '{"openrouter": {"model": "provider/model"}}', encoding="utf-8"
            )

            with patch.object(main, "CONFIG_PATH", config_path):
                self.assertEqual(main.load_config()["openrouter"]["model"], "provider/model")


if __name__ == "__main__":
    unittest.main()
```

**`tests/test_ocr_pipeline.py`**

```python
import os
import tempfile
import unittest
from unittest.mock import patch

import main
import ocr
import processing_text


class MarkdownSeparatorTests(unittest.TestCase):
    def test_separates_adjacent_words(self):
        self.assertEqual(processing_text.markdown_separator("primer", "segundo"), " ")

    def test_keeps_hyphenated_word_together(self):
        self.assertEqual(processing_text.markdown_separator("inter-", "nacional"), "")

    def test_separates_markdown_blocks(self):
        self.assertEqual(processing_text.markdown_separator("# Título", "## Sección"), "\n\n")


class CleanMarkdownTests(unittest.TestCase):
    def test_removes_balanced_fence_wrapper(self):
        self.assertEqual(processing_text.clean_markdown("```markdown\nhola\n```"), "hola")
        self.assertEqual(processing_text.clean_markdown("```\nhola\n```"), "hola")

    def test_preserves_code_block_without_wrapper(self):
        self.assertEqual(
            processing_text.clean_markdown("```python\nprint('hi')\n```"),
            "```python\nprint('hi')\n```",
        )

    def test_preserves_trailing_backticks(self):
        self.assertEqual(processing_text.clean_markdown("texto ```"), "texto ```")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(processing_text.clean_markdown("hola mundo"), "hola mundo")


class OcrPipelineTests(unittest.TestCase):
    @patch("ocr.processing_images.to_model")
    @patch("ocr.processing_images.message_build")
    @patch("ocr.processing_images.image_prep", return_value=[["first"], ["second"]])
    @patch("ocr.processing_pdf.convert")
    def test_joins_chunks_and_uses_cleaned_context(
        self, convert, image_prep, message_build, to_model
    ):
        to_model.side_effect = [
            {"choices": [{"message": {"content": "```markdown\nprimer\n```"}}]},
            {"choices": [{"message": {"content": "segundo"}}]},
        ]

        chunks = []

        result = ocr.process_pdf(
            "document.pdf", "images", "key", "model", on_chunk=chunks.append
        )

        self.assertEqual(result, "primer segundo")
        self.assertEqual(chunks, ["primer", " segundo"])
        self.assertEqual(message_build.call_args_list[1].args[1], "primer")
        convert.assert_called_once_with("document.pdf", "images")
        image_prep.assert_called_once_with("images")

    @patch("main.processing_text.to_md")
    @patch("main.ocr.process_pdf", return_value="contenido")
    def test_cli_uses_and_cleans_an_isolated_image_directory(self, process_pdf, to_md):
        with tempfile.TemporaryDirectory() as work_dir:
            pdf_path = os.path.join(work_dir, "uno.pdf")
            with open(pdf_path, "wb") as pdf:
                pdf.write(b"%PDF-1.7")

            main.run_ai_ocr(pdf_path, "key", "model")

        image_dir = process_pdf.call_args.args[1]
        self.assertFalse(os.path.exists(image_dir))
        self.assertEqual(
            process_pdf.call_args.args[:4], (pdf_path, image_dir, "key", "model")
        )
        to_md.assert_called_once_with("contenido", "uno")


if __name__ == "__main__":
    unittest.main()
```

Verificar que la suite corre y **falla** (los módulos `app`/`ocr` aún no
existen — es lo esperado).

> Commit: `test: restore test suite from recovered spec`

### 2.2 Dependencias

```bash
uv add flask pdf2image openai markdown
```

(`openai` reemplaza a `requests`: toda la HTTP hacia los endpoints la hace el
SDK — **no** añadir `requests` como dependencia. `markdown` se usa para
renderizar los `.md` en la webapp. Poppler ya está instalado en el sistema:
`pdftoppm`/`pdfinfo`.)

> Commit: `chore: declare project dependencies`

### 2.3 Housekeeping y protección de secretos

1. Añadir `__pycache__/` al `.gitignore` (hoy no está).
2. Borrar el `__pycache__/` raíz obsoleto (artefactos de Python 3.11) y
   `tests/__pycache__/` (queda regenerado por la suite nueva).
3. Sacar `config.json` del tracking: `git rm --cached config.json`, añadir
   `config.json` al `.gitignore`, y crear `config.example.json` (misma forma,
   keys vacías). El `config.json` real queda en disco, sin trackear.
   Motivo: la webapp escribirá API keys ahí y la regla 3 prohíbe commitearlas.
4. Añadir `output/*.meta.json` **no** es necesario ignorarlo (no contiene
   secretos: solo modelo, fechas y uso de tokens); se commitea o no según el
   criterio del usuario sobre `output/` en general. Mantener el `.gitignore`
   actual al respecto, sin tocar.

> Commit: `chore: stop tracking config.json and ignore pycache`

---

## 3. Fase 1 — Núcleo OCR por lotes (contrato exacto)

Orden de implementación sugerido: `processing_text` → `processing_images` →
`processing_pdf` → `ocr` → `main` → `app` (helpers de config). Un commit por
archivo. Al final, la suite completa (11 tests) debe pasar.

**Nota global de la fase:** todos los `print` actuales desaparecen de
`processing_*` (pasan a ser funciones de librería). El feedback se emite desde
`ocr.process_pdf` / `main.run_ai_ocr` mediante el callback opcional `on_event`
(ver 3.4), conservando la redacción en español y el estilo de árbol. Con
`on_event=None` (lo que pasan los tests) no se emite nada y el comportamiento
observable por los tests es idéntico al descrito.

### 3.1 `processing_text.py`

```python
def clean_markdown(text: str) -> str
```

- Si `text` empieza con ` ```markdown\n` y termina con `\n``` ` (o ` ``` ` al
  final, ver ejemplos), quita el wrapper y devuelve el interior.
- También quita wrapper de fence "desnudo": empieza con ` ```\n` y termina con
  ` ``` `.
- **No** tocar fences con otro lenguaje: `"```python\nprint('hi')\n```"` se
  devuelve idéntico.
- **No** tocar backticks sueltos: `"texto ```"` idéntico; `"hola mundo"`
  idéntico.
- Regla práctica que satisface lo anterior: solo se considera wrapper si el
  texto **comienza** con la fence y **termina** con la fence de cierre; el
  lenguaje del wrapper debe ser `markdown` o vacío. Implementación sugerida:
  detectar la primera línea; si es ` ```markdown ` o ` ``` ` y la última línea
  no vacía es ` ``` `, devolver las líneas intermedias unidas con `\n`.

```python
def markdown_separator(prev: str, next: str) -> str
```

- Devuelve `""` si `prev` termina en `"-"` (palabra partida entre lotes).
- Devuelve `"\n\n"` si `next` empieza con `"#"` (bloque Markdown nuevo).
- En cualquier otro caso, `" "`.

```python
def to_md(contenido: str, nombre: str) -> None
```

- **Cambio de firma**: ahora recibe el texto Markdown (no una ruta a JSON).
- Escribe `output/{nombre}.md` (UTF-8). Crea `output/` si no existe.
- Sin `print` (el evento «Guardando a Markdown» lo emite `main.run_ai_ocr`).

> Commit: `feat(processing_text): add clean_markdown and markdown_separator, to_md takes text`

### 3.2 `processing_images.py`

```python
BATCH_SIZE = 20

def image_prep(images_dir: str) -> list[list[str]]
```

- Lista los `.jpg` de `images_dir`, los ordena en **orden natural de página**
  (extraer el número de página del nombre — p.ej. la última secuencia de
  dígitos del stem — y ordenar por su valor numérico; desempate por nombre).
  No usar orden lexicográfico puro.
- Codifica cada imagen a base64 y devuelve la lista de lotes de tamaño
  `BATCH_SIZE` (lista de listas de strings base64).
- Corrige el bug actual: construir con `append`, nunca
  `encoded[images.index(img)] = ...`.

```python
def message_build(image_batch: list[str], context: str | None = None) -> list[dict]
```

- Inicializa `data = [{"type": "text", "text": prompt}]` donde `prompt` es
  `prompts.OCR_PROMPT` si `context is None`, si no
  `prompts.OCR_CONTINUATION_PROMPT`.
- Si `context is not None`: `data.append({"type": "text", "text": context})`.
- Luego un `{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}`
  por cada imagen del lote.
- Devuelve `[{"role": "user", "content": data}]`.
- Corrige el NameError actual (`data` sin definir) y **renombra** el parámetro
  para no sombrear la función `last_gen`.
- Sin `print`.

```python
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

def to_model(api_key, model, message, base_url=DEFAULT_BASE_URL,
             reasoning=None, write_output=False) -> dict
```

- **Usar la librería oficial `openai`** (no `requests`):
  `client = OpenAI(api_key=api_key, base_url=base_url)`. `base_url` es la URL
  **base** del proveedor; el SDK añade la ruta del transporte. Crear el cliente
  dentro de la función (es barato y mantiene la firma simple).
- Transporte y conversión:
  - OpenRouter: `client.chat.completions.create(model=model,
    messages=message, ...)` con las partes `image_url` tal cual.
  - OpenCode Responses (GPT/Grok según la URL y el model id):
    `client.responses.create(model=model, input=...)`; convertir las partes a
    `input_text`/`input_image` y normalizar `output_text`/`usage` a un dict con
    forma de Chat Completions para que `ocr.py` no cambie.
  - OpenCode OpenAI-compatible: `client.chat.completions.create()` con
    `reasoning_effort` en la raíz cuando el modelo tenga variantes de effort.
    No usar aquí el campo OpenRouter `reasoning`.
  - Modelos OpenCode que solo exponen `/messages` o APIs específicas quedan
    fuera de la selección hasta contar con un adaptador propio.
- Reasoning por transporte:
  - OpenRouter: si no es None/vacío, pasar
    `extra_body={"reasoning": {"effort": reasoning}}` verbatim.
  - OpenCode Responses: pasar `reasoning={"effort": reasoning}`.
  - OpenCode chat: pasar `reasoning_effort=reasoning` solo para esfuerzos
    soportados; en modo `toggle` no enviar ningún campo.
- Si un endpoint OpenCode rechaza el effort, reintentar una vez sin el campo de
  reasoning y conservar el error original si el segundo intento falla.
- **Devolver un dict plano**: `return completion.model_dump()`. Obligatorio:
  `ocr.process_pdf` accede estilo dict
  (`response["choices"][0]["message"]["content"]`) y los tests mockean
  `to_model` con dicts. El dict incluirá `usage` cuando el proveedor lo
  devuelva (OpenAI/OpenRouter lo incluyen por defecto); `ocr.py` lo reenvía
  por `on_event` (ver 3.4) y la webapp lo usa para tokens y coste estimado.
- Errores: el SDK lanza `openai.APIError` y subclases; dejarlos propagar (el
  job manager de la webapp captura `Exception`). Nada de `raise_for_status`.
- Si `write_output=True`, volcar el dict devuelto a `output.json`
  (`indent=4, ensure_ascii=False`) como hace el código actual. Por defecto no
  escribe nada.
- La función `last_gen()` actual queda en desuso: **eliminarla** (la limpieza
  de contexto vive ahora en `ocr.py`).
- Sin `print`.

> Commit: `fix(processing_images): repair image_prep/message_build, parametrize to_model`

### 3.3 `processing_pdf.py`

```python
def convert(pdf_file: str, output_dir: str) -> None
```

- Igual que hoy, pero con `output_dir` parametrizado y `output_file` =
  stem del PDF (`Path(pdf_file).stem`), en vez de las rutas hardcodeadas.
- **Eliminar** `create_img_dir()` y toda la lógica que destruye/recrea el
  directorio compartido `img/` (el caller provee un directorio aislado).
- Sin `print`.

> Commit: `refactor(processing_pdf): parametrize output directory`

### 3.4 `ocr.py` (nuevo)

Módulo orquestador. Importa `processing_images`, `processing_pdf`,
`processing_text` (los tests patchean `ocr.processing_images.*` y
`ocr.processing_pdf.convert`, así que deben ser `import processing_images` /
`import processing_pdf`, no `from ... import ...`).

```python
def process_pdf(pdf_path, image_dir, api_key, model, on_chunk=None,
                base_url=processing_images.DEFAULT_BASE_URL, reasoning=None,
                on_event=None) -> str
```

(`on_event` es **extensión opcional**: los tests no lo pasan; con `None` no se
emite nada y todo lo de abajo sigue siendo exactamente lo que los tests fijan.)

Comportamiento (todo esto está fijado por los tests):

1. `processing_pdf.convert(pdf_path, image_dir)` — llamada **posicional**.
2. `batches = processing_images.image_prep(image_dir)` — llamada posicional
   con un solo argumento.
3. Para cada `batch` en orden:
   - `message = processing_images.message_build(batch, context)` — llamada
     **posicional**; `context` es `None` en el primer lote y, en los
     siguientes, los **últimos 250 caracteres del texto limpio acumulado**
     (`acumulado[-250:]`).
   - `response = processing_images.to_model(api_key, model, message, base_url=base_url, reasoning=reasoning)`
     (los kwargs extra no molestan al test porque `to_model` está mockeado).
   - `content = response["choices"][0]["message"]["content"]`.
   - `cleaned = processing_text.clean_markdown(content)`.
   - Si es el primer lote: `piece = cleaned`. Si no:
     `piece = processing_text.markdown_separator(acumulado, cleaned) + cleaned`.
   - `acumulado += piece`.
   - Si `on_chunk` no es None: `on_chunk(piece)` (primer pedazo sin separador;
     los siguientes con el separador **prefijado** — el test espera
     `["primer", " segundo"]`).
4. Devolver `acumulado`.

Con el mock de los tests, `process_pdf("document.pdf", "images", "key", "model",
on_chunk=chunks.append)` debe devolver `"primer segundo"`.

**Eventos `on_event`** (extensión opcional; contrato de la Fase 3, ver 5.6).
Firma del callback: `on_event(kind: str, payload)` con `payload`
JSON-serializable (str o dict). Puntos de emisión y payloads:

| Momento | Evento |
|---|---|
| Antes de `convert` | `("log", "Convirtiendo PDF a imágenes")`, `("status", {"step": "converting"})` |
| Tras `image_prep` | `("log", f"{páginas} páginas en {n} lotes")`, `("status", {"step": "batch", "index": 0, "total": n})` |
| Por lote, antes de `message_build` | `("log", f"├ Construyendo mensaje (lote {i}/{n})")`, `("status", {"step": "batch", "index": i, "total": n, "pages": len(batch)})` |
| Por lote, antes de `to_model` | `("log", "├ Enviando solicitud al modelo")` |
| Por lote, tras `to_model` | `("log", f"└ Respuesta recibida ({segundos:.1f} s)")` — medir con `time.monotonic()`; si `response.get("usage")` existe: `("usage", response["usage"])` |

`páginas` = `sum(len(b) for b in batches)`; lotes numerados desde 1 en los
mensajes de log. Los mocks de los tests devuelven dicts sin `usage` → no se
emite evento `usage` (usar `.get`, nunca acceso directo).

> Commit: `feat(ocr): add batched document processing with cleaned context and progress events`

### 3.5 `main.py` (pasa a ser librería)

Contenido final del módulo (sin loop CLI — la webapp es la única interfaz;
eliminar el código a nivel de módulo actual es **obligatorio** porque los tests
importan `main`):

```python
CONFIG_PATH = Path("config.json")

def load_config() -> dict:
    # json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # Debe leer el global CONFIG_PATH en tiempo de llamada
    # (los tests lo patchean con patch.object).

def run_ai_ocr(pdf_path, api_key, model, base_url=None, reasoning=None,
               on_chunk=None, on_event=None, output_name=None) -> None:
```

(`on_event` y `output_name` son **extensiones opcionales**: el test llama
`run_ai_ocr(pdf_path, "key", "model")` y sigue exigiendo
`to_md("contenido", "uno")`.)

`run_ai_ocr`:

1. `image_dir = tempfile.mkdtemp(prefix="not-cr-")` — directorio aislado por
   documento (fuera del árbol del proyecto).
2. `try`: llama `ocr.process_pdf(pdf_path, image_dir, api_key, model,
   on_chunk=on_chunk, base_url=..., reasoning=..., on_event=on_event)` y luego
   `processing_text.to_md(contenido, output_name or Path(pdf_path).stem)`.
   - Pasar `pdf_path` **tal cual** se recibió (el test lo compara).
   - `base_url`: si es None, no reenviarlo (que aplique el default de
     `ocr.process_pdf`); lo mismo para `reasoning` y `on_event`.
   - Los 4 primeros argumentos a `process_pdf` deben ser posicionales
     `(pdf_path, image_dir, api_key, model)`; el resto por keyword.
   - `output_name`: si es None, se usa el stem del PDF (lo que exige el test).
     La webapp pasa un nombre con tag de modelo y timestamp (ver 5.5).
   - Antes de `to_md`, si `on_event`: emitir
     `("log", "└ Guardando a Markdown")` y `("status", {"step": "saving"})`.
3. `finally`: `shutil.rmtree(image_dir, ignore_errors=True)` — el test verifica
   que el directorio **ya no existe** tras volver de `run_ai_ocr`.

Importar como `import ocr` y `import processing_text` (los tests patchean
`main.ocr.process_pdf` y `main.processing_text.to_md`).

> Commit: `refactor(main): turn into library with isolated per-document image dir`

### 3.6 `app.py` — helpers de configuración (todavía sin Flask)

```python
CONFIG_PATH = Path("config.json")

def load_config() -> dict:
    # igual que main.load_config: lee el global CONFIG_PATH en tiempo de llamada.

def save_config(config: dict) -> None:
    # Escritura atómica:
    #   fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent)  (prefijo "tmp" por defecto)
    #   escribir json (indent=4, ensure_ascii=False, utf-8) y cerrar
    #   os.replace(tmp, CONFIG_PATH)
    #   ante excepción: borrar tmp y re-lanzar.
    # El test exige que no queden archivos "tmp*" en el directorio.
```

> Commit: `feat(app): add atomic config load/save helpers`

**Checkpoint de la Fase 1:**
`.venv/bin/python -m unittest discover -s tests -v` → **11 tests en verde.**
Si no es así, no seguir a la Fase 2.

---

## 4. Fase 2 — Config multi-proveedor

### 4.1 Esquema nuevo de `config.json` (y `config.example.json`)

```json
{
    "active": "openrouter",
    "openrouter": {
        "api-url": "https://openrouter.ai/api/v1",
        "api-key": "",
        "model": "qwen/qwen3-vl-235b-a22b-instruct",
        "reasoning": "low"
    },
    "opencode": {
        "api-url": "https://opencode.ai/zen/go/v1",
        "api-key": "",
        "model": "",
        "reasoning": "low"
    }
}
```

- Ojo: `"api-url"` ahora guarda la URL **base** del proveedor (sin
  `/chat/completions`); se pasa tal cual como `base_url` al cliente `OpenAI`.
- `model` y `reasoning` son los **valores por defecto** que la webapp
  preselecciona en el formulario de procesado; cada corrida puede
  sobrescribirlos (ver 5.4). `reasoning` admite `""` (= no enviar el
  parámetro al modelo).
- Claves por proveedor en top-level (retrocompatible con el config actual y
  con los tests, que solo leen `config["openrouter"]["model"]` de archivos que
  ellos mismos crean — el esquema no afecta a los tests).
- Migrar el `config.json` real conservando el valor actual de `model`
  (`qwen/qwen3-vl-235b-a22b-instruct`). El `api-key` real jamás se commitea
  (ya está fuera del tracking desde 2.3).

### 4.2 Helper en `app.py`

```python
def get_active_provider(config: dict) -> dict:
    """Devuelve config[config["active"]]; KeyError claro si falta."""
```

> Commit: `feat(config): multi-provider schema with active provider`

---

## 5. Fase 3 — Webapp Flask en `app.py`

Libertad de implementación dentro de este comportamiento. Mantener simple:
templates Jinja server-side, un `static/style.css`, JS vanilla solo donde hace
falta (combobox de modelos, cronómetro y stream SSE). **Sin** framework de
frontend ni base de datos.

### 5.1 Job manager (en `app.py`)

- `jobs: dict[str, dict]` en memoria: `job_id` (uuid4 hex) →
  `{"doc": str, "model": str, "output_name": str,
    "status": "running" | "done" | "error",
    "events": list[tuple[str, str]], "started": float, "usage": list[dict],
    "pricing": dict | None}`.
  - `events`: tuplas `(kind, payload)` donde `payload` es str JSON ya
    serializado (`json.dumps`) para poder reenviarlo tal cual por SSE.
  - `usage`: acumula los payloads de los eventos `usage` de cada lote.
  - `pricing`: pricing del modelo si estaba en el caché del catálogo
    (ver 5.3); sirve para el coste estimado. `None` si se desconoce.
  - Estado volátil (se pierde al reiniciar): aceptable para app local.
- Al procesar: crear job, arrancar `threading.Thread(target=_run_job, daemon=True)`.
- Si ya hay un job `running` para ese **(documento, modelo)**, redirigir al
  job existente en vez de crear otro. El mismo documento con otro modelo (o
  terminado el anterior) arranca una corrida nueva — es la vía para
  «procesar varias veces».
- `_run_job(job, pdf_path, provider, model, reasoning)`:

```python
def emit(kind, payload):
    job["events"].append((kind, json.dumps(payload, ensure_ascii=False)))

job["status"] = "running"
try:
    main.run_ai_ocr(
        str(pdf_path),
        provider["api-key"],
        model,
        base_url=provider["api-url"],
        reasoning=reasoning,
        on_chunk=lambda piece: emit("chunk", piece),
        on_event=lambda kind, payload: (
            job["usage"].append(payload) if kind == "usage" else None,
            emit(kind, payload),
        ),
        output_name=job["output_name"],
    )
    write_meta(job, provider, reasoning)   # sidecar .meta.json, ver 5.5
    emit("done", {"output": job["output_name"]})
    job["status"] = "done"
except Exception as exc:
    emit("error", f"{type(exc).__name__}: {exc}")
    job["status"] = "error"
```

### 5.2 Rutas

| Ruta | Método | Comportamiento |
|---|---|---|
| `/` | GET | `index.html` con **dos secciones** (ver 5.5 y 5.8): «Archivos subidos» —formulario de subida + tabla de PDFs en `docs/` (nombre enlazando a `/docs/<name>`, tamaño, nº de resultados existentes y acción de borrar)— y «Resultados anteriores» —corridas agrupadas por documento, indentadas bajo un encabezado visualmente distinto, cada una con **tag del modelo**, fecha, enlaces ver/descargar y acción de borrar—. Si se borró el PDF, el grupo queda accesible y se marca «PDF eliminado». Nav con enlace a `/config`. |
| `/upload` | POST | Valida que el archivo termine en `.pdf`; guarda en `docs/` con `werkzeug.utils.secure_filename`; redirect a `/docs/<name>` (a la página del documento recién subido, para previsualizarlo y procesarlo). |
| `/docs/<name>` | GET | Página del documento (`doc.html`): **previsualización del PDF** embebida (`<embed src="/docs/<name>/file" type="application/pdf">` con enlace de fallback «Abrir PDF»), formulario «Procesar» (combobox de modelos 5.4 + selector de razonamiento + ficha de metadatos del modelo) e historial de resultados de ese documento con sus tags. 404 si no existe `docs/<name>.pdf`. `name` sanitizado. |
| `/docs/<name>/file` | GET | Sirve el PDF inline: `send_from_directory("docs", "<name>.pdf")` (`as_attachment=False`, el navegador lo renderiza). Sanitizar `name`. |
| `/docs/<name>/delete` | POST | Borra solo `docs/<name>.pdf` tras confirmación. Conserva resultados previos para no destruir OCR útil; si hay un job `running` para el documento, rechaza la acción y lo informa. Redirect al índice. |
| `/api/models` | GET | Query param `provider` (por defecto el activo). Devuelve JSON sin caché (`Cache-Control: no-store`) con el catálogo normalizado de `providers.list_models` (ver 5.3): `[{"id", "name", "pricing"?, "context_length"?, "supports_reasoning"?, "reasoning_mode"?, "reasoning_efforts"?, "reasoning_mandatory"?}]`, ordenado por `id`. Errores del proveedor → 502 con `{"error": mensaje}` (la UI degrada a texto libre, ver 5.4). |
| `/process/<name>` | POST | `name` sanitizado (stem, sin extensión); exige que exista `docs/<name>.pdf`. Lee `model` y `reasoning` del form: si vienen vacíos, usa los del proveedor activo; `model` vacío tras eso → 400 con mensaje. Valida contra los esfuerzos del modelo cuando están en caché; un effort obsoleto para un modelo sin control explícito se ignora y equivale a no enviar. Resuelve `get_active_provider(load_config())`, genera `output_name` con tag de modelo + timestamp (ver 5.5), crea el job y arranca el hilo; redirect a `/jobs/<job_id>`. |
| `/jobs/<job_id>` | GET | Pantalla de procesado (`job.html`, ver 5.6). 404 si el job no existe. |
| `/jobs/<job_id>/stream` | GET | **SSE** (`text/event-stream`). Emite los eventos de `job["events"]` con replay desde el inicio (soporta que el cliente conecte tarde) y sigue hasta agotar tras `done`/`error`. Formato por evento: `event: <kind>\ndata: <payload_json>\n\n`. Mientras no haya eventos nuevos, enviar comentario keepalive `: ping\n\n` cada ~15 s (o dormir breve y reintentar; evitar busy-loop agresivo). |
| `/output/<name>` | GET | Renderiza `output/<name>.md` a HTML con la librería `markdown` y lo muestra (`output.html`): cabecera con **badges** (tag del modelo, proveedor, fecha, tokens/coste si hay sidecar), contenido renderizado y enlace de descarga. 404 si no existe. Sanitizar `name`. |
| `/output/<name>/raw` | GET | `send_from_directory("output", "<name>.md", as_attachment=True)`. |
| `/output/<name>/delete` | POST | Borra `output/<name>.md` y, si existe, `output/<name>.meta.json` tras confirmación. Redirect al índice o al documento de origen según el formulario. |
| `/config` | GET | **Página de configuración** (`config.html`): una tarjeta por proveedor con `api-url` (texto), `api-key` (password, precargada — app local), `model` por defecto (combobox con catálogo y fallback a texto libre), `reasoning` por defecto dinámico según el modelo seleccionado; radio de proveedor activo; botón «Probar conexión» por tarjeta. Todo precargado desde `load_config()`. |
| `/config` | POST | Reconstruye el dict desde el form (preservando claves ajenas al form si las hubiera), `save_config(config)`, redirect a `/config` con flash de confirmación. |
| `/config/test/<provider>` | POST | Toma `api-url`/`api-key` del form de esa tarjeta (sin guardar), llama `providers.list_models`; redirect a `/config` con flash «Conexión OK: N modelos disponibles» o «Error: …». |

### 5.3 `providers.py` (nuevo, pequeño) — catálogo y capacidades

Módulo de funciones (sin clases) para aprovechar lo que publica cada API:

```python
REASONING_EFFORTS = ["low", "medium", "high", "minimal", "xhigh", "max"]

def list_models(api_key: str, base_url: str) -> list[dict]
```

- `OpenAI(api_key=api_key, base_url=base_url).models.list()`. Los modelos del
  SDK (pydantic) admiten campos extra: usar `m.model_dump()` por cada uno para
  **conservar los metadatos del proveedor** (`pricing`, `context_length`,
  `supported_parameters`, `description`... lo que haya).
- Normaliza cada entrada a:
  - `id`, `name` (fallback: `id`).
  - `pricing`: dict tal cual venga (OpenRouter: `{"prompt", "completion",
    "image", ...}` en USD **por token**, como strings). Ausente si el
    proveedor no lo publica.
  - `context_length`: int si viene.
  - `supports_reasoning`: `True` si `"reasoning" in supported_parameters`;
    `False` si el campo existe y no lo contiene; `None` si el proveedor no
    publica `supported_parameters` (desconocido), salvo que exista un bloque
    explícito de reasoning.
  - `reasoning_efforts`: lista de esfuerzos permitidos si el proveedor la
    publica; `none` se trata como la opción de UI «No enviar» y no se lista
    como effort independiente. `None` significa que no hay allowlist conocida;
    una lista vacía significa que el modelo no admite effort explícito.
  - `reasoning_default_effort`: default publicado por el proveedor, si existe.
  - `reasoning_mandatory`: `True` cuando el modelo no permite desactivar el
    razonamiento.
  - `reasoning_mode`: `"effort"` para niveles, `"toggle"` para modelos que
    solo admiten razonamiento por defecto/activado, ausente si se desconoce.
- Ordena por `id`. Los errores del SDK se propagan (las rutas los convierten
  en 502 / flash).
- **Caché en memoria** con TTL de 5 min: `{(base_url): (timestamp, data)}`.
  La usa también el job manager para recuperar `pricing` al crear el job.

**Reglas específicas de OpenCode:** el endpoint público `/models` devuelve
principalmente IDs y no expone todo `models.dev`. El normalizador debe completar
las capacidades conocidas por familia y transporte, siguiendo la matriz:

| Familia/modelo | Transporte | Reasoning |
|---|---|---|
| GPT de OpenCode | `/responses` | `reasoning={"effort": ...}`; `low`/`medium`/`high`/`xhigh` como conjunto base |
| GLM-5.2 | `/chat/completions` | `reasoning_effort` raíz; `high`/`max` |
| MiMo, Kimi, DeepSeek, Qwen y GLM salvo GLM-5.2 | `/chat/completions` | modo `toggle`/default del proveedor; no inventar niveles |
| Otros OpenAI-compatible compatibles | `/chat/completions` | `reasoning_effort` raíz con esfuerzos publicados o estándar |

Esta matriz se deriva de `models.dev` y de `packages/opencode/src/provider/transform.ts`
de OpenCode. Si el proveedor comienza a publicar `reasoning_options`, esa
metadata tiene prioridad: `type="effort"` usa sus `values`; `type="toggle"`
desactiva el selector de niveles; `type="budget_tokens"` requiere un adaptador
de presupuesto explícito.

```python
def format_price(per_token: str | float | None) -> str
def format_context(n: int | None) -> str
```

- `format_price`: USD por token → `"$X.XX / M tokens"` (`float(p) * 1_000_000`,
  2 decimales; `"gratis"` si es 0; `""` si es None/inválido).
- `format_context`: `128000` → `"128k tokens"`.

### 5.4 Selector de modelo (combobox buscador) y nivel de razonamiento

En `doc.html`, el formulario «Procesar» lleva:

**Combobox de modelos** (JS vanilla, accesible):

- Estructura: `<input type="text" role="combobox" aria-expanded aria-controls
  aria-autocomplete="list">` + `<ul role="listbox">` con `<li role="option">`.
- Datos: `fetch("/api/models?provider=<activo>")` al cargar la página (o al
  primer focus); se guardan en memoria del navegador.
- **Filtrado cliente**: con el input **vacío se muestran todas** las opciones;
  al escribir, se muestran las que contienen la subcadena (case-insensitive)
  en `id` o `name` («mini» → todos los modelos con «mini»).
- Teclado: `↓`/`↑` mueven la opción activa, `Enter` elige, `Esc` cierra; click
  elige. Al elegir, el input muestra el `id` del modelo.
- Valor por defecto: el `model` del config del proveedor activo (preseleccionado).
- El form envía un `<input type="hidden" name="model">` sincronizado con la
  elección (si el usuario escribe un id a mano y no elige de la lista, se
  acepta tal cual — permite modelos no listados).
- **Degradación**: si `/api/models` responde 502, el combobox funciona como
  campo de texto libre (sin desplegable) y se muestra una nota discreta con el
  error. Nunca bloquea el procesado.

**Ficha de metadatos del modelo seleccionado** (bajo el combobox, texto muted):

- Precio entrada/salida (`format_price` de `pricing.prompt`/`completion`),
  contexto (`format_context`), badge «razonamiento» si
  `supports_reasoning is True`. Solo se muestran los datos que existan.

**Selector de nivel de razonamiento**:

- `<select name="reasoning">` con «Por defecto del proveedor» (value `""` →
  el servidor usa el default del config/transporte), los esfuerzos de
  `reasoning_efforts` cuando existen y «No enviar» (value `"off"` → el
  servidor envía `reasoning=None` aunque el config tenga valor).
- `none` publicado por un proveedor se representa como «No enviar» y no como
  una opción separada.
- **Habilitado solo cuando corresponde**: si `supports_reasoning is False` →
  deshabilitado con nota «este modelo no admite razonamiento». Si
  `reasoning_mode == "toggle"` → se muestra solo el default y el selector de
  niveles queda deshabilitado con nota «este modelo usa el razonamiento del
  proveedor y no admite niveles explícitos». Si es `True`/`None` y el modo es
  `effort`, se habilitan los valores publicados o el fallback del transporte.
- Si `reasoning_mandatory is True`, no se ofrece «No enviar».
- El formulario de `/config` reutiliza el mismo combobox y selector dinámico
  para el modelo/reasoning por defecto de cada proveedor.

### 5.5 Resultados múltiples con tag de modelo

**Nombrado** (en `/process/<name>`):

```python
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model)   # "qwen/qwen3-vl" → "qwen_qwen3-vl"
output_name = f"{stem}__{slug}__{timestamp}"
```

Cada corrida produce `output/{output_name}.md` (vía `output_name` de
`run_ai_ocr`) → reprocesar el mismo documento **nunca sobrescribe**: genera un
resultado nuevo con su propio tag.

**Sidecar de metadatos** `output/{output_name}.meta.json` (lo escribe
`write_meta` del job manager al terminar con éxito):

```json
{
    "doc": "<stem del pdf>",
    "model": "<id del modelo usado>",
    "provider": "<nombre del proveedor>",
    "reasoning": "<effort usado o null>",
    "started": "<iso8601>", "finished": "<iso8601>",
    "usage": [<usage por lote, tal cual llegó>],
    "estimated_cost": <float | null>
}
```

`estimated_cost`: solo si hay `usage` con `prompt_tokens`/`completion_tokens`
y `pricing` conocido (del caché del catálogo): `prompt_tokens * pricing.prompt
+ completion_tokens * pricing.completion`. Si falta cualquier dato, `null`.

**Listados** (index y página del documento):

- Escanear `output/*.md`; para cada uno intentar leer su `.meta.json`.
  - Con meta: badge con el **id del modelo** (ese es el tag), fecha
    (`finished`), proveedor.
  - Sin meta pero nombre con patrón `{doc}__{slug}__{ts}`: parsear slug como
    tag y ts como fecha.
  - Legacy `output/{stem}.md` (anteriores a este cambio): se listan con badge
    neutro «sin etiqueta» bajo su documento.
- Orden: más reciente primero. Agrupados por documento en el index; filtrados
  por `meta["doc"] == name` (o prefijo de nombre) en `/docs/<name>`.
- El título del documento del grupo debe tener jerarquía tipográfica distinta
  y los resultados deben aparecer indentados bajo una guía lateral. Si se
  elimina el PDF pero quedan resultados, el grupo conserva sus enlaces de
  resultado y se marca como «PDF eliminado».

### 5.6 Pantalla de procesado: feedback constante y detallado

`job.html` presenta tres zonas, alimentadas por el `EventSource`:

1. **Cabecera de estado**: nombre del documento, **badge con el modelo**,
   spinner mientras `running`, cronómetro (JS, cuenta desde `job["started"]`),
   paso actual («Convirtiendo PDF a imágenes» / «Lote 2/5» / «Guardando
   Markdown») y **barra de progreso** de lotes (`index`/`total` de los eventos
   `status`).
2. **Texto en vivo**: `<pre>` (con `white-space: pre-wrap`) donde se van
   volcando los eventos `chunk`, tal cual llegan.
3. **Registro** (panel monospace con auto-scroll): las líneas `log`, que
   **iteran el feedback por consola de la herramienta actual** —misma
   redacción y estilo `├`/`└`, ahora con detalle—:
   - `Convirtiendo PDF a imágenes`
   - `└ 42 páginas en 3 lotes`
   - `├ Construyendo mensaje (lote 1/3)`
   - `├ Enviando solicitud al modelo`
   - `└ Respuesta recibida (12.4 s)`
   - `Guardando a Markdown` / `Listo!`
4. **Uso y coste** (línea muted en la cabecera o al final): acumulado de
   tokens (`prompt_tokens`/`completion_tokens` de los eventos `usage`) y, si
   el job tiene `pricing`, coste estimado en vivo. Solo se muestra si hay
   datos.
5. **Final**: al recibir `done` → sustituir spinner por ✓, mostrar resumen
   (duración total, tokens, coste estimado si hay) y enlace destacado a
   `/output/<output_name>`. Al recibir `error` → banner rojo con el mensaje
   (tipo de excepción + detalle, p.ej. `AuthenticationError: ...`).
    En ambos casos el JS cierra el `EventSource`. El panel verde con el enlace
    al resultado lleva `hidden` desde el HTML y solo se muestra tras `done`;
    el CSS debe preservar `[hidden] { display: none !important; }` aunque el
    panel use `display: flex`.

- La barra de progreso de lotes empieza oculta durante la conversión y se
  muestra recién al recibir un evento `status` de lote con `index >= 1`; el
  texto de estado sigue mostrando «Convirtiendo PDF a imágenes» antes de eso.
- El listener SSE para `error` debe ignorar eventos nativos de `EventSource`
  sin `event.data`; solo renderiza el banner cuando recibe el evento SSE de
  error serializado por el job. Así nunca se muestra un mensaje `undefined`.

JS vanilla: `event.data` viene JSON-encoded → `JSON.parse`; dispatch por
`event.type` (`log`/`status`/`chunk`/`usage`/`done`/`error`). El catálogo de
modelos se solicita con `cache: "no-store"` y la respuesta HTTP lleva
`Cache-Control: no-store` para evitar capacidades obsoletas en el navegador.

### 5.7 Templates y estáticos

- `templates/base.html`: layout común, header con marca «not-cr», nav
  «Documentos» (`/`) / «Configuración» (`/config`), toggle de tema persistente
  y bloque para flashes.
- `templates/index.html`: secciones «Archivos subidos» y «Resultados
  anteriores» (5.5).
- `templates/doc.html`: preview + formulario de procesado + historial (5.2,
  5.4); lleva el JS del combobox (puede ser `static/combobox.js`).
- `templates/job.html`: pantalla de procesado (5.6); lleva el JS del stream
  (puede ser `static/job.js`).
- `templates/output.html`: badges + Markdown renderizado + MathJax 3 para
  `$...$`/`$$...$$` + descarga y borrado.
- `templates/config.html`: página de configuración (5.2), combobox y
  selector de reasoning por proveedor.
- `static/combobox.js`: combobox reutilizable para documento/configuración.
- `static/job.js`: cronómetro, SSE, progreso diferido y errores nativos.
- `static/theme.js`: toggle claro/oscuro con `localStorage` y
  `prefers-color-scheme`.
- `static/style.css`: todo el diseño (5.8), incluyendo `[hidden]` explícito y
  breakpoints tablet.

### 5.8 Diseño de la interfaz

Principios: server-rendered, sin framework CSS ni build; un solo
`static/style.css` con custom properties; JS vanilla solo para combobox,
cronómetro, SSE y toggle de tema. Tema claro/oscuro, denso pero aireado,
aspecto de herramienta de escritorio moderna.

**Layout general**

- Header superior (no fijo): marca `not-cr` a la izquierda (mono, bold), nav a
  la derecha con enlaces texto; borde inferior fino.
- Contenedor centrado `max-width: 1100px`, padding lateral `1rem`.
- Las secciones son **tarjetas**: fondo blanco, `border: 1px solid
  var(--border)`, `border-radius: 10px`, padding `1rem 1.25rem`, separadas
  `1.25rem`; título de tarjeta `<h2>` pequeño.
- `doc.html` y `job.html` usan grid de 2 columnas (preview/texto a la
  izquierda, formulario/registro a la derecha) que colapsa a 1 columna bajo
  `900px`.
- Desde tablet los flex containers deben permitir wrapping: top bar alineada
  con `align-items: center`, formularios de subida flexibles, filas de
  resultados envolventes y títulos de página con `overflow-wrap: anywhere`.
- La top bar debe envolver la navegación en pantallas pequeñas sin desalinear
  enlaces y toggle.

**Color y tipografía** (CSS custom properties en `:root`)

- `--bg: #f5f6f8` (fondo página), `--surface: #ffffff` (tarjetas),
  `--text: #1f2329`, `--muted: #6b7280`, `--border: #e2e5ea`,
  `--accent: #3b5bdb` (botones primarios, enlaces, fill de progreso),
  `--accent-soft: #edf2ff` (fondo de badges/selección),
  `--ok: #2b8a3e`, `--err: #c92a2a`, `--err-soft: #fff0f0`.
- Texto: stack del sistema (`system-ui, -apple-system, "Segoe UI", sans-serif`),
  base 15–16 px. Mono para marca, log, `<pre>`, tags y cronómetro:
  `ui-monospace, "SF Mono", Menlo, Consolas, monospace`.
- El modo oscuro redefine las custom properties de superficie, texto, borde,
  muted, accent, estados y fondos de código bajo `:root[data-theme="dark"]`.
  El valor elegido se guarda como `not-cr-theme`; sin preferencia guardada se
  respeta `prefers-color-scheme`.

**Componentes**

- **Botones** `.btn`: primario (fondo `--accent`, texto blanco) y secundario
  (fondo blanco, borde `--border`, texto `--text`); `border-radius: 8px`.
- **Badge / tag de modelo** `.tag`: pill monospace, fondo `--accent-soft`,
  texto `--accent`, padding `0.1rem 0.5rem`; variante `.tag--muted` gris para
  «sin etiqueta» y metadatos; `.tag--ok` / `.tag--err` para estados.
- **Combobox** `.combo`: input normal; el desplegable es una lista bajo el
  input con borde, `border-radius: 8px`, `max-height: 16rem` con scroll,
  sombra suave; opción activa (teclado/hover) con fondo `--accent-soft`; el
  `id` del modelo en mono y el `name`/metadatos en muted a la derecha.
- **Ficha de modelo**: línea en `--muted` bajo el combobox
  («$0.30 / M tokens entrada · $1.20 / M salida · 128k tokens ·
  [razonamiento]»).
- **Formularios**: label sobre el input, inputs `width: 100%`, borde
  `--border`, `border-radius: 8px`, `:focus` con outline `--accent` suave.
- **Barra de progreso** `.progress`: pista gris clara, fill `--accent`,
  transición suave de `width`.
- **Registro** `.log`: panel con borde, fondo `#fafbfc`, mono, líneas con
  `├`/`└`, auto-scroll al último evento.
- **Texto en vivo**: `<pre>` con `white-space: pre-wrap`, `min-height` para
  que no «salte» la página.
- **Preview PDF**: `<embed>` `width: 100%`, `height: 70vh`, borde `--border`;
  debajo, enlace «Abrir PDF en pestaña nueva».
- **Flashes**: banner a todo lo ancho del contenedor, verde suave (éxito) o
  `--err-soft` (error), `border-radius: 8px`.
- **Estados vacíos**: texto `--muted` («Aún no hay resultados para este
  documento…»).
- **Tablas/listados**: sin bordes verticales; filas separadas por borde
  inferior fino; hover con fondo `#fafbfc`; columnas de fecha/tamaño en
  `--muted`.
- **Resultados agrupados**: el nombre del PDF es un encabezado fuerte y
  neutral; los Markdown tienen peso/color secundarios, contador de resultados,
  indentación y guía lateral con marcadores alineados.
- **Borrado**: acciones `POST` con `confirm()` y estilo de peligro; nunca
  aceptar borrado por `GET`.

### 5.9 Arranque

```python
if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, threaded=True)
```

`host="0.0.0.0"` para que la app sea accesible desde otras máquinas de la red
local (no solo `localhost`). `threaded=True` es necesario para que el SSE no
bloquee otras peticiones. Documentar también `flask --app app run --host 0.0.0.0`
en el README.

> Commits sugeridos (atómicos):
> `feat(app): add job manager with background OCR threads and progress events`
> `feat(providers): add model catalog with pricing and reasoning capabilities`
> `feat(app): add upload, document page with PDF preview and result history`
> `feat(app): add searchable model combobox and per-run reasoning selector`
> `feat(app): tag each OCR run with model and timestamp, keep result history`
> `feat(app): stream detailed OCR progress over SSE`
> `feat(app): add markdown output view with model badges and download`
> `feat(app): add provider settings page with connection test`
> `feat(app): add document and result deletion`
> `feat(ui): add MathJax output, dark mode, and responsive refinements`
> `fix(opencode): align reasoning variants with provider transports`

### 5.10 Robustez y features posteriores

- `output.html` carga MathJax 3 y configura `$...$`/`$$...$$`; el Markdown
  almacenado no se modifica.
- La UI incluye un toggle claro/oscuro persistido en `localStorage`, respetando
  `prefers-color-scheme` como valor inicial.
- Los títulos de página largos deben partirse (`overflow-wrap: anywhere`) y la
  top bar/flex containers deben envolver en tablet. Los marcadores de grupos de
  resultados se calculan desde el padding de la guía, no con offsets mágicos.
- El CSS debe declarar `[hidden] { display: none !important; }`: clases con
  `display: flex` no pueden hacer visibles paneles pendientes.
- La barra de progreso de lotes se mantiene oculta hasta el primer lote real;
  el panel final solo aparece tras `done`.
- El listener SSE ignora errores nativos sin `event.data` para no mostrar
  `undefined` y conserva el banner de errores serializados del job.
- Borrar un documento solo elimina el PDF y conserva resultados; borrar un
  resultado elimina también su sidecar. Ambas acciones son `POST` confirmadas.

---

## 6. Fase 4 — Cierre

### 6.1 README

Reescribir `README.md` (hoy es una línea) con:

- Qué hace (PDF → Markdown vía LLMs multimodales, por lotes, con progreso en
  vivo y resultados etiquetados por modelo).
- Requisitos: Python 3.13, `uv`, poppler (`pdftoppm`).
- Setup: `uv sync`; `cp config.example.json config.json`; editar keys o usar
  la página `/config` de la web (URL base y API key por proveedor, con botón
  de probar conexión).
- Uso: `python app.py` (o `flask --app app run`), abrir `http://localhost:5000`,
  subir PDF, previsualizarlo en la página del documento, elegir modelo en el
  buscador (con precios y nivel de razonamiento cuando el proveedor los
  publica), procesar, ver el progreso en vivo y descargar el `.md`. Cada
  corrida genera un resultado nuevo con el tag del modelo — reprocesar nunca
  sobrescribe. La vista de resultados renderiza LaTeX mediante MathJax y
  permite borrar PDFs/resultados con confirmación; la interfaz incluye modo
  oscuro persistente.
- Proveedores soportados: OpenRouter y OpenCode Zen/Go, accedidos con la
  librería oficial `openai` cambiando `base_url`. OpenRouter usa
  `chat/completions`; OpenCode combina `/responses` para GPT, y
  `/chat/completions` para sus modelos OpenAI-compatible. Los modelos de
  OpenCode publicados solo bajo `/messages` o APIs específicas no se deben
  ofrecer hasta implementar su adaptador. Los metadatos de precios,
  `supported_parameters`, `reasoning_options` y capacidades dependen de lo
  que publique cada catálogo.
- Tests: `.venv/bin/python -m unittest discover -s tests -v`.

> Commit: `docs: rewrite README for the webapp`

### 6.2 Verificación final

1. Suite completa en verde: 11 tests.
2. Smoke de rutas sin red externa, p.ej.:
    `.venv/bin/python -c "import app; c = app.app.test_client(); print(c.get('/').status_code, c.get('/config').status_code)"`.
    (Sin API key, `/api/models` debe responder 502 con mensaje, no colgarse ni
    500.)
3. Smoke de transportes sin red externa: mockear `OpenAI` y comprobar que
   OpenRouter envía `extra_body.reasoning`, OpenCode chat envía
   `reasoning_effort`, OpenCode Responses convierte `input_image`/`output_text`
   y modelos `toggle` no envían effort. Comprobar también que el catálogo de
   OpenRouter no recibe heurísticas de OpenCode.
4. Smoke de borrado y SSE con jobs ficticios: borrar un PDF conserva sus
   resultados, borrar un Markdown borra también su sidecar, el panel final
   permanece oculto antes de `done` y un error SSE sin `event.data` no muestra
   `undefined`.
5. Smoke E2E real (requiere que el usuario ponga una API key): subir un PDF
   pequeño desde la web, previsualizarlo, elegir modelo con el buscador,
   procesar viendo el progreso detallado en vivo, y comprobar el
   `output/<doc>__<modelo>__<ts>.md` final, su `.meta.json` y los badges en la
   vista. Procesar una segunda vez con otro modelo y comprobar que conviven
   ambos resultados con sus tags.
   **Pedir la key al usuario; no inventarla ni hardcodearla.**

### 6.3 Checklist de aceptación

- [ ] 11 tests en verde (`test_config.py` ×2, `MarkdownSeparatorTests` ×3,
      `CleanMarkdownTests` ×4, `OcrPipelineTests` ×2).
- [ ] Ningún `git push` realizado; historial con commits atómicos.
- [ ] `config.json` fuera del tracking; `config.example.json` commiteado;
      ningún secret en el historial.
- [ ] `main.py` importable sin efectos laterales (sin loop CLI).
- [ ] Documentos procesados producen `output/<doc>__<modelo>__<ts>.md` + su
      `.meta.json`; el directorio de imágenes temporal se limpia siempre
      (éxito o error).
- [ ] El mismo documento se puede procesar varias veces: ninguna corrida
      sobrescribe otra y cada resultado muestra el tag del modelo usado.
- [ ] Selector de modelo combobox: vacío muestra todos los modelos del
      proveedor; al escribir filtra por subcadena; degrada a texto libre si
      el catálogo falla.
- [ ] La ficha del modelo muestra precios y contexto cuando el proveedor los
      publica; el selector de razonamiento usa `reasoning_efforts`/
      `reasoning_options`, respeta `toggle`/`mandatory` y no envía campos
      incompatibles con el transporte.
- [ ] Pantalla de procesado con feedback constante: paso actual, barra de
      lotes i/N, cronómetro, registro estilo árbol con tiempos por lote,
      texto en vivo y tokens/coste cuando hay datos.
- [ ] Index con secciones «Archivos subidos» y «Resultados anteriores»;
      previsualización del PDF embebida en la página del documento; borrado
      seguro de documentos/resultados y grupos huérfanos sin enlaces rotos.
- [ ] Página `/config` operativa: URL base y API key por proveedor, defaults
      de modelo/razonamiento, proveedor activo y prueba de conexión.
- [ ] Interfaz acorde a la sección 5.8 (tarjetas, badges, combobox, tema
      claro/oscuro, responsive tablet y wrapping de títulos/acciones).
- [ ] `README.md` actualizado; `pyproject.toml` con todas las dependencias.

---

## 7. Referencia rápida de firmas (contrato)

```python
# processing_text.py
clean_markdown(text: str) -> str
markdown_separator(prev: str, next: str) -> str
to_md(contenido: str, nombre: str) -> None

# processing_images.py  (HTTP vía SDK openai; requests queda prohibido aquí)
BATCH_SIZE = 20
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
image_prep(images_dir: str) -> list[list[str]]
message_build(image_batch: list[str], context: str | None = None) -> list[dict]
to_model(api_key, model, message, base_url=DEFAULT_BASE_URL,
         reasoning=None, write_output=False) -> dict   # dict plano normalizado
# OpenRouter: chat + extra_body.reasoning
# OpenCode GPT: Responses + reasoning
# OpenCode OpenAI-compatible: chat + reasoning_effort; toggle no effort

# processing_pdf.py
convert(pdf_file: str, output_dir: str) -> None

# ocr.py  (importar como: import processing_images / import processing_pdf / import processing_text)
# on_event: extensión opcional; eventos ("log", str) ("status", dict) ("usage", dict)
process_pdf(pdf_path, image_dir, api_key, model, on_chunk=None,
            base_url=..., reasoning=None, on_event=None) -> str

# main.py  (importar como: import ocr / import processing_text)
# on_event/output_name: extensiones opcionales (con None → comportamiento del test)
CONFIG_PATH = Path("config.json")
load_config() -> dict
run_ai_ocr(pdf_path, api_key, model, base_url=None, reasoning=None,
           on_chunk=None, on_event=None, output_name=None) -> None

# providers.py  (catálogo vía SDK openai; conserva metadatos del proveedor)
REASONING_EFFORTS = ["low", "medium", "high", "minimal", "xhigh", "max"]
list_models(api_key: str, base_url: str) -> list[dict]  # id, name, pricing?, context_length?, supports_reasoning, reasoning_mode?, reasoning_efforts?
format_price(per_token) -> str   # USD/token → "$X.XX / M tokens"
format_context(n) -> str         # 128000 → "128k tokens"

# app.py
CONFIG_PATH = Path("config.json")
load_config() -> dict
save_config(config: dict) -> None          # atómico: mkstemp + os.replace, sin restos tmp*
get_active_provider(config: dict) -> dict
# + app = Flask(__name__), job manager (jobs dict + _run_job + write_meta),
#   slug/output_name de corridas, y las rutas de la Fase 3
```

---

## 8. Addendum — aprendizajes de la implementación

Este addendum prevalece sobre cualquier frase anterior que afirme que todos
los modelos de OpenCode son `chat/completions` o aceptan el mismo payload de
reasoning.

1. OpenCode debe tratarse como una familia de transportes, no como un único
   proveedor OpenAI-compatible. La matriz mínima soportada por esta webapp es:
   GPT/Grok Responses cuando el model id y la URL lo indican, OpenAI-compatible
   chat para modelos compatibles, y OpenRouter chat. Los transportes
   Anthropic/Google requieren adaptadores propios antes de ofrecer esos
   modelos.
2. El endpoint público de modelos de OpenCode puede devolver solo IDs; no debe
   interpretarse la ausencia de `reasoning_options` como una lista universal de
   esfuerzos. Para familias conocidas se siguen las reglas de
   `models.dev`/`provider/transform.ts`; si no hay certeza, se omite el campo y
   se usa el default del proveedor.
3. La normalización debe conservar y exponer suficiente información para que
   UI y servidor coincidan: `supports_reasoning`, `reasoning_mode`,
   `reasoning_efforts`, `reasoning_default_effort` y `reasoning_mandatory`.
4. Cada transporte necesita una prueba mock de request y response. Como mínimo:
   OpenRouter `extra_body.reasoning`, OpenCode chat `reasoning_effort`, OpenCode
   Responses `input_image`/`output_text`, y un modelo `toggle` sin effort.
5. Las features posteriores a la especificación inicial (MathJax, modo oscuro,
   borrado seguro, jerarquía de resultados, responsive tablet y robustez SSE)
   forman parte de la aceptación, no son cambios cosméticos opcionales.
