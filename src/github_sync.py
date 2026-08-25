import ast
import urllib.parse
import re
import base64
from typing import List, Dict, Any, Optional, Tuple
import httpx
from src.config import settings
from src.security import is_secret_path, scan_content_for_secrets
from src.database import DatabaseManager

MAX_FILE_SIZE_BYTES = 500 * 1024  # 500 KB limit
MAX_REPO_FILES = 250               # 250 files cap

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".pyc", ".pyo", ".pyd", ".wasm",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
}

IGNORED_DIRECTORIES = {
    "node_modules", "venv", ".venv", "env", ".env",
    "dist", "build", ".git", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode",
}

IGNORED_LOCKFILES = {
    "package-lock.json", "yarn.lock", "poetry.lock",
    "pnpm-lock.yaml", "Pipfile.lock", "composer.lock",
}


def is_eligible_repo_file(path: str, size: Optional[int] = None) -> Tuple[bool, str]:
    """
    Check if a repository file is eligible for indexing.
    Returns: (is_eligible, reason_if_excluded)
    """
    normalized = path.replace("\\", "/").strip()
    parts = normalized.split("/")
    filename = parts[-1]

    # Directory check
    for part in parts[:-1]:
        if part.lower() in IGNORED_DIRECTORIES:
            return False, "EXCLUDED_DIR"

    # Lockfile check
    if filename.lower() in IGNORED_LOCKFILES:
        return False, "EXCLUDED_LOCKFILE"

    # Secret path check
    if is_secret_path(normalized):
        return False, "EXCLUDED_SECRET"

    # Binary check
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in BINARY_EXTENSIONS:
        return False, "EXCLUDED_BINARY"

    # Size check
    if size is not None and size > MAX_FILE_SIZE_BYTES:
        return False, "EXCLUDED_SIZE"

    return True, "ELIGIBLE"


def chunk_python_code(
    code_str: str,
    repo_name: str,
    file_path: str,
    commit_sha: str,
) -> List[Dict[str, Any]]:
    """
    Language-aware structural chunking for Python using the standard library ast module.
    Extracts classes, methods, functions with docstrings, line numbers, and imports.
    Falls back to line chunking if AST parsing fails.
    """
    encoded_path = urllib.parse.quote(file_path.replace("\\", "/"))
    base_url = f"https://github.com/{repo_name}/blob/{commit_sha}/{encoded_path}"

    try:
        tree = ast.parse(code_str)
    except SyntaxError:
        return chunk_generic_code(code_str, repo_name, file_path, commit_sha)

    lines = code_str.splitlines()
    chunks: List[Dict[str, Any]] = []

    # Collect module-level imports
    imports: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}")

    def get_source_segment(start_line: int, end_line: int) -> str:
        return "\n".join(lines[start_line - 1 : end_line])

    # Extract top-level classes and functions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start_l = node.lineno
            end_l = getattr(node, "end_lineno", start_l + 10)
            docstring = ast.get_docstring(node) or ""
            fn_code = get_source_segment(start_l, end_l)
            chunks.append({
                "section_title": f"{file_path} > def {node.name}()",
                "permalink_url": f"{base_url}#L{start_l}-L{end_l}",
                "content": fn_code,
                "metadata": {
                    "repo": repo_name,
                    "file": file_path,
                    "symbol": node.name,
                    "symbol_type": "function",
                    "docstring": docstring,
                    "line_range": f"{start_l}-{end_l}",
                    "imports": imports[:10],
                    "commit_sha": commit_sha,
                }
            })
        elif isinstance(node, ast.ClassDef):
            cls_start = node.lineno
            cls_doc = ast.get_docstring(node) or ""
            # Header chunk for the class itself
            first_method_line = next((m.lineno for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))), getattr(node, "end_lineno", cls_start + 15))
            cls_end = max(cls_start + 3, first_method_line - 1)
            cls_code = get_source_segment(cls_start, cls_end)
            chunks.append({
                "section_title": f"{file_path} > class {node.name}",
                "permalink_url": f"{base_url}#L{cls_start}-L{cls_end}",
                "content": cls_code,
                "metadata": {
                    "repo": repo_name,
                    "file": file_path,
                    "symbol": node.name,
                    "symbol_type": "class",
                    "docstring": cls_doc,
                    "line_range": f"{cls_start}-{cls_end}",
                    "imports": imports[:10],
                    "commit_sha": commit_sha,
                }
            })

            # Extract each method inside the class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_start = item.lineno
                    m_end = getattr(item, "end_lineno", m_start + 10)
                    m_doc = ast.get_docstring(item) or ""
                    m_code = get_source_segment(m_start, m_end)
                    chunks.append({
                        "section_title": f"{file_path} > {node.name}.{item.name}()",
                        "permalink_url": f"{base_url}#L{m_start}-L{m_end}",
                        "content": m_code,
                        "metadata": {
                            "repo": repo_name,
                            "file": file_path,
                            "symbol": f"{node.name}.{item.name}",
                            "symbol_type": "method",
                            "docstring": m_doc,
                            "line_range": f"{m_start}-{m_end}",
                            "imports": imports[:10],
                            "commit_sha": commit_sha,
                        }
                    })

    # If no functions or classes were found (e.g. script/config), fallback to generic chunking
    if not chunks:
        return chunk_generic_code(code_str, repo_name, file_path, commit_sha)

    return chunks


def chunk_generic_code(
    code_str: str,
    repo_name: str,
    file_path: str,
    commit_sha: str,
    chunk_lines: int = 60,
    overlap_lines: int = 10,
) -> List[Dict[str, Any]]:
    """
    Fallback structural sliding window chunker for non-Python or script files.
    """
    encoded_path = urllib.parse.quote(file_path.replace("\\", "/"))
    base_url = f"https://github.com/{repo_name}/blob/{commit_sha}/{encoded_path}"

    lines = code_str.splitlines()
    if not lines:
        return []

    chunks: List[Dict[str, Any]] = []
    total_lines = len(lines)

    step = max(1, chunk_lines - overlap_lines)
    for i in range(0, total_lines, step):
        chunk_slice = lines[i : i + chunk_lines]
        start_l = i + 1
        end_l = min(total_lines, i + len(chunk_slice))
        chunk_text = "\n".join(chunk_slice).strip()
        if not chunk_text:
            continue

        chunks.append({
            "section_title": f"{file_path} (L{start_l}-L{end_l})",
            "permalink_url": f"{base_url}#L{start_l}-L{end_l}",
            "content": chunk_text,
            "metadata": {
                "repo": repo_name,
                "file": file_path,
                "line_range": f"{start_l}-{end_l}",
                "commit_sha": commit_sha,
            }
        })

    return chunks


def format_github_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Format sanitized HTTP headers for GitHub REST API authentication."""
    headers = {
        "User-Agent": "Perlica-Knowledge-Copilot",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    raw_token = token or settings.GITHUB_TOKEN
    if raw_token:
        auth_token = raw_token.strip().strip("'\"")
        if auth_token:
            if auth_token.startswith("Bearer ") or auth_token.startswith("token "):
                headers["Authorization"] = auth_token
            elif auth_token.startswith("ghp_") or auth_token.startswith("github_pat_"):
                headers["Authorization"] = f"token {auth_token}"
            else:
                headers["Authorization"] = f"Bearer {auth_token}"
    return headers


async def fetch_github_repo_tree(
    repo_name: str,
    branch: str = "main",
    token: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    Fetch the exact commit SHA and the recursive Git tree for a repository.
    1. Resolves branch/ref to the exact immutable commit SHA via the Commits API.
    2. Fetches the recursive tree using the commit SHA.
    Returns: (commit_sha, tree_entries, is_truncated)
    """
    headers = format_github_headers(token)
    auth_configured = "Authorization" in headers

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Resolve branch to exact Commit SHA
        commit_api_url = f"https://api.github.com/repos/{repo_name}/commits/{branch}"
        commit_resp = await client.get(commit_api_url, headers=headers)

        target_branch = branch
        if commit_resp.status_code == 404 and branch == "main":
            # Fallback to master branch
            master_url = f"https://api.github.com/repos/{repo_name}/commits/master"
            master_resp = await client.get(master_url, headers=headers)
            if master_resp.status_code == 200:
                commit_resp = master_resp
                target_branch = "master"

        if commit_resp.status_code == 401:
            raise RuntimeError(
                "GitHub authentication failed (401 Unauthorized). "
                "Please verify that your GITHUB_TOKEN is valid and active."
            )
        elif commit_resp.status_code == 404:
            if not auth_configured:
                raise RuntimeError(
                    f"Repository '{repo_name}' not found (404). "
                    "If this is a private repository, please add your 'GITHUB_TOKEN' (with 'repo' scope) to your environment/.env file."
                )
            else:
                raise RuntimeError(
                    f"Repository '{repo_name}' (branch '{branch}') not found (404). "
                    "Please verify that the repository name and branch exist, and that your GITHUB_TOKEN has access permissions to this private repository."
                )

        commit_resp.raise_for_status()
        commit_data = commit_resp.json()
        commit_sha = commit_data.get("sha", target_branch)

        # Step 2: Fetch recursive Git tree using commit_sha
        tree_api_url = f"https://api.github.com/repos/{repo_name}/git/trees/{commit_sha}?recursive=1"
        tree_resp = await client.get(tree_api_url, headers=headers)
        tree_resp.raise_for_status()
        tree_data = tree_resp.json()
        tree = tree_data.get("tree", [])
        is_truncated = bool(tree_data.get("truncated", False))

        return commit_sha, tree, is_truncated


async def fetch_github_blob_content(
    repo_name: str,
    file_path: str,
    ref: str,
    token: Optional[str] = None,
) -> Optional[str]:
    """
    Fetch raw blob content for a specific file in a GitHub repository.
    """
    headers = format_github_headers(token)

    encoded_path = urllib.parse.quote(file_path.replace("\\", "/"))
    api_url = f"https://api.github.com/repos/{repo_name}/contents/{encoded_path}?ref={ref}"

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(api_url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content_b64 = data.get("content", "")
        if content_b64:
            try:
                return base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:
                return None
        return None
