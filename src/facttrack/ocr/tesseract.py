"""Tesseract OCR wrapper for scanned lease images.

Invokes the system `tesseract` binary (5.x+) with sensible defaults for
hand-typed / typewritten land-record scans. Caches OCR output to disk so
re-running the clause parser doesn't re-OCR the same images.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from facttrack.config import PATHS, ensure_dirs

log = logging.getLogger(__name__)


class TesseractMissing(Exception):
    pass


@dataclass
class OCRResult:
    image_path: Path
    text_path: Path
    char_count: int
    cached: bool


def _tesseract_binary() -> str:
    binary = shutil.which("tesseract")
    if not binary:
        raise TesseractMissing(
            "tesseract binary not found on PATH. Install with: apt-get install tesseract-ocr"
        )
    return binary


def _ocr_cache_path_for(image_path: Path, county_fips: str | None = None) -> Path:
    """Cache OCR text alongside the image, in a sibling 'ocr' directory."""
    ensure_dirs()
    parent = image_path.parent
    cache_dir = parent / "_ocr"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / (image_path.stem + ".txt")


def ocr_image(image_path: Path, *, force: bool = False, psm: int = 6, lang: str = "eng") -> OCRResult:
    """Run Tesseract on `image_path`. Returns the text path + char count.

    PSM 6 = "Assume a single uniform block of text" — works well for
    scanned legal documents with consistent line spacing. Other useful
    values: 1 (auto OSD), 3 (auto, no OSD), 11 (sparse text).
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))
    cache = _ocr_cache_path_for(image_path)
    if cache.exists() and not force and cache.stat().st_size > 0:
        return OCRResult(image_path=image_path, text_path=cache,
                         char_count=cache.stat().st_size, cached=True)

    binary = _tesseract_binary()
    out_stem = str(cache.with_suffix(""))
    cmd = [binary, str(image_path), out_stem, "--psm", str(psm), "-l", lang]
    log.info("running tesseract: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"tesseract failed (exit {proc.returncode}): {proc.stderr[:500]}"
        )
    if not cache.exists():
        raise RuntimeError(f"tesseract finished but {cache} not produced")
    return OCRResult(image_path=image_path, text_path=cache,
                     char_count=cache.stat().st_size, cached=False)


def ocr_directory(images_dir: Path) -> list[OCRResult]:
    """OCR every PNG/JPG in a directory; return one result per image."""
    images_dir = Path(images_dir)
    results: list[OCRResult] = []
    for child in sorted(images_dir.iterdir()):
        if child.is_dir():
            continue
        if child.suffix.lower() not in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            continue
        try:
            results.append(ocr_image(child))
        except Exception as e:
            log.warning("OCR failed for %s: %s", child, e)
    return results


def read_ocr_text(image_path: Path) -> str:
    """Return the OCR'd text for an image — OCRs it on demand if not yet cached."""
    result = ocr_image(Path(image_path))
    return result.text_path.read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--psm", type=int, default=6)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = ocr_image(Path(args.image), force=args.force, psm=args.psm)
    print(f"OCR → {result.text_path} ({result.char_count} bytes, cached={result.cached})")
    print("--- first 500 chars ---")
    print(result.text_path.read_text(encoding="utf-8", errors="replace")[:500])
