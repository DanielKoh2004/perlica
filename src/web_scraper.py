import re
import urllib.parse
from typing import List, Dict, Any, Optional
import trafilatura
from src.security import fetch_safe_url, scan_content_for_secrets


async def scrape_webpage(url: str) -> List[Dict[str, Any]]:
    """
    Safely scrape a public web page with SSRF protection and extract article text via trafilatura.
    """
    response = await fetch_safe_url(url, max_redirects=3, timeout=12.0)
    html_content = response.text

    extracted_text = trafilatura.extract(
        html_content,
        include_links=True,
        include_tables=True,
        include_images=False,
        output_format="txt",
    )

    if not extracted_text:
        # Fallback to basic tag stripping if trafilatura finds no main article
        clean = re.sub(r'<[^>]+>', ' ', html_content)
        extracted_text = re.sub(r'\s+', ' ', clean).strip()

    if not extracted_text or scan_content_for_secrets(extracted_text):
        return []

    parsed_url = urllib.parse.urlparse(url)
    page_title = parsed_url.path.strip("/").split("/")[-1] or parsed_url.netloc

    # Chunk long articles by double newlines
    paragraphs = [p.strip() for p in extracted_text.split("\n\n") if len(p.strip()) > 40]
    if not paragraphs:
        paragraphs = [extracted_text]

    chunks: List[Dict[str, Any]] = []
    for i, p in enumerate(paragraphs):
        chunks.append({
            "section_title": f"{page_title} (Part {i+1})",
            "permalink_url": url,
            "content": p,
            "metadata": {
                "url": url,
                "domain": parsed_url.netloc,
                "part": i + 1,
                "file_type": "WEB",
            }
        })

    return chunks
