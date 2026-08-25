import pytest
import aiosqlite
from unittest.mock import AsyncMock, patch
from src.database import DatabaseManager
from src.github_sync import chunk_python_code
from src.rag_engine import (
    chunk_markdown_text,
    compute_embedding,
    cosine_similarity_matrix,
    hybrid_retrieve,
    synthesize_copilot_answer,
)


def test_python_ast_chunking():
    """Verify that Python functions with docstrings and line numbers are extracted cleanly."""
    sample_code = '''"""Module docstring."""
import os
from datetime import datetime

def calculate_fuel_details(amount: float) -> float:
    """Calculate fuel volume from total expenditure."""
    price = 1.99
    return amount / price

class FuelTracker:
    """Class docstring."""
    def log(self):
        pass
'''
    chunks = chunk_python_code(
        code_str=sample_code,
        repo_name="DanielKoh2004/perlica",
        file_path="src/fuel.py",
        commit_sha="abcd1234ef",
    )

    assert len(chunks) == 2  # 1 function, 1 class
    fn_chunk = chunks[0]
    assert fn_chunk["metadata"]["symbol"] == "calculate_fuel_details"
    assert "Calculate fuel volume" in fn_chunk["metadata"]["docstring"]
    assert "os" in fn_chunk["metadata"]["imports"]
    assert "datetime.datetime" in fn_chunk["metadata"]["imports"]
    assert "#L" in fn_chunk["permalink_url"]


def test_markdown_header_chunking():
    """Verify that markdown header hierarchy creates breadcrumb chunks."""
    md = """# Architecture Guide
Welcome to Perlica architecture.

## Database Layer
We use SQLite with WAL mode.

### FTS5 Indexing
FTS5 provides fast lexical search with BM25.
"""
    chunks = chunk_markdown_text(md, "PerlicaDocs", "docs/arch.md")
    assert len(chunks) == 3
    assert "docs/arch.md > Architecture Guide" in chunks[0]["section_title"]
    assert "docs/arch.md > Architecture Guide > Database Layer" in chunks[1]["section_title"]
    assert "docs/arch.md > Architecture Guide > Database Layer > FTS5 Indexing" in chunks[2]["section_title"]


@pytest.mark.asyncio
async def test_symbol_tokenization_and_fts_retrieval(tmp_path):
    """Verify FTS5 tokenizes symbols containing _ and prose with punctuation."""
    db = DatabaseManager(db_path=str(tmp_path / "test_rag.db"))
    await db.init_db()

    source_id = await db.get_or_create_source("Perlica", "GITHUB", "github:DanielKoh2004/perlica")
    await db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/extractor.py",
        blob_sha="sha_ext",
        sync_id=1,
        chunks=[
            {
                "section_title": "Fuel Helper",
                "content": "def calculate_fuel_details(amount): return amount / 1.99",
                "metadata": {"symbol": "calculate_fuel_details"},
            },
            {
                "section_title": "Config Info",
                "content": "We require GROQ_API_KEY for LLM synthesis. Built with SQLite.",
                "metadata": {"symbol": "GROQ_API_KEY"},
            },
        ],
        embeddings=[],
    )

    # 1. Exact underscore symbol
    res_symbol = await db.fts_search_knowledge("calculate_fuel_details", source_id=source_id)
    assert len(res_symbol) == 1
    assert "calculate_fuel_details" in res_symbol[0]["content"]

    # 2. Config symbol
    res_key = await db.fts_search_knowledge("GROQ_API_KEY", source_id=source_id)
    assert len(res_key) == 1
    assert "GROQ_API_KEY" in res_key[0]["content"]

    # 3. Prose with punctuation
    res_prose = await db.fts_search_knowledge("SQLite", source_id=source_id)
    assert len(res_prose) == 1


@pytest.mark.asyncio
async def test_zero_result_abstention_never_calls_llm(tmp_path):
    """
    CRITICAL INVARIANT (Invariants 11 & 13):
    Zero-result queries MUST NOT invoke the LLM.
    """
    db = DatabaseManager(db_path=str(tmp_path / "test_abstain.db"))
    await db.init_db()

    with patch("src.rag_engine.AsyncGroq") as mock_groq_class:
        mock_groq_instance = mock_groq_class.return_value

        res = await synthesize_copilot_answer(
            db=db,
            query="What is the quantum encryption protocol in Perlica?",
        )

        assert res["status"] == "ABSTAINED"
        assert "could not find any relevant information" in res["response"]
        # Invariant check: Groq client was NEVER instantiated/called
        mock_groq_instance.chat.completions.create.assert_not_called()
