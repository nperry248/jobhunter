"""
services/resume_parser.py — Utilities for extracting text from resumes and job descriptions.

WHY THIS IS A SERVICE (NOT AN AGENT):
  Agents have a run() entry point, talk to the DB, and are scheduled by the orchestrator.
  Services are plain utility modules — pure functions with no side effects.
  This module just takes a file path or string and returns a string. No DB, no queue.
  That makes it independently testable and reusable from any agent.

DEPENDENCIES:
  - pdfminer.six: Pure-Python PDF text extractor. No binary system deps (unlike pdftotext).
    Install: already in requirements.txt as `pdfminer.six`
  - re (stdlib): Built-in regex module for stripping HTML tags.
"""

import re
from io import StringIO
from pathlib import Path

from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams


def parse_pdf(path: str | Path) -> str:
    """
    Extract all text from a PDF file and return it as a single string.

    WHY pdfminer.six:
      - Pure Python — no system binaries (poppler, ghostscript) required.
      - Handles multi-column layouts better than simpler tools.
      - Widely used in production resume parsing pipelines.

    HOW IT WORKS:
      pdfminer renders each page into a layout tree of text boxes, then
      extract_text_to_fp() serialises those boxes into a stream of characters,
      preserving reading order as best it can.

    Args:
        path: Absolute or relative path to the PDF file.

    Returns:
        Extracted text as a single string. Newlines separate paragraphs/lines.
        Returns an empty string if the file has no extractable text (e.g. scanned image PDF).

    Raises:
        FileNotFoundError: If the path does not exist.
        pdfminer.pdfexceptions.PDFSyntaxError: If the file is not a valid PDF.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Resume PDF not found: {path}")

    # LAParams (Layout Analysis Parameters) controls how pdfminer groups characters
    # into words and lines. The defaults work well for standard resume layouts.
    # char_margin: max gap (as fraction of char width) before a new word starts.
    # line_margin: max gap (as fraction of line height) before a new paragraph starts.
    laparams = LAParams(char_margin=2.0, line_margin=0.5)

    output = StringIO()

    with open(path, "rb") as pdf_file:
        # extract_text_to_fp writes extracted text into our StringIO buffer.
        # CONCEPT — StringIO: an in-memory file object. We use it so pdfminer
        # can "write" to it without creating a real file on disk.
        extract_text_to_fp(pdf_file, output, laparams=laparams)

    text = output.getvalue()

    # Collapse 3+ consecutive newlines into 2 — keeps paragraph breaks without
    # leaving huge blank gaps from PDF header/footer whitespace.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def strip_html(text: str) -> str:
    """
    Remove HTML tags from a string and decode common HTML entities.

    WHY THIS IS NEEDED:
      Job descriptions from Greenhouse and Lever APIs come back as raw HTML strings:
        "<p>We are looking for a <strong>backend engineer</strong>...</p>
         <ul><li>Python experience</li></ul>"
      If we send that HTML to Claude, it wastes tokens on tags and confuses the model.
      Clean prose scores better and costs less.

    APPROACH:
      We use two regex passes:
        1. Replace block-level tags (<p>, <li>, <br>, <div>) with newlines so we
           preserve paragraph structure as whitespace.
        2. Strip all remaining tags.
      Then decode the most common HTML entities (&amp; &lt; &gt; &nbsp;).

    NOTE: This is NOT a full HTML sanitizer. We're not worried about security here
    (we're not rendering this in a browser), just extracting readable text.

    Args:
        text: A string that may contain HTML markup.

    Returns:
        Plain text with tags removed and entities decoded.
    """
    if not text:
        return ""

    # Step 1: Convert block-level tags to newlines to preserve paragraph structure.
    # These tags act as line/paragraph separators in rendered HTML.
    text = re.sub(r"<(?:p|div|br|li|h[1-6]|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)

    # Step 2: Strip all remaining HTML tags (inline tags like <b>, <em>, <a>, etc.)
    text = re.sub(r"<[^>]+>", "", text)

    # Step 3: Decode the most common HTML entities.
    # NOTE: html.unescape() handles ALL entities, but importing the stdlib html
    # module just for that feels like overkill given we only need 5 entities.
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&nbsp;": " ",
        "&quot;": '"',
        "&#39;": "'",
    }
    for entity, char in replacements.items():
        text = text.replace(entity, char)

    # Step 4: Collapse excessive whitespace / blank lines.
    text = re.sub(r" {2,}", " ", text)           # multiple spaces → one space
    text = re.sub(r"\n{3,}", "\n\n", text)        # 3+ newlines → double newline

    return text.strip()
