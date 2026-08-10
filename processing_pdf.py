from pathlib import Path

from pdf2image import convert_from_path


def convert(pdf_file: str, output_dir: str) -> None:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    convert_from_path(
        pdf_file,
        output_folder=str(directory),
        fmt="jpeg",
        jpegopt={"quality": 70, "optimize": True, "progressive": False},
        output_file=Path(pdf_file).stem,
    )
