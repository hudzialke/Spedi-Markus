from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from markus_dispatcher.core.job_archive import next_document_index, slugify

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGB", "L"):
        return image.convert("RGB")
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        return background
    return image.convert("RGB")


def save_pil_as_pod_pdf(
    documents_dir: Path,
    image: Image.Image,
    *,
    label: str = "pod",
) -> Path:
    """Zapisuje jedno zdjęcie jako osobny jednostronicowy PDF POD."""
    documents_dir.mkdir(parents=True, exist_ok=True)
    idx = next_document_index(documents_dir)
    dest = documents_dir / f"{idx:02d}_{slugify(label or 'pod')}.pdf"
    _to_rgb(image).save(dest, "PDF", resolution=150.0)
    return dest


def save_image_file_as_pod_pdf(
    documents_dir: Path,
    source: Path,
    *,
    label: str = "",
) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"Brak pliku: {source}")
    suffix = source.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(
            "POD: użyj zdjęcia (.jpg, .jpeg, .png, .webp, .bmp, .tif)."
        )
    with Image.open(source) as img:
        img.load()
        return save_pil_as_pod_pdf(
            documents_dir,
            img,
            label=label or f"pod_{source.stem}",
        )


def save_image_bytes_as_pod_pdf(
    documents_dir: Path,
    data: bytes,
    *,
    label: str = "pod",
) -> Path:
    with Image.open(BytesIO(data)) as img:
        img.load()
        return save_pil_as_pod_pdf(documents_dir, img, label=label)
