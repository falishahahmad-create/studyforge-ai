"""Upload validation and text extraction."""
import io
import os
import re

from pypdf import PdfReader

MAX_MB = 10
MAX_PAGES = 300
ALLOWED = {".pdf", ".txt", ".md"}
CHUNK_CHARS = 3000


class ParseError(Exception):
    pass


def _clean(text):
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_text(text):
    """Split plain text into numbered sections (these are NOT real page numbers)."""
    text = _clean(text)
    if len(text) < 20:
        raise ParseError("The text is too short to study from.")
    sections, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) > CHUNK_CHARS and buf:
            sections.append(buf.strip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        sections.append(buf.strip())
    return [(i, s) for i, s in enumerate(sections, 1)], False


def parse_upload(name, data):
    """Returns ([(page_or_section_no, text)], has_real_pages). Raises ParseError."""
    ext = os.path.splitext(name.lower())[1]
    if ext not in ALLOWED:
        raise ParseError("Unsupported file type. Use PDF, TXT or MD.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise ParseError(f"File is larger than {MAX_MB} MB.")
    if not data:
        raise ParseError("File is empty.")
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ParseError("This file does not look like a real PDF.")
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ParseError("The PDF is password-protected.")
            if len(reader.pages) > MAX_PAGES:
                raise ParseError(f"PDF has more than {MAX_PAGES} pages. Split it first.")
            pages = []
            for i, page in enumerate(reader.pages, 1):
                t = _clean(page.extract_text() or "")
                if t:
                    pages.append((i, t))
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"Could not read the PDF ({type(e).__name__}).")
        if not pages:
            raise ParseError("No selectable text found. Scanned PDFs need OCR first.")
        return pages, True
    return parse_text(data.decode("utf-8", errors="ignore"))
