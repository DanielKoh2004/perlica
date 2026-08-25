import os
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from src.database import DatabaseManager
from src.github_sync import chunk_python_code
from src.rag_engine import (
    synthesize_copilot_answer,
    compute_embeddings_batch,
    chunk_markdown_text,
    MODEL_ID,
)


@pytest.fixture
async def real_codebase_db(tmp_path):
    """Seed test database with real codebase files from the Perlica repository."""
    db_file = str(tmp_path / "test_real_smoke.db")
    db = DatabaseManager(db_path=db_file)
    await db.init_db()

    repo_name = "DanielKoh2004/perlica"
    commit_sha = "31436a88b59adfc83e390c5fa63198089c20f124"
    source_ref = f"github:{repo_name}"

    source_id = await db.get_or_create_source(
        name="Perlica",
        source_type="GITHUB",
        source_ref=source_ref,
        eligible_count=4,
    )

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_files = [
        "src/database.py",
        "src/github_sync.py",
        "src/rag_engine.py",
        "README.md",
    ]

    for rel_path in target_files:
        full_path = os.path.join(base_dir, rel_path)
        if not os.path.exists(full_path):
            continue

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        if rel_path.endswith(".py"):
            chunks = chunk_python_code(content, repo_name, rel_path, commit_sha)
        else:
            chunks = chunk_markdown_text(content, rel_path, f"https://github.com/{repo_name}/blob/{commit_sha}/{rel_path}")

        if not chunks:
            continue

        texts = [c["content"] for c in chunks]
        embs = await asyncio.to_thread(compute_embeddings_batch, texts)
        emb_tuples = [(MODEL_ID, e) for e in embs]

        await db.commit_file_reconciliation(
            source_id=source_id,
            file_path=rel_path,
            blob_sha=f"sha_{rel_path}",
            sync_id=1,
            chunks=chunks,
            embeddings=emb_tuples,
        )

    await db.update_source_status(source_id, eligible_count=len(target_files), indexed_count=len(target_files), status="COMPLETE")
    return db


@pytest.mark.asyncio
async def test_real_corpus_smoke_queries(real_codebase_db):
    """
    Real-corpus smoke test verifying retrieval against actual Perlica production codebase files.
    """
    db = real_codebase_db

    async def mock_groq_create(*args, **kwargs):
        mock_choice = MagicMock()
        mock_choice.message.content = "Grounded response based on actual Perlica codebase."
        mock_res = MagicMock()
        mock_res.choices = [mock_choice]
        return mock_res

    with patch("src.rag_engine.AsyncGroq") as mock_groq_cls:
        mock_groq_instance = mock_groq_cls.return_value
        mock_groq_instance.chat.completions.create = AsyncMock(side_effect=mock_groq_create)

        # 1. DatabaseManager & PRAGMAs
        q1 = await synthesize_copilot_answer(db, "Where is DatabaseManager defined and what PRAGMA statements are set?")
        assert q1["status"] == "SUCCESS"
        assert len(q1["evidence"]) > 0
        assert any("class DatabaseManager" in c["content"] or "PRAGMA foreign_keys" in c["content"] for c in q1["evidence"])
        assert any("src/database.py" in c.get("permalink_url", "") for c in q1["evidence"])

        # 2. GitHub Manifest Reconciliation & Deleted Files
        q2 = await synthesize_copilot_answer(db, "How does manifest reconciliation purge unseen deleted files?")
        assert q2["status"] == "SUCCESS"
        assert len(q2["evidence"]) > 0
        assert any("purge_unseen_source_files" in c["content"] or "last_seen_sync_id" in c["content"] for c in q2["evidence"])

        # 3. Hybrid RRF Retrieval
        q3 = await synthesize_copilot_answer(db, "How does hybrid_retrieve combine BM25 and FastEmbed embeddings?")
        assert q3["status"] == "SUCCESS"
        assert len(q3["evidence"]) > 0
        assert any("hybrid_retrieve" in c["content"] or "rrf_scores" in c["content"] for c in q3["evidence"])
        assert any("src/rag_engine.py" in c.get("permalink_url", "") for c in q3["evidence"])
