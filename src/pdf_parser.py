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
        raise ValueError(f"PDF exceeds page limit ({num_pages} > {MAX_PDF_PAGES} pages). Please split or provide a smaller document.")

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
            base_section_title = f"{filename} > Page {page_num} > {clause_str}"
        else:
            base_section_title = f"{filename} > Page {page_num}"

        # Sub-chunk dense pages (> 1500 chars) to ensure complete dense embedding fidelity
        if len(text) <= 1500:
            chunks.append({
                "section_title": base_section_title,
                "permalink_url": f"pdf:{filename}#page={page_num}",
                "content": text,
                "metadata": {
                    "filename": filename,
                    "page": page_num,
                    "clause": clause_str,
                    "file_type": "PDF",
                }
            })
        else:
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            current_sub: List[str] = []
            curr_len = 0
            part_idx = 1
            for p in paragraphs:
                p_len = len(p) + 2
                if curr_len + p_len > 1200 and current_sub:
                    sub_text = "\n\n".join(current_sub).strip()
                    chunks.append({
                        "section_title": f"{base_section_title} (Part {part_idx})",
                        "permalink_url": f"pdf:{filename}#page={page_num}",
                        "content": sub_text,
                        "metadata": {
                            "filename": filename,
                            "page": page_num,
                            "clause": clause_str,
                            "part": part_idx,
                            "file_type": "PDF",
                        }
                    })
                    part_idx += 1
                    current_sub = [p]
                    curr_len = p_len
                else:
                    current_sub.append(p)
                    curr_len += p_len

            if current_sub:
                sub_text = "\n\n".join(current_sub).strip()
                chunks.append({
                    "section_title": f"{base_section_title} (Part {part_idx})",
                    "permalink_url": f"pdf:{filename}#page={page_num}",
                    "content": sub_text,
                    "metadata": {
                        "filename": filename,
                        "page": page_num,
                        "clause": clause_str,
                        "part": part_idx,
                        "file_type": "PDF",
                    }
                })

    return chunks


async def parse_pdf_file(file_path: str) -> List[Dict[str, Any]]:
    """Extract page-aware chunks from a PDF file using a thread pool."""
    return await asyncio.to_thread(_extract_pdf_sync, file_path)
