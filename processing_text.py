from pathlib import Path


def clean_markdown(text: str) -> str:
    """Remove only a balanced markdown fence wrapper."""
    lines = text.splitlines()
    if not lines or lines[0] not in {"```", "```markdown"}:
        return text

    closing_index = next(
        (index for index in range(len(lines) - 1, 0, -1) if lines[index] != ""),
        None,
    )
    if closing_index is None or lines[closing_index] != "```":
        return text

    return "\n".join(lines[1:closing_index])


def markdown_separator(prev: str, next: str) -> str:
    if prev.endswith("-"):
        return ""
    if next.startswith("#"):
        return "\n\n"
    return " "


def to_md(contenido: str, nombre: str) -> None:
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{nombre}.md").write_text(contenido, encoding="utf-8")
