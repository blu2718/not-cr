from pathlib import Path

from pdf2image import convert_from_path

DPI = 150


def convert(pdf_file: str, output_dir: str) -> None:
    convert_from_path(
        pdf_file,
        dpi=DPI,
        output_folder=output_dir,
        fmt="jpeg",
        jpegopt={"quality": 70, "optimize": True, "progressive": False},
        output_file=Path(pdf_file).stem,
        paths_only=True,
    )
