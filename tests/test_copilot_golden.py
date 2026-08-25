import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from src.database import DatabaseManager
from src.rag_engine import (
    synthesize_copilot_answer,
    compute_embedding,
    MODEL_ID,
)


@pytest.fixture
async def seeded_copilot_db(tmp_path):
    """Seed test database with mock GitHub code, PDF agreement, and notes."""
    db_file = str(tmp_path / "test_golden.db")
    db = DatabaseManager(db_path=db_file)
    await db.init_db()

    # 1. Seed GitHub Code Source
    repo_src_id = await db.get_or_create_source("Perlica", "GITHUB", "github:DanielKoh2004/perlica", eligible_count=10)
    
    fuel_code = (
        "def calculate_fuel_details(amount: float) -> float:\n"
        "    \"\"\"Calculate fuel volume in liters based on official subsidized rate of RM 1.99/L.\"\"\"\n"
        "    return round(amount / 1.99, 2)\n"
    )
    fuel_emb = await asyncio.to_thread(compute_embedding, fuel_code)
    await db.commit_file_reconciliation(
        source_id=repo_src_id,
        file_path="src/fuel.py",
        blob_sha="sha_fuel_1",
        sync_id=1,
        chunks=[{
            "section_title": "src/fuel.py > def calculate_fuel_details()",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha_fuel_1/src/fuel.py#L1-L3",
            "content": fuel_code,
            "metadata": {"symbol": "calculate_fuel_details", "line_range": "1-3"},
        }],
        embeddings=[(MODEL_ID, fuel_emb)],
    )

    auth_code = (
        "def init_groq_client():\n"
        "    \"\"\"Initialize Groq Async client using GROQ_API_KEY environment variable.\"\"\"\n"
        "    return AsyncGroq(api_key=settings.GROQ_API_KEY)\n"
    )
    auth_emb = await asyncio.to_thread(compute_embedding, auth_code)
    await db.commit_file_reconciliation(
        source_id=repo_src_id,
        file_path="src/config.py",
        blob_sha="sha_cfg_1",
        sync_id=1,
        chunks=[{
            "section_title": "src/config.py > def init_groq_client()",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha_cfg_1/src/config.py#L1-L3",
            "content": auth_code,
            "metadata": {"symbol": "GROQ_API_KEY", "line_range": "1-3"},
        }],
        embeddings=[(MODEL_ID, auth_emb)],
    )

    norm_code = (
        "def normalize_canonical_asset(raw_name: str) -> tuple[str, str]:\n"
        "    \"\"\"Deterministically map tickers to (Canonical Name, Asset Class) tuple.\"\"\"\n"
        "    return (raw_name.upper(), 'Equities')\n"
    )
    norm_emb = await asyncio.to_thread(compute_embedding, norm_code)
    await db.commit_file_reconciliation(
        source_id=repo_src_id,
        file_path="src/database.py",
        blob_sha="sha_db_1",
        sync_id=1,
        chunks=[{
            "section_title": "src/database.py > def normalize_canonical_asset()",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha_db_1/src/database.py#L1-L3",
            "content": norm_code,
            "metadata": {"symbol": "normalize_canonical_asset", "line_range": "1-3"},
        }],
        embeddings=[(MODEL_ID, norm_emb)],
    )

    dup_code = (
        "def find_recent_similar_expense(amount: float, category: str):\n"
        "    \"\"\"Double-Tap Protection: Search for identical transactions in the past 5 minutes.\"\"\"\n"
        "    return db.query_recent_collision(amount, category)\n"
    )
    dup_emb = await asyncio.to_thread(compute_embedding, dup_code)
    await db.commit_file_reconciliation(
        source_id=repo_src_id,
        file_path="src/duplicate_guard.py",
        blob_sha="sha_db_2",
        sync_id=1,
        chunks=[{
            "section_title": "src/duplicate_guard.py > def find_recent_similar_expense()",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha_db_2/src/duplicate_guard.py#L10-L15",
            "content": dup_code,
            "metadata": {"symbol": "find_recent_similar_expense", "line_range": "10-15"},
        }],
        embeddings=[(MODEL_ID, dup_emb)],
    )

    reconcile_doc = (
        "Manifest-based incremental reconciliation: files are assigned last_seen_sync_id = sync_id.\n"
        "After processing, any row whose last_seen_sync_id != sync_id is considered deleted and purged."
    )
    rec_emb = await asyncio.to_thread(compute_embedding, reconcile_doc)
    await db.commit_file_reconciliation(
        source_id=repo_src_id,
        file_path="docs/architecture.md",
        blob_sha="sha_doc_1",
        sync_id=1,
        chunks=[{
            "section_title": "docs/architecture.md > Reconciliation",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha_doc_1/docs/architecture.md",
            "content": reconcile_doc,
            "metadata": {"topic": "reconciliation"},
        }],
        embeddings=[(MODEL_ID, rec_emb)],
    )

    # Mark Perlica status as PARTIAL (5/10 files indexed)
    await db.update_source_status(repo_src_id, eligible_count=10, indexed_count=5, status="PARTIAL")

    # 2. Seed PDF Tenancy & Policy Source
    pdf_src_id = await db.get_or_create_source("Tenancy Agreement", "PDF", "pdf:tenancy_2026.pdf", eligible_count=1)
    tenancy_clause = (
        "Clause 8.1 Termination Notice: Either party may terminate this tenancy by providing "
        "at least two (2) full months prior written notice or payment of rent in lieu thereof."
    )
    tenancy_emb = await asyncio.to_thread(compute_embedding, tenancy_clause)
    await db.commit_file_reconciliation(
        source_id=pdf_src_id,
        file_path="tenancy_2026.pdf",
        blob_sha="sha_pdf_1",
        sync_id=1,
        chunks=[{
            "section_title": "tenancy_2026.pdf > Page 4 > Clause 8.1",
            "permalink_url": "pdf:tenancy_2026.pdf#page=4",
            "content": tenancy_clause,
            "metadata": {"page": 4, "clause": "Clause 8.1"},
        }],
        embeddings=[(MODEL_ID, tenancy_emb)],
    )

    subsidy_clause = (
        "RON95 Subsidy Policy: Malaysian citizens are entitled to an allowance of 200 liters of RON95 "
        "subsidized fuel per month priced at RM 1.99/L. Unused quotas do not roll over."
    )
    sub_emb = await asyncio.to_thread(compute_embedding, subsidy_clause)
    await db.commit_file_reconciliation(
        source_id=pdf_src_id,
        file_path="fuel_policy.pdf",
        blob_sha="sha_pdf_2",
        sync_id=1,
        chunks=[{
            "section_title": "fuel_policy.pdf > Page 2",
            "permalink_url": "pdf:fuel_policy.pdf#page=2",
            "content": subsidy_clause,
            "metadata": {"page": 2},
        }],
        embeddings=[(MODEL_ID, sub_emb)],
    )
    await db.update_source_status(pdf_src_id, eligible_count=2, indexed_count=2, status="COMPLETE")

    return db


@pytest.mark.asyncio
async def test_10_golden_queries_benchmark(seeded_copilot_db):
    """
    Execute the structured 10 Golden Retrieval Queries benchmark:
    - 3 x exact symbol/code queries
    - 2 x conceptual document queries
    - 2 x cross-section/context queries
    - 2 x unanswerable queries
    - 1 x partial-index query
    """
    db = seeded_copilot_db

    # Mock Groq synthesis to return evidence-grounded responses
    async def mock_groq_create(*args, **kwargs):
        messages = kwargs.get("messages", [])
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        mock_choice = MagicMock()
        mock_choice.message.content = f"Synthesized answer grounded in evidence: {user_msg[:100]}"
        mock_res = MagicMock()
        mock_res.choices = [mock_choice]
        return mock_res

    with patch("src.rag_engine.AsyncGroq") as mock_groq_cls:
        mock_groq_instance = mock_groq_cls.return_value
        mock_groq_instance.chat.completions.create = AsyncMock(side_effect=mock_groq_create)

        # --- 3 x Exact Symbol/Code Queries ---
        # 1. calculate_fuel_details
        q1 = await synthesize_copilot_answer(db, "Where is calculate_fuel_details defined?")
        assert q1["status"] == "SUCCESS"
        assert any("calculate_fuel_details" in c["content"] for c in q1["evidence"])

        # 2. GROQ_API_KEY
        q2 = await synthesize_copilot_answer(db, "Where is GROQ_API_KEY referenced in config?")
        assert q2["status"] == "SUCCESS"
        assert any("GROQ_API_KEY" in c["content"] for c in q2["evidence"])

        # 3. normalize_canonical_asset
        q3 = await synthesize_copilot_answer(db, "What does normalize_canonical_asset return?")
        assert q3["status"] == "SUCCESS"
        assert any("normalize_canonical_asset" in c["content"] for c in q3["evidence"])

        # --- 2 x Conceptual Document Queries ---
        # 4. Tenancy termination notice period
        q4 = await synthesize_copilot_answer(db, "What is the tenancy agreement termination notice period?")
        assert q4["status"] == "SUCCESS"
        assert any("two (2) full months" in c["content"] for c in q4["evidence"])

        # 5. RON95 subsidy quota
        q5 = await synthesize_copilot_answer(db, "What are the rules for RON95 subsidy eligibility and liters?")
        assert q5["status"] == "SUCCESS"
        assert any("200 liters" in c["content"] for c in q5["evidence"])

        # --- 2 x Cross-Section / Context Queries ---
        # 6. Double-tap duplicate protection
        q6 = await synthesize_copilot_answer(db, "How does Perlica handle double-tap duplicate expenses?")
        assert q6["status"] == "SUCCESS"
        assert any("Double-Tap Protection" in c["content"] for c in q6["evidence"])

        # 7. Manifest reconciler deleted file detection
        q7 = await synthesize_copilot_answer(db, "How does the manifest reconciler detect deleted files?")
        assert q7["status"] == "SUCCESS"
        assert any("last_seen_sync_id" in c["content"] for c in q7["evidence"])

        # --- 2 x Unanswerable Queries ---
        # 8. Quantum encryption protocol (must abstain without LLM call)
        q8 = await synthesize_copilot_answer(db, "What is the quantum RSA decryption algorithm used in Perlica?")
        assert q8["status"] == "ABSTAINED"

        # 9. Bitcoin mining rigs
        q9 = await synthesize_copilot_answer(db, "How many Bitcoin mining rigs are currently operational?")
        assert q9["status"] == "ABSTAINED"

        # --- 1 x Partial-Index Query ---
        # 10. Query for un-indexed file in a PARTIAL source
        q10 = await synthesize_copilot_answer(db, "Find the deployment Dockerfile configuration in Perlica", source_scope="github:DanielKoh2004/perlica")
        assert q10["status"] == "ABSTAINED"
        # Invariant: Discloses partial coverage ratio
        assert "partially indexed" in q10["response"]
        assert "5 / 10" in q10["response"]


@pytest.mark.asyncio
async def test_telemetry_logging_and_historical_snapshot_survival(seeded_copilot_db):
    """
    Verify retrieval telemetry is written to SQLite and historical snapshots survive source purges.
    """
    db = seeded_copilot_db

    async def mock_groq_create(*args, **kwargs):
        mock_choice = MagicMock()
        mock_choice.message.content = "Subsidized price is RM 1.99 per liter."
        mock_res = MagicMock()
        mock_res.choices = [mock_choice]
        return mock_res

    with patch("src.rag_engine.AsyncGroq") as mock_groq_cls:
        mock_groq_instance = mock_groq_cls.return_value
        mock_groq_instance.chat.completions.create = AsyncMock(side_effect=mock_groq_create)

        res = await synthesize_copilot_answer(db, "What is the fuel price in calculate_fuel_details?")
        answer_id = res.answer_id
        assert answer_id is not None
        assert res.status == "SUCCESS"
        assert len(res.citations) > 0
        assert len(res.evidence_ids) > 0
        assert res.coverage.status == "COMPLETE"
        assert res.citations[0].label is not None

        # Verify telemetry was recorded in retrieval_logs
        async with db.get_connection() as conn:
            async with conn.execute("SELECT * FROM retrieval_logs WHERE answer_id = ?", (answer_id,)) as cur:
                log_row = await cur.fetchone()
                assert log_row is not None
                assert log_row["query"] == "What is the fuel price in calculate_fuel_details?"
                assert log_row["context_token_count"] > 0

        # Purge the GitHub source
        await db.delete_knowledge_source("github:DanielKoh2004/perlica")

        # CRITICAL INVARIANT: Historical answer evidence snapshot is STILL PRESERVED!
        snapshots = await db.get_answer_evidence_snapshots(answer_id)
        assert len(snapshots) > 0
        assert any("calculate_fuel_details" in s["raw_text"] for s in snapshots)
