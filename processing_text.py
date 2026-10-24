from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

WRAPPER_OPENINGS = ("```markdown", "```")


def clean_markdown(text: str) -> str:
    """Quita el fence ```markdown / ``` que envuelve toda la respuesta, si lo hay."""
    lines = text.strip().split("\n")

    if len(lines) >= 2 and lines[0].strip() in WRAPPER_OPENINGS and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return text.strip()


def markdown_separator(prev: str, next: str) -> str:
    """Separador a insertar entre el texto acumulado y la página siguiente."""
    if prev.endswith("-"):
        return ""
    if next.startswith("#"):
        return "\n\n"
    return " "


def to_md(contenido: str, nombre: str) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{nombre}.md").write_text(contenido, encoding="utf-8")
