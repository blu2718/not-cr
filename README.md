# not-cr

A small web app that turns PDFs into clean Markdown using multimodal (vision) LLMs from
[OpenRouter](https://openrouter.ai). Each PDF is transcribed **page by page**: every request
carries one page image plus the last 250 characters already transcribed, so paragraphs,
lists and formulas continue across page breaks.

- Live progress for every run: current page, page map, elapsed time, estimated time left,
  the processing log, the text as it arrives, tokens and estimated cost.
- Every run is a new result tagged with its model (`output/<doc>__<model>__<timestamp>.md`
  plus a `.meta.json` sidecar). Reprocessing never overwrites anything; a run that fails
  halfway keeps the pages it finished as a "partial" result.
- Bulk actions: upload several PDFs at once, process several documents with one model
  (at most 2 run at the same time, the rest wait in a queue), delete several at once.
- PDFs can be deleted alone (results are kept) or together with their results.
- Results can be searched, filtered by model or folder, sorted, grouped by document or
  folder, given a title and a note, and one of them marked as the document's main result.
- Reader view with MathJax, raw Markdown view, and side-by-side comparison with the PDF.

The interface is in Spanish.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Poppler (`pdftoppm`, `pdfinfo`), used by `pdf2image`
- [Tailscale](https://tailscale.com), if you want to use it from other devices (see below)
- An OpenRouter API key

## Setup

```bash
uv sync
cp config.example.json config.json
```

Put your API key in `config.json`, or leave it empty and set it later from the
**Configuración** page (`/config`), which also has a "Probar conexión" button.
`config.json` is git-ignored; the key is never sent to the browser.

```json
{
    "openrouter": {
        "api-url": "https://openrouter.ai/api/v1",
        "api-key": "",
        "model": "qwen/qwen3-vl-235b-a22b-instruct",
        "reasoning": ""
    }
}
```

`model` and `reasoning` are only defaults: both can be changed for each run. `reasoning`
is sent only when the chosen model supports it (`""` means don't send it).

## Usage

```bash
uv run python app.py
```

By default the app listens only on the tailnet address of this machine (MagicDNS name
`arenarium`) on port 5000, so open `http://arenarium:5000` from any device in the tailnet.
There is no login: the tailnet is the access boundary. Tailscale must be running when the
app starts.

- Local use only: `NOT_CR_HOST=127.0.0.1 uv run python app.py`
- Other port: `NOT_CR_PORT=8000`
- Extra host names to accept (for example the full MagicDNS name):
  `NOT_CR_ALLOWED_HOSTS=arenarium.example.ts.net`
- Alternative: `uv run flask --app app run --host arenarium`

Typical flow: upload one or more PDFs (button or drag and drop), open a document to preview
it, pick a model in the searchable list, process it (or select several documents and
process them together), follow the progress on the home page or on the run's own page,
then organize the results (folders, titles, notes, main result) and download them as `.md`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests do not touch the network or the real `docs/` and `output/` folders.
