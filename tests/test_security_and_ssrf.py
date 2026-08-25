import pytest
from src.security import is_secret_path, scan_content_for_secrets, validate_safe_url, SSRFSecurityError


def test_secret_path_denylist():
    """Verify that secret files and credential extensions are rejected."""
    assert is_secret_path(".env") is True
    assert is_secret_path(".env.local") is True
    assert is_secret_path("config/.env.production") is True
    assert is_secret_path("server.key") is True
    assert is_secret_path("certs/tls.pem") is True
    assert is_secret_path("keys/id_rsa") is True
    assert is_secret_path("keys/id_ed25519") is True
    assert is_secret_path("credentials.json") is True
    assert is_secret_path("service_account.json") is True

    # Safe files
    assert is_secret_path("src/database.py") is False
    assert is_secret_path("README.md") is False
    assert is_secret_path("contracts/lease_2026.pdf") is False


def test_secret_content_scanning():
    """Verify private key and token regex scanning."""
    private_key_sample = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
    assert scan_content_for_secrets(private_key_sample) is True

    aws_sample = "AWS_SECRET_KEY = AKIAIOSFODNN7EXAMPLE35"
    assert scan_content_for_secrets(aws_sample) is True

    safe_code = "def calculate_fuel_details(amount: float):\n    return amount / 1.99"
    assert scan_content_for_secrets(safe_code) is False


def test_ssrf_url_validation_blocks_private_and_local():
    """Verify that SSRF validation rejects localhost, internal IPs, and non-http schemes."""
    # Bad schemes
    with pytest.raises(SSRFSecurityError):
        validate_safe_url("file:///etc/passwd")

    with pytest.raises(SSRFSecurityError):
        validate_safe_url("ftp://server.local/data")

    # Localhost
    with pytest.raises(SSRFSecurityError):
        validate_safe_url("http://localhost:8000/api")

    with pytest.raises(SSRFSecurityError):
        validate_safe_url("http://127.0.0.1:5000/secret")

    with pytest.raises(SSRFSecurityError):
        validate_safe_url("http://[::1]/internal")

    # Cloud metadata
    with pytest.raises(SSRFSecurityError):
        validate_safe_url("http://169.254.169.254/latest/meta-data")


def test_ssrf_url_validation_allows_public():
    """Verify that public valid URLs pass validation."""
    assert validate_safe_url("https://example.com/docs") == "https://example.com/docs"
    assert validate_safe_url("https://github.com/DanielKoh2004/perlica") == "https://github.com/DanielKoh2004/perlica"


def test_copilot_authorization_fails_closed(monkeypatch):
    """Verify Copilot authorization is fail-closed when no allowlist is configured."""
    from src.security import is_user_authorized_for_copilot
    from src.config import settings

    # Case 1: Unconfigured -> fails closed
    monkeypatch.setattr(settings, "ALLOWED_DISCORD_USERS", [])
    monkeypatch.setattr(settings, "ALLOWED_USER_ID", None)
    assert is_user_authorized_for_copilot(123456789) is False

    # Case 2: Configured with ALLOWED_USER_ID
    monkeypatch.setattr(settings, "ALLOWED_USER_ID", 123456789)
    assert is_user_authorized_for_copilot(123456789) is True
    assert is_user_authorized_for_copilot(999999999) is False

    # Case 3: Configured with ALLOWED_DISCORD_USERS
    monkeypatch.setattr(settings, "ALLOWED_DISCORD_USERS", [111, 222])
    monkeypatch.setattr(settings, "ALLOWED_USER_ID", None)
    assert is_user_authorized_for_copilot(111) is True
    assert is_user_authorized_for_copilot(222) is True
    assert is_user_authorized_for_copilot(333) is False


def test_quick_notes_secret_scanning_and_embedding_semantics():
    """Verify that Quick Notes pass through secret scanning before persistence."""
    from src.security import scan_content_for_secrets

    # Case 1: Private key or token in note content
    secret_note = "Here is my secret token: ghp_123456789012345678901234567890123456"
    assert scan_content_for_secrets(secret_note) is True

    secret_title = "-----BEGIN RSA PRIVATE KEY-----"
    assert scan_content_for_secrets(secret_title) is True

    # Case 2: Safe notes pass
    safe_note = "Remember to deploy Railway staging using git push origin main."
    assert scan_content_for_secrets(safe_note) is False
    assert scan_content_for_secrets("Deployment Cheatsheet") is False
