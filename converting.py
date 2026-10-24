"""Conversión a PDF de lo que no lo es: DOCX/PPTX con LibreOffice, imágenes con Pillow."""

import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

import processing_pdf

PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = {".docx", ".pptx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS

CONVERT_TIMEOUT = 180
MAX_IMAGE_SIDE = 3000

# Archivo que identifica cada formato de Office dentro del ZIP.
OFFICE_MARKERS = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml"}
KIND_LABELS = {"docx": "DOCX", "pptx": "PPTX", "image": "Imagen"}

# LibreOffice no tolera instancias concurrentes y cada una ocupa bastante RAM.
SOFFICE_LOCK = threading.Lock()


class ConversionError(Exception):
    """Fallo de validación o conversión; el mensaje va tal cual a la interfaz."""


def kind_of(extension: str) -> str | None:
    extension = extension.lower()
    if extension in PDF_EXTENSIONS:
        return "pdf"
    if extension in OFFICE_EXTENSIONS:
        return extension[1:]
    if extension in IMAGE_EXTENSIONS:
        return "image"
    return None


def validate(path: Path, kind: str) -> None:
    if kind == "pdf":
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise ConversionError("no es un PDF")
    elif kind in OFFICE_MARKERS:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            raise ConversionError(f"no es un archivo {kind.upper()} válido") from None
        if OFFICE_MARKERS[kind] not in names:
            raise ConversionError(f"no es un archivo {kind.upper()} válido")
    elif kind == "image":
        try:
            with Image.open(path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise ConversionError("no es una imagen válida") from None
    else:
        raise ConversionError("formato no soportado (PDF, DOCX, PPTX o imagen)")


def _flatten(frame: Image.Image) -> Image.Image:
    """RGB sobre fondo blanco, con la orientación EXIF aplicada y tamaño acotado."""
    frame = ImageOps.exif_transpose(frame)
    if frame.mode in ("RGBA", "LA") or (frame.mode == "P" and "transparency" in frame.info):
        rgba = frame.convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))
    else:
        page = frame.convert("RGB")
    if max(page.size) > MAX_IMAGE_SIDE:
        page.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return page


def _image_to_pdf(src: Path, dest: Path) -> None:
    try:
        with Image.open(src) as image:
            if image.format == "GIF":
                frames = [image]  # un GIF animado: solo el primer fotograma
            else:
                frames = ImageSequence.Iterator(image)
            pages = [_flatten(frame) for frame in frames]
    except (UnidentifiedImageError, OSError) as exc:
        raise ConversionError(f"no se pudo leer la imagen ({exc})") from None
    if not pages:
        raise ConversionError("la imagen no tiene contenido")
    # A la misma resolución con la que se rasteriza: cada página recupera sus píxeles.
    pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:], resolution=processing_pdf.DPI)


def _office_to_pdf(src: Path, kind: str, dest: Path) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise ConversionError("convertir DOCX/PPTX necesita LibreOffice (soffice), que no está instalado")

    with tempfile.TemporaryDirectory(prefix="not-cr-convert-") as work:
        work = Path(work)
        source = work / f"input.{kind}"
        shutil.copyfile(src, source)
        command = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation={(work / 'profile').as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(source),
        ]
        with SOFFICE_LOCK:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=CONVERT_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise ConversionError(
                    f"LibreOffice no terminó la conversión en {CONVERT_TIMEOUT} s"
                ) from None
        output = work / "input.pdf"
        if result.returncode != 0 or not output.exists():
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            reason = detail[-1] if detail else f"código {result.returncode}"
            raise ConversionError(f"LibreOffice no pudo convertir el archivo ({reason})")
        shutil.move(output, dest)


def to_pdf(src: Path, kind: str, dest: Path) -> None:
    """Escribe en `dest` un PDF a partir de `src`. Lanza ConversionError si no puede."""
    if kind == "pdf":
        shutil.copyfile(src, dest)
    elif kind == "image":
        _image_to_pdf(src, dest)
    elif kind in OFFICE_MARKERS:
        _office_to_pdf(src, kind, dest)
    else:
        raise ConversionError("formato no soportado (PDF, DOCX, PPTX o imagen)")

    with open(dest, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise ConversionError("la conversión no produjo un PDF válido")
