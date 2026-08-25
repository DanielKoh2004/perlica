import os
import re
import asyncio
from typing import List, Dict, Any, Optional
from pypdf import PdfReader
from src.security import scan_content_for_secrets

MAX_PDF_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB limit
MAX_PDF_PAGES = 50                     # 50 pages max


def _extract_pdf_sync(file_path: str) -> List[Dict[str, Any]]:
    """Synchronous CPU worker to extract page-level text from PDF."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size > MAX_PDF_SIZE_BYTES:
        raise ValueError(f"PDF exceeds size limit ({file_size} > {MAX_PDF_SIZE_BYTES} bytes).")

    reader = PdfReader(file_path)
    num_pages = len(reader.pages)
    if num_pages > MAX_PDF_PAGES:
        num_pages = MAX_PDF_PAGES

    filename = os.path.basename(file_path)
    chunks: List[Dict[str, Any]] = []

    for i in range(num_pages):
        page = reader.pages[i]
        text = page.extract_text() or ""
        text = text.strip()
        if not text:
            continue

        if scan_content_for_secrets(text):
            continue

        page_num = i + 1
        clause_match = re.search(r'(?:Clause|Section|Article)\s*([0-9]+(?:\.[0-9]+)*)', text, re.IGNORECASE)
        clause_str = clause_match.group(0) if clause_match else None

        if clause_str:
            section_title = f"{filename} > Page {page_num} > {clause_str}"
        else:
            section_title = f"{filename} > Page {page_num}"

        chunks.append({
            "section_title": section_title,
            "permalink_url": f"pdf:{filename}#page={page_num}",
            "content": text,
            "metadata": {
                "filename": filename,
                "page": page_num,
                "clause": clause_str,
                "file_type": "PDF",
            }
        })

    return chunks


async def parse_pdf_file(file_path: str) -> List[Dict[str, Any]]:
    """Extract page-aware chunks from a PDF file using a thread pool."""
    return await asyncio.to_thread(_extract_pdf_sync, file_path)
