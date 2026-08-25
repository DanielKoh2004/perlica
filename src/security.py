import ipaddress
import socket
import fnmatch
import re
import urllib.parse
from typing import Optional, List
import httpx
from src.config import settings


# Pre-compiled regex patterns for recognizable credential material
SECRET_CONTENT_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9_-]+ PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key ID
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub Personal Access Token
    re.compile(r"gho_[a-zA-Z0-9]{36}"),  # GitHub OAuth Token
    re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"),  # Slack Token
    re.compile(r"(?:api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", re.IGNORECASE),
]


def is_secret_path(file_path: str, secret_patterns: Optional[List[str]] = None) -> bool:
    """
    Check if a relative file path or filename matches secret path patterns.
    Uses configurable SECRET_PATH_PATTERNS from settings.
    """
    if secret_patterns is None:
        secret_patterns = settings.SECRET_PATH_PATTERNS

    normalized = file_path.replace("\\", "/").strip().lower()
    parts = normalized.split("/")
    filename = parts[-1]

    for pattern in secret_patterns:
        pat_lower = pattern.lower()
        if fnmatch.fnmatch(filename, pat_lower):
            return True
        if fnmatch.fnmatch(normalized, pat_lower):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat_lower):
                return True

    return False


def scan_content_for_secrets(content: str) -> bool:
    """
    Lightweight content scanner to detect recognizable private keys and credential material.
    Returns True if sensitive credential patterns are detected.
    """
    if not content:
        return False

    for pat in SECRET_CONTENT_PATTERNS:
        if pat.search(content):
            return True

    return False


class SSRFSecurityError(ValueError):
    """Raised when a URL fails SSRF safety checks."""
    pass


def validate_safe_url(url: str) -> str:
    """
    Validate that a URL is safe to fetch and does not target internal or private networks.
    Checks scheme, DNS-resolved IPv4 and IPv6 addresses, and blocked cloud metadata.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFSecurityError(f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityError("URL missing hostname.")

    hostname_clean = hostname.strip().lower()

    # Block well-known localhost aliases
    if hostname_clean in ("localhost", "0.0.0.0", "127.0.0.1", "::1", "metadata.google.internal"):
        raise SSRFSecurityError(f"Access to '{hostname_clean}' is strictly blocked for security.")

    try:
        addr_info = socket.getaddrinfo(hostname_clean, None)
    except socket.gaierror as e:
        raise SSRFSecurityError(f"Could not resolve host '{hostname_clean}': {e}")

    for item in addr_info:
        ip_str = item[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFSecurityError(f"Invalid IP address resolved: {ip_str}")

        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise SSRFSecurityError(f"Host '{hostname_clean}' resolves to private/internal IP {ip_str}, which is blocked.")

        # Explicit AWS metadata IPv4 check
        if str(ip_obj) == "169.254.169.254":
            raise SSRFSecurityError("Access to cloud metadata endpoint is blocked.")

    return url


MAX_WEB_BODY_BYTES = 2 * 1024 * 1024  # 2 MB hard body limit


async def fetch_safe_url(url: str, max_redirects: int = 3, timeout: float = 10.0) -> httpx.Response:
    """
    Fetch a remote URL safely with per-hop SSRF validation and hard body size limits.
    """
    current_url = validate_safe_url(url)
    redirects_left = max_redirects

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        while True:
            response = await client.get(
                current_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PerlicaKnowledgeBot/1.0)"}
            )

            if response.is_redirect:
                if redirects_left <= 0:
                    raise SSRFSecurityError("Too many redirects.")
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    raise SSRFSecurityError("Redirect response missing Location header.")

                # Resolve relative redirects
                next_url = urllib.parse.urljoin(current_url, redirect_url)
                current_url = validate_safe_url(next_url)
                redirects_left -= 1
                continue

            response.raise_for_status()

            # Enforce hard response body size limit
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_WEB_BODY_BYTES:
                raise ValueError(f"Webpage response exceeds size limit ({int(content_length)} > {MAX_WEB_BODY_BYTES} bytes).")

            if len(response.content) > MAX_WEB_BODY_BYTES:
                raise ValueError(f"Webpage content exceeds size limit ({len(response.content)} > {MAX_WEB_BODY_BYTES} bytes).")

            return response


def is_user_authorized_for_copilot(user_id: int) -> bool:
    """
    Check if a Discord user is authorized to use the Knowledge Copilot.
    Fails closed: requires at least one configured authorized ID in ALLOWED_DISCORD_USERS or ALLOWED_USER_ID.
    """
    if settings.ALLOWED_DISCORD_USERS:
        return user_id in settings.ALLOWED_DISCORD_USERS
    if settings.ALLOWED_USER_ID is not None:
        return user_id == settings.ALLOWED_USER_ID
    return False
