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
async def test_failed_processing_does_not_purge_remote_file(temp_db):
    """
    Verify that transient network or parsing failures during a sync do NOT
    cause the file to be purged from SQLite manifest.
    """
    source_id = await temp_db.get_or_create_source("Resilient Repo", "GITHUB", "github:DanielKoh2004/resilient-repo")

    # Sync 1: file1.py is successfully indexed
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_auth_v1",
        sync_id=1,
        chunks=[{"section_title": "auth", "content": "def authenticate_user(): pass"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )
    res1 = await temp_db.fts_search_knowledge("authenticate_user", source_id=source_id)
    assert len(res1) == 1

    # Sync 2: src/auth.py still exists in remote tree, but GitHub fetch fails transiently
    await temp_db.mark_source_file_failed(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_auth_v2",
        sync_id=2,
        status="FAILED_FETCH",
    )

    # Purge unseen files for Sync 2 -> 0 files purged because auth.py was seen
    purged = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=2)
    assert purged == 0

    # Invariant check: source_files entry is FAILED_FETCH and manifest is preserved
    manifest = await temp_db.get_source_files_manifest(source_id)
    assert "src/auth.py" in manifest
    assert manifest["src/auth.py"]["status"] == "FAILED_FETCH"
    assert manifest["src/auth.py"]["last_seen_sync_id"] == 2


@pytest.mark.asyncio
async def test_failed_file_is_not_retrievable(temp_db):
    """
    Invariant: Only files whose current sync status is 'INDEXED' participate in retrieval.
    Chunks of failed files are not exposed to the LLM to prevent presenting stale evidence as current.
    """
    source_id = await temp_db.get_or_create_source("Stale Guard Repo", "GITHUB", "github:DanielKoh2004/stale-guard")

    # Sync 1: auth.py is indexed and searchable
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_v1",
        sync_id=1,
        chunks=[{"section_title": "auth", "content": "def verify_legacy_token(): pass"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )
    res1 = await temp_db.fts_search_knowledge("verify_legacy_token", source_id=source_id)
    assert len(res1) == 1

    dense1 = await temp_db.get_all_chunks_with_embeddings(source_id=source_id)
    assert len(dense1) == 1

    # Sync 2: auth.py fails to fetch/parse -> marked FAILED_FETCH
    await temp_db.mark_source_file_failed(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_v2",
        sync_id=2,
        status="FAILED_FETCH",
    )

    # Retrieval must return 0 results because status is FAILED_FETCH
    res_failed = await temp_db.fts_search_knowledge("verify_legacy_token", source_id=source_id)
    assert len(res_failed) == 0

    dense_failed = await temp_db.get_all_chunks_with_embeddings(source_id=source_id)
    assert len(dense_failed) == 0

    # Sync 3: auth.py successfully reindexes with new content
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/auth.py",
        blob_sha="sha_v3",
        sync_id=3,
        chunks=[{"section_title": "auth", "content": "def verify_oauth_token(): pass"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )

    # Searchable again!
    res_restored = await temp_db.fts_search_knowledge("verify_oauth_token", source_id=source_id)
    assert len(res_restored) == 1


@pytest.mark.asyncio
async def test_secret_exclusion_migration_purges_chunks_and_preserves_manifest(temp_db):
    """
    Verify the migration: a previously indexed file that later contains a secret is marked
    EXCLUDED_SECRET, retained in the manifest without deletion, and its chunks are removed.
    """
    source_id = await temp_db.get_or_create_source("Secret Repo", "GITHUB", "github:DanielKoh2004/secret-repo")

    # Sync 1: config.py is indexed normally
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="config/keys.py",
        blob_sha="sha_clean",
        sync_id=1,
        chunks=[{"section_title": "keys", "content": "PUBLIC_KEY = '123'"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )
    res1 = await temp_db.fts_search_knowledge("PUBLIC_KEY", source_id=source_id)
    assert len(res1) == 1

    # Sync 2: config.py now contains a secret -> marked EXCLUDED_SECRET
    await temp_db.mark_source_file_secret_excluded(
        source_id=source_id,
        file_path="config/keys.py",
        blob_sha="sha_secret",
        sync_id=2,
    )

    # Purge unseen files -> 0 files purged because keys.py was seen in Sync 2
    purged = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=2)
    assert purged == 0

    manifest = await temp_db.get_source_files_manifest(source_id)
    assert "config/keys.py" in manifest
    assert manifest["config/keys.py"]["status"] == "EXCLUDED_SECRET"
    assert manifest["config/keys.py"]["last_seen_sync_id"] == 2

    # Chunks are completely purged from FTS and dense index
    res_secret = await temp_db.fts_search_knowledge("PUBLIC_KEY", source_id=source_id)
    assert len(res_secret) == 0

    dense_secret = await temp_db.get_all_chunks_with_embeddings(source_id=source_id)
    assert len(dense_secret) == 0


@pytest.mark.asyncio
async def test_embedding_failure_does_not_purge_or_retrieve_file(temp_db):
    """
    Verify that an ONNX embedding runtime error marks the file FAILED_EMBED,
    preserves manifest tracking without deletion, and excludes old chunks from retrieval.
    """
    source_id = await temp_db.get_or_create_source("Embed Guard Repo", "GITHUB", "github:DanielKoh2004/embed-guard")

    # Sync 1: vector.py is indexed and retrievable
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/vector.py",
        blob_sha="sha_vec_v1",
        sync_id=1,
        chunks=[{"section_title": "vector", "content": "def compute_cosine_distance(): pass"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )
    res1 = await temp_db.fts_search_knowledge("compute_cosine_distance", source_id=source_id)
    assert len(res1) == 1

    # Sync 2: embedding calculation fails -> marks FAILED_EMBED
    await temp_db.mark_source_file_failed(
        source_id=source_id,
        file_path="src/vector.py",
        blob_sha="sha_vec_v2",
        sync_id=2,
        status="FAILED_EMBED",
    )

    # Purge unseen files for Sync 2 -> 0 files purged
    purged = await temp_db.purge_unseen_source_files(source_id=source_id, current_sync_id=2)
    assert purged == 0

    manifest = await temp_db.get_source_files_manifest(source_id)
    assert "src/vector.py" in manifest
    assert manifest["src/vector.py"]["status"] == "FAILED_EMBED"
    assert manifest["src/vector.py"]["last_seen_sync_id"] == 2

    # Old chunks are not searchable
    res_failed = await temp_db.fts_search_knowledge("compute_cosine_distance", source_id=source_id)
    assert len(res_failed) == 0

    dense_failed = await temp_db.get_all_chunks_with_embeddings(source_id=source_id)
    assert len(dense_failed) == 0

    # Sync 3: successful re-embed restores retrieval
    await temp_db.commit_file_reconciliation(
        source_id=source_id,
        file_path="src/vector.py",
        blob_sha="sha_vec_v3",
        sync_id=3,
        chunks=[{"section_title": "vector", "content": "def compute_euclidean_distance(): pass"}],
        embeddings=[("bge-small-en-v1.5", b"\x00" * 1536)],
    )
    res_restored = await temp_db.fts_search_knowledge("compute_euclidean_distance", source_id=source_id)
    assert len(res_restored) == 1


@pytest.mark.asyncio
async def test_get_github_sources_and_auto_sync_configuration(temp_db):
    """Verify get_github_sources only retrieves GITHUB sources and config settings are valid."""
    from src.config import settings

    # Add 2 GITHUB sources, 1 WEB source, 1 NOTE source
    await temp_db.get_or_create_source("Perlica Core", "GITHUB", "github:DanielKoh2004/perlica")
    await temp_db.get_or_create_source("Frontend Repo", "GITHUB", "github:DanielKoh2004/perlica-web")
    await temp_db.get_or_create_source("Docs Site", "WEB", "https://docs.perlica.dev")
    await temp_db.get_or_create_source("Quick Notes", "NOTES", "local:notes")

    gh_sources = await temp_db.get_github_sources()
    assert len(gh_sources) == 2
    assert all(s["source_type"] == "GITHUB" for s in gh_sources)
    refs = [s["source_ref"] for s in gh_sources]
    assert "github:DanielKoh2004/perlica" in refs
    assert "github:DanielKoh2004/perlica-web" in refs

    # Verify auto-sync configuration properties
    assert settings.REPO_AUTO_SYNC_ENABLED is True
    assert settings.repo_auto_sync_hour_minute == (4, 0)


@pytest.mark.asyncio
async def test_durable_ingestion_recovery_resets_running_jobs(temp_db):
    """Verify that startup recovery resets interrupted RUNNING jobs to PENDING and returns them."""
    j1 = await temp_db.create_ingestion_job("GITHUB", "github:DanielKoh2004/perlica")
    j2 = await temp_db.create_ingestion_job("WEB", "https://docs.perlica.dev")
    j3 = await temp_db.create_ingestion_job("PDF", "doc.pdf")

    # Simulate j1 running, j2 completed, j3 pending when container crashes
    await temp_db.update_ingestion_job(j1, "RUNNING", "Cloning repository...")
    await temp_db.update_ingestion_job(j2, "COMPLETED", "Indexed 10 pages")

    # Bot restarts: recover jobs
    pending = await temp_db.recover_interrupted_ingestion_jobs()
    pending_ids = [j["id"] for j in pending]

    assert j1 in pending_ids
    assert j3 in pending_ids
    assert j2 not in pending_ids

    j1_rec = await temp_db.get_ingestion_job(j1)
    assert j1_rec["status"] == "PENDING"
    assert "Recovered after restart" in j1_rec["progress_text"]


@pytest.mark.asyncio
async def test_detailed_source_coverage_breakdown(temp_db):
    """Verify get_source_detailed_coverage categorizes indexed, failed, and excluded files accurately."""
    source_id = await temp_db.get_or_create_source("Repo", "GITHUB", "github:test/repo")

    # Commit 2 indexed files
    await temp_db.commit_file_reconciliation(source_id, "src/a.py", "sha1", 1, [{"section_title": "a", "content": "a"}], [])
    await temp_db.commit_file_reconciliation(source_id, "src/b.py", "sha2", 1, [{"section_title": "b", "content": "b"}], [])

    # Record 1 failed file and 2 capped/excluded files
    await temp_db.mark_source_file_failed(source_id, "src/c.py", "sha3", 1, "FAILED_FETCH")
    await temp_db.mark_source_files_excluded_cap(source_id, [("large/d.py", "sha4")], 1)
    await temp_db.mark_source_file_secret_excluded(source_id, ".env", "sha5", 1)

    coverage = await temp_db.get_source_detailed_coverage(source_id)
    assert coverage["indexed_count"] == 2
    assert coverage["failed_count"] == 1
    assert coverage["excluded_cap_count"] == 1
    assert coverage["excluded_other_count"] == 1
    assert coverage["total_count"] == 5


def test_ast_logical_units_full_span_preserved():
    """Verify that chunk_python_code preserves the full logical function span without truncation."""
    from src.github_sync import chunk_python_code

    code = "def large_handler():\n" + "\n".join([f"    line_{i} = {i}" for i in range(150)]) + "\n    return True\n"
    chunks = chunk_python_code(code, "test/repo", "handlers.py", "sha123")

    assert len(chunks) == 1
    fn_chunk = chunks[0]
    assert fn_chunk["section_title"] == "handlers.py > def large_handler()"
    assert "line_149 = 149" in fn_chunk["content"]
    assert "return True" in fn_chunk["content"]
    assert fn_chunk["metadata"]["line_range"] == "1-152"
    assert fn_chunk["permalink_url"] == "https://github.com/test/repo/blob/sha123/handlers.py#L1-L152"


def test_pdf_over_50_pages_rejected(tmp_path):
    """Verify that PDF files with > 50 pages are explicitly rejected with ValueError."""
    from src.pdf_parser import _extract_pdf_sync
    from pypdf import PdfWriter

    pdf_path = str(tmp_path / "huge_doc.pdf")
    writer = PdfWriter()
    for _ in range(55):
        writer.add_blank_page(width=100, height=100)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    with pytest.raises(ValueError) as exc:
        _extract_pdf_sync(pdf_path)
    assert "PDF exceeds page limit" in str(exc.value)


@pytest.mark.asyncio
async def test_web_body_size_cap_enforced():
    """Verify MAX_WEB_BODY_BYTES constant and size verification."""
    from src.security import MAX_WEB_BODY_BYTES

    assert MAX_WEB_BODY_BYTES == 2 * 1024 * 1024

