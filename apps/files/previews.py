"""The small pictures on file tiles (PRD: attachments, previews; ADR 0013). Only the worker
calls this: the site never decodes a client's image or PDF."""

from io import BytesIO
from typing import NamedTuple

import pypdfium2 as pdfium
from PIL import Image, ImageOps

# Twice the largest tile (120 × 90), so previews stay sharp on high-density screens.
BOX = (320, 240)

# How Pillow and PDFium say a file is broken, too big to unpack, or not what it claims: the
# client's file, not our bug.
CANNOT_READ = (OSError, ValueError, SyntaxError, Image.DecompressionBombError, pdfium.PdfiumError)


class Preview(NamedTuple):
    webp: bytes
    # A PDF's page count; None for an image.
    pages: int | None


def _from_image(content: bytes) -> Image.Image:
    with Image.open(BytesIO(content)) as image:
        # A photo from a phone stores its turn in the EXIF data; the preview turns it upright.
        upright = ImageOps.exif_transpose(image)
        upright.thumbnail(BOX)
        return upright.convert("RGBA" if upright.has_transparency_data else "RGB")


def _first_page(document: pdfium.PdfDocument) -> Image.Image:
    page = document[0]
    width, height = page.get_size()
    # The tile shows the top of the page; a tall page may be twice the box's height.
    scale = min(BOX[0] / width, BOX[1] * 2 / height)
    picture: Image.Image = page.render(scale=scale).to_pil()
    return picture


def draw(content: bytes, content_type: str) -> Preview:
    """Raises one of CANNOT_READ when the file cannot be drawn."""
    pages = None
    if content_type == "application/pdf":
        document = pdfium.PdfDocument(content)
        try:
            pages = len(document)
            picture = _first_page(document)
        finally:
            document.close()
    else:
        picture = _from_image(content)
    out = BytesIO()
    picture.save(out, "WEBP", quality=80)
    return Preview(out.getvalue(), pages)
