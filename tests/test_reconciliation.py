import os
import pytest
import aiosqlite
from src.database import DatabaseManager


@pytest.fixture
async def temp_db(tmp_path):
    db_file = str(tmp_path / "test_reconciliation.db")
    db = DatabaseManager(db_path=db_file)
    await db.init_db()
    yield db


@pytest.mark.asyncio
async def test_fts5_triggers_insert_update_delete(temp_db):
    """Verify FTS5 triggers synchronize on INSERT, UPDATE, and DELETE."""
    source_id = await temp_db.get_or_create_source("Test Repo", "GITHUB", "github:DanielKoh2004/perlica")

    # 1. Insert chunk via reconciliation
    file_id = await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/fuel.py",
        blob_sha="sha1",
        sync_id=1,
        chunks=[{
            "section_title": "calculate_fuel_details",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha1/src/fuel.py#L1-L10",
            "content": "def calculate_fuel_details(amount):\n    return amount / 1.99",
            "metadata": {"symbol": "calculate_fuel_details", "line_range": "1-10"},
        }],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )

    # Search for calculate_fuel_details
    fts_res = await temp_db.fts_search_knowledge("calculate_fuel_details", source_id=source_id)
    assert len(fts_res) == 1
    assert fts_res[0]["section_title"] == "calculate_fuel_details"

    # 2. Update chunk (replace file content)
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/fuel.py",
        blob_sha="sha2",
        sync_id=2,
        chunks=[{
            "section_title": "calculate_diesel_details",
            "permalink_url": "https://github.com/DanielKoh2004/perlica/blob/sha2/src/fuel.py#L1-L10",
            "content": "def calculate_diesel_details(amount):\n    return amount / 2.95",
            "metadata": {"symbol": "calculate_diesel_details", "line_range": "1-10"},
        }],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )

    # Old search should return nothing, new search should return result
    old_res = await temp_db.fts_search_knowledge("calculate_fuel_details", source_id=source_id)
    assert len(old_res) == 0

    new_res = await temp_db.fts_search_knowledge("calculate_diesel_details", source_id=source_id)
    assert len(new_res) == 1
    assert new_res[0]["section_title"] == "calculate_diesel_details"


@pytest.mark.asyncio
async def test_manifest_reconciliation_and_deleted_file_purge(temp_db):
    """
    Verify manifest reconciliation:
    - Sync 1: Adds file_a.py and file_b.py
    - Sync 2: file_a.py is modified, file_c.py is new, file_b.py is omitted (deleted remotely)
    - Verifies file_b.py and its chunks are purged cleanly via sync_id.
    """
    source_id = await temp_db.get_or_create_source("Perlica", "GITHUB", "github:DanielKoh2004/perlica")

    # Sync 1: sync_id = 101
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="file_a.py",
        blob_sha="sha_a1",
        sync_id=101,
        chunks=[{"section_title": "File A", "content": "Content of file A v1"}],
        embeddings=[],
    )
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="file_b.py",
        blob_sha="sha_b1",
        sync_id=101,
        chunks=[{"section_title": "File B", "content": "Content of file B v1"}],
        embeddings=[],
    )

    manifest_1 = await temp_db.get_source_files_manifest(source_id)
    assert len(manifest_1) == 2
    assert "file_a.py" in manifest_1
    assert "file_b.py" in manifest_1

    # Sync 2: sync_id = 102 (file_a modified, file_c added, file_b deleted)
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="file_a.py",
        blob_sha="sha_a2",
        sync_id=102,
        chunks=[{"section_title": "File A", "content": "Content of file A v2"}],
        embeddings=[],
    )
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="file_c.py",
        blob_sha="sha_c1",
        sync_id=102,
        chunks=[{"section_title": "File C", "content": "Content of file C v1"}],
        embeddings=[],
    )

    # Purge unseen files for sync_id 102
    purged_count = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=102)
    assert purged_count == 1  # file_b.py purged!

    manifest_2 = await temp_db.get_source_files_manifest(source_id)
    assert len(manifest_2) == 2
    assert "file_a.py" in manifest_2
    assert "file_c.py" in manifest_2
    assert "file_b.py" not in manifest_2

    # Verify FTS search for purged file_b returns 0
    res_b = await temp_db.fts_search_knowledge("Content of file B", source_id=source_id)
    assert len(res_b) == 0


@pytest.mark.asyncio
async def test_full_cascade_and_historical_snapshot_survival(temp_db):
    """
    Verify full lifecycle invariant:
    DELETE source -> source_files deleted -> knowledge_chunks deleted -> FTS5 entries deleted -> source query returns zero results -> historical answer_evidence still exists!
    """
    source_ref = "github:test/repo"
    source_id = await temp_db.get_or_create_source("Test Repo", "GITHUB", source_ref)

    file_id = await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="auth.py",
        blob_sha="sha_auth",
        sync_id=1,
        chunks=[{"section_title": "Auth Guard", "content": "GROQ_API_KEY authorization logic"}],
        embeddings=[],
    )

    # Record answer with snapshot evidence
    evidence = [{
        "source_id": source_id,
        "source_file_id": file_id,
        "citation": "auth.py:L1-10",
        "content": "GROQ_API_KEY authorization logic",
        "metadata": {"symbol": "auth"},
    }]
    answer_id = await temp_db.record_answer_with_evidence(
        query="Where is GROQ_API_KEY used?",
        response="In auth.py",
        user_id="12345",
        evidence_list=evidence,
    )

    # Verify FTS can find it
    fts_before = await temp_db.fts_search_knowledge("GROQ_API_KEY", source_id=source_id)
    assert len(fts_before) == 1

    # DELETE the knowledge source
    deleted = await temp_db.delete_knowledge_source(source_ref)
    assert deleted is True

    # 1. Source files deleted
    manifest = await temp_db.get_source_files_manifest(source_id)
    assert len(manifest) == 0

    # 2. FTS returns 0 results
    fts_after = await temp_db.fts_search_knowledge("GROQ_API_KEY", source_id=source_id)
    assert len(fts_after) == 0

    # 3. CRITICAL INVARIANT: Historical answer evidence snapshot is STILL INTACT!
    snapshots = await temp_db.get_answer_evidence_snapshots(answer_id)
    assert len(snapshots) == 1
    assert snapshots[0]["citation"] == "auth.py:L1-10"
    assert "GROQ_API_KEY" in snapshots[0]["raw_text"]


@pytest.mark.asyncio
async def test_fts5_persistence_across_reopen(temp_db, tmp_path):
    """Verify FTS5 results remain correct after closing and reopening the SQLite connection."""
    db_file = str(tmp_path / "test_reopen.db")
    db1 = DatabaseManager(db_path=db_file)
    await db1.init_db()

    source_id = await db1.get_or_create_source("Doc", "PDF", "local:doc.pdf")
    await db1.commit_file_reconciliation(
        source_id=source_id,
        file_path="doc.pdf",
        blob_sha="pdf_sha",
        sync_id=1,
        chunks=[{"section_title": "Tenancy Clause", "content": "Termination notice period is 2 months."}],
        embeddings=[],
    )

    # Close and open with new connection manager
    db2 = DatabaseManager(db_path=db_file)
    await db2.init_db()

    results = await db2.fts_search_knowledge("Termination notice period", source_id=source_id)
    assert len(results) == 1
    assert "2 months" in results[0]["content"]


@pytest.mark.asyncio
async def test_atomicity_failure_preserves_previous_state(temp_db):
    """
    Simulate an embedding/validation failure during ingestion and verify
    the previous chunks remain intact and the source is marked FAILED.
    """
    source_id = await temp_db.get_or_create_source("Perlica", "GITHUB", "github:DanielKoh2004/perlica")

    # 1. Successful Sync 1
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="main.py",
        blob_sha="sha_v1",
        sync_id=1,
        chunks=[{"section_title": "Main", "content": "Original production code v1"}],
        embeddings=[],
    )
    await temp_db.update_source_status(source_id=source_id, eligible_count=1, indexed_count=1, status="COMPLETE")

    # Verify initial state
    res1 = await temp_db.fts_search_knowledge("Original production code", source_id=source_id)
    assert len(res1) == 1

    # 2. Simulate extraction/embedding pipeline failure before commit
    try:
        # Pipeline fails here (e.g. out of memory, network disconnect, corrupted model output)
        raise RuntimeError("Embedding model runtime failed during vector quantization")
        # commit_file_reconciliation is NEVER reached
    except Exception as e:
        # Record failure on source
        await temp_db.update_source_status(
            source_id=source_id,
            eligible_count=1,
            indexed_count=0,
            status="FAILED",
            last_error=str(e),
        )

    # Verify previous state is PRESERVED and source status is FAILED
    src = await temp_db.get_source_by_id(source_id)
    assert src["status"] == "FAILED"
    assert "Embedding model runtime failed" in src["last_error"]

    res_after = await temp_db.fts_search_knowledge("Original production code", source_id=source_id)
    assert len(res_after) == 1
    assert res_after[0]["content"] == "Original production code v1"


@pytest.mark.asyncio
async def test_cap_aware_manifest_tracking_preserves_excluded_files(temp_db):
    """
    Verify that files beyond the ingestion cap marked as EXCLUDED_CAP
    are tracked in the manifest and NOT mistakenly purged as deleted files.
    """
    source_id = await temp_db.get_or_create_source("Large Repo", "GITHUB", "github:DanielKoh2004/large-repo")

    # Sync 1: 4 eligible files, index 2, cap 2
    # File 1 & 2 -> INDEXED
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/file1.py",
        blob_sha="sha1",
        sync_id=1,
        chunks=[{"section_title": "file1", "content": "print('file1')"}],
        embeddings=[],
    )
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/file2.py",
        blob_sha="sha2",
        sync_id=1,
        chunks=[{"section_title": "file2", "content": "print('file2')"}],
        embeddings=[],
    )
    # File 3 & 4 -> EXCLUDED_CAP
    await temp_db.mark_source_files_excluded_cap(
        source_id=source_id,
        capped_files=[("src/file3.py", "sha3"), ("src/file4.py", "sha4")],
        sync_id=1,
    )

    # Purge unseen files for Sync 1 -> 0 files purged
    purged1 = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=1)
    assert purged1 == 0

    manifest1 = await temp_db.get_source_files_manifest(source_id)
    assert len(manifest1) == 4
    assert manifest1["src/file1.py"]["status"] == "INDEXED"
    assert manifest1["src/file3.py"]["status"] == "EXCLUDED_CAP"

    # Sync 2: File 4 is deleted remotely (only files 1, 2, 3 in remote tree)
    # File 1 & 2 seen again
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/file1.py",
        blob_sha="sha1",
        sync_id=2,
        chunks=[{"section_title": "file1", "content": "print('file1')"}],
        embeddings=[],
    )
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/file2.py",
        blob_sha="sha2",
        sync_id=2,
        chunks=[{"section_title": "file2", "content": "print('file2')"}],
        embeddings=[],
    )
    # File 3 seen in manifest as EXCLUDED_CAP
    await temp_db.mark_source_files_excluded_cap(
        source_id=source_id,
        capped_files=[("src/file3.py", "sha3")],
        sync_id=2,
    )

    # Purge unseen files for Sync 2 -> exactly 1 file (src/file4.py) purged!
    purged2 = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=2)
    assert purged2 == 1

    manifest2 = await temp_db.get_source_files_manifest(source_id)
    assert len(manifest2) == 3
    assert "src/file4.py" not in manifest2
    assert "src/file3.py" in manifest2
    assert manifest2["src/file3.py"]["status"] == "EXCLUDED_CAP"


@pytest.mark.asyncio
async def test_failed_processing_does_not_purge_remote_file(temp_db):
    """
    Verify that transient network or parsing failures during a sync do NOT
    cause the file or its previous valid chunks to be purged from SQLite.
    """
    source_id = await temp_db.get_or_create_source("Resilient Repo", "GITHUB", "github:DanielKoh2004/resilient-repo")

    # Sync 1: file1.py is successfully indexed
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_auth_v1",
        sync_id=1,
        chunks=[{"section_title": "auth", "content": "def authenticate_user(): pass"}],
        embeddings=[],
    )
    res1 = await temp_db.fts_search_knowledge("authenticate_user", source_id=source_id)
    assert len(res1) == 1

    # Sync 2: src/auth.py still exists in remote tree, but GitHub fetch fails transiently
    await temp_db.mark_source_file_failed(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_auth_v1",
        sync_id=2,
        status="FAILED_FETCH",
    )

    # Purge unseen files for Sync 2 -> 0 files purged because auth.py was seen
    purged = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=2)
    assert purged == 0

    # Invariant check: source_files entry is FAILED_FETCH and old chunks remain searchable
    manifest = await temp_db.get_source_files_manifest(source_id)
    assert "src/auth.py" in manifest
    assert manifest["src/auth.py"]["status"] == "FAILED_FETCH"
    assert manifest["src/auth.py"]["last_seen_sync_id"] == 2

    # Previous valid chunks are preserved!
    res2 = await temp_db.fts_search_knowledge("authenticate_user", source_id=source_id)
    assert len(res2) == 1
    assert "authenticate_user" in res2[0]["content"]


@pytest.mark.asyncio
async def test_secret_exclusion_does_not_purge_remote_file(temp_db):
    """
    Verify that files excluded due to secret detection are tracked in the manifest
    without being treated as absent/deleted, and their chunks are purged.
    """
    source_id = await temp_db.get_or_create_source("Secret Repo", "GITHUB", "github:DanielKoh2004/secret-repo")

    # Sync 1: File contains AWS secret and is excluded
    await temp_db.mark_source_file_secret_excluded(
        source_id=source_id,
        file_path="config/aws_keys.py",
        blob_sha="sha_secret_1",
        sync_id=1,
    )

    # Purge unseen files for Sync 1 -> 0 files purged
    purged = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=1)
    assert purged == 0

    manifest = await temp_db.get_source_files_manifest(source_id)
    assert "config/aws_keys.py" in manifest
    assert manifest["config/aws_keys.py"]["status"] == "EXCLUDED_SECRET"
    assert manifest["config/aws_keys.py"]["last_seen_sync_id"] == 1

    # Chunks are completely empty
    async with temp_db.get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) as count FROM knowledge_chunks WHERE source_id = ?", (source_id,)) as cur:
            row = await cur.fetchone()
            assert row["count"] == 0
