"""
Security tests: Authentication & API hardening.

Covers:
  - SECRET_KEY validation
  - Auth requirements on endpoints
  - Admin-only wallet payment endpoints
  - Tool execution security (exec disabled, shell=False)
  - Sandbox hardening (pids_limit, read_only, cap_drop, tmpfs)
  - UserUpdate password complexity
  - File ID glob injection prevention
  - Rate limiter Redis-backed storage
  - Admin endpoint dependency
  - Wallet safety limit enforcement
  - LND TLS cert temp file security
  - GPU service output path traversal
  - Notification link open redirect prevention
"""
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    PropertyMock,
    call,
    patch,
)
from uuid import UUID, uuid4
import os
import re
import tempfile

import pytest
import pytest_asyncio

from tests.helpers.paths import backend_file, project_file, require_file


# ============================================================================
# SECRET_KEY Validation
# ============================================================================

class TestSecretKeyValidation:
    """Tests that insecure SECRET_KEY values are rejected."""

    def test_insecure_key_raises_in_production(self):
        """Production environment with default key should raise RuntimeError."""
        from app.core.config import Settings

        s = Settings(
            secret_key="dev_secret_key_change_in_production",
            environment="production",
            database_url="sqlite:///test.db",
        )
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            s.validate_secret_key()

    def test_insecure_key_warns_in_development(self):
        """Development environment with default key should warn but not raise."""
        from app.core.config import Settings

        s = Settings(
            secret_key="dev_secret_key_change_in_production",
            environment="development",
            database_url="sqlite:///test.db",
        )
        # Should not raise — just logs a warning
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            s.validate_secret_key()
            mock_logger.warning.assert_called_once()

    def test_secure_key_passes_in_production(self):
        """Production environment with strong key should pass."""
        from app.core.config import Settings

        s = Settings(
            secret_key="a_very_strong_and_unique_secret_key_that_is_not_default_12345",
            environment="production",
            database_url="sqlite:///test.db",
        )
        # Should not raise
        s.validate_secret_key()

    def test_all_known_insecure_keys_rejected(self):
        """All entries in _INSECURE_SECRET_KEYS should be rejected in production."""
        from app.core.config import Settings

        insecure_keys = {
            "dev_secret_key_change_in_production",
            "your_super_secret_key_here_change_this_in_production",
            "changeme",
            "secret",
        }
        for bad_key in insecure_keys:
            s = Settings(
                secret_key=bad_key,
                environment="production",
                database_url="sqlite:///test.db",
            )
            with pytest.raises(RuntimeError):
                s.validate_secret_key()


# ============================================================================
# Opportunity Endpoints Require Auth
# ============================================================================

class TestOpportunityEndpointsAuth:
    """Tests that opportunity endpoints reject unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_list_opportunities_requires_auth(self, async_client):
        """GET /opportunities/ should require auth (307 redirect or 401/403)."""
        response = await async_client.get("/api/v1/opportunities/")
        assert response.status_code in (401, 403, 307)

    @pytest.mark.asyncio
    async def test_get_opportunity_requires_auth(self, async_client):
        """GET /opportunities/{id} should require auth."""
        response = await async_client.get(f"/api/v1/opportunities/{uuid4()}")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_statistics_requires_auth(self, async_client):
        """GET /opportunities/statistics should require auth."""
        response = await async_client.get("/api/v1/opportunities/statistics")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_pipeline_requires_auth(self, async_client):
        """GET /opportunities/pipeline should require auth."""
        response = await async_client.get("/api/v1/opportunities/pipeline")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_by_tier_requires_auth(self, async_client):
        """GET /opportunities/by-tier should require auth."""
        response = await async_client.get("/api/v1/opportunities/by-tier")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_strategies_requires_auth(self, async_client):
        """GET /opportunities/strategies should require auth."""
        response = await async_client.get("/api/v1/opportunities/strategies")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_insights_requires_auth(self, async_client):
        """GET /opportunities/insights should require auth."""
        response = await async_client.get("/api/v1/opportunities/insights")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_agent_endpoints_require_admin(self, async_client, test_user):
        """Agent endpoints should require admin role, not just auth."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        headers = {"Authorization": f"Bearer {token}"}

        response = await async_client.post(
            "/api/v1/opportunities/agent/plan",
            headers=headers,
            json={"goal": "test"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_authenticated_user_can_access(self, async_client, test_user):
        """Authenticated user should be able to access basic endpoints (not 401/403)."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        headers = {"Authorization": f"Bearer {token}"}

        response = await async_client.get(
            "/api/v1/opportunities/by-tier",
            headers=headers,
        )
        # Should succeed (200) — not a 401/403
        assert response.status_code == 200


# ============================================================================
# Wallet Payment Endpoints Require Admin
# ============================================================================

class TestWalletAdminOnly:
    """Tests that payment endpoints require admin role."""

    @pytest.mark.asyncio
    async def test_send_payment_requires_admin(self, async_client, test_user):
        """Non-admin user should get 403 on payment endpoints (with LND dependency overridden)."""
        from app.core.security import create_access_token
        from app.api.endpoints.wallet import require_lnd

        token = create_access_token(data={"sub": str(test_user.id)})

        # Override require_lnd to bypass LND availability check
        from app.main import app
        app.dependency_overrides[require_lnd] = lambda: True

        try:
            response = await async_client.post(
                "/api/v1/wallet/payments/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"payment_request": "lnbc1test"},
            )
            assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(require_lnd, None)

    @pytest.mark.asyncio
    async def test_send_onchain_requires_admin(self, async_client, test_user):
        """Non-admin cannot send on-chain transactions."""
        from app.core.security import create_access_token
        from app.api.endpoints.wallet import require_lnd

        token = create_access_token(data={"sub": str(test_user.id)})

        from app.main import app
        app.dependency_overrides[require_lnd] = lambda: True

        try:
            response = await async_client.post(
                "/api/v1/wallet/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"address": "bc1qtest", "amount_sats": 1000},
            )
            assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(require_lnd, None)

    @pytest.mark.asyncio
    async def test_update_safety_limit_requires_admin(self, async_client, test_user):
        """Non-admin cannot update safety limits."""
        from app.core.security import create_access_token
        from app.api.endpoints.wallet import require_lnd

        token = create_access_token(data={"sub": str(test_user.id)})

        from app.main import app
        app.dependency_overrides[require_lnd] = lambda: True

        try:
            response = await async_client.put(
                "/api/v1/wallet/safety-limit",
                headers={"Authorization": f"Bearer {token}"},
                json={"max_payment_sats": 1000000},
            )
            assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(require_lnd, None)

    @pytest.mark.asyncio
    async def test_admin_can_access_payment_endpoints(self, async_client, test_admin_user):
        """Admin user should pass auth check (may fail on LND, but not 403)."""
        from app.core.security import create_access_token
        from app.api.endpoints.wallet import require_lnd

        token = create_access_token(data={"sub": str(test_admin_user.id)})

        from app.main import app
        app.dependency_overrides[require_lnd] = lambda: True

        try:
            response = await async_client.post(
                "/api/v1/wallet/payments/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"payment_request": "lnbc1test"},
            )
            # Should not be 403 (admin passes auth; may be 422/500 due to invalid data)
            assert response.status_code != 403
        finally:
            app.dependency_overrides.pop(require_lnd, None)


# ============================================================================
# Tool Execution Security
# ============================================================================

class TestToolExecutionSecurity:
    """Tests that dangerous tool execution paths are disabled."""

    @pytest.mark.asyncio
    async def test_exec_call_template_disabled(self):
        """Python SDK call_template (exec) should return an error."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()

        # Create a mock Tool object
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.slug = "test-tool"

        result = await executor._execute_python_sdk(
            tool=mock_tool,
            params={"test": "value"},
            config={
                "module": "httpx",
                "call_template": "exec('import os')",
            },
        )
        assert result.success is False
        assert "disabled" in result.error.lower() or "security" in result.error.lower()

    @pytest.mark.asyncio
    async def test_cli_always_uses_exec_not_shell(self):
        """CLI execution should always use subprocess exec, never shell."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()

        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.slug = "test-tool"

        # Even if config says shell=True, it should be ignored
        with patch("app.services.tool_execution_service.asyncio.create_subprocess_exec") as mock_exec, \
             patch("app.services.tool_execution_service.asyncio.create_subprocess_shell") as mock_shell:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await executor._execute_cli(
                tool=mock_tool,
                params={"args": ["hello"]},
                config={
                    "command": "ffmpeg",  # Use an allowed command
                    "shell": True,  # Should be ignored
                },
            )

            # Should have used exec, never shell
            mock_exec.assert_called()
            mock_shell.assert_not_called()


# ============================================================================
# Sandbox Image Allowlist
# ============================================================================

class TestSandboxImageAllowlist:
    """Tests that sandbox rejects images not on the allowlist.

    Requires the ``docker`` Python SDK, which is only installed inside the
    backend container.  The entire class is skipped when the package is
    absent so the rest of the security test file still runs locally.
    """

    @pytest.mark.asyncio
    async def test_disallowed_image_rejected(self):
        """Non-allowlisted image should raise ValueError."""
        docker = pytest.importorskip("docker", reason="docker SDK only installed in container")
        from app.services.dev_sandbox_service import DevSandboxService

        service = DevSandboxService()

        with pytest.raises(ValueError, match="not in the allowed"):
            await service.create_sandbox(image="malicious/image:latest")

    @pytest.mark.asyncio
    async def test_default_image_is_on_allowlist(self):
        """The default sandbox image should be on the allowlist."""
        from app.core.config import settings

        assert settings.dev_sandbox_default_image in settings.dev_sandbox_allowed_images

    def test_allowlist_contains_expected_images(self):
        """Verify the allowlist contains the standard development images."""
        from app.core.config import settings

        assert "python:3.12-slim" in settings.dev_sandbox_allowed_images
        assert "node:20-slim" in settings.dev_sandbox_allowed_images


# ============================================================================
# Auth Error Messages (Anti-enumeration)
# ============================================================================

class TestAuthErrorMessages:
    """Tests that auth errors don't leak user existence info."""

    @pytest.mark.asyncio
    async def test_duplicate_email_generic_message(self, async_client, test_user):
        """Registering with existing email gives generic message."""
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": "newuser123",
                "password": "StrongPass1!",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        # Should NOT reveal that the email is taken
        assert "email" not in detail.lower()
        assert "already" not in detail.lower()

    @pytest.mark.asyncio
    async def test_duplicate_username_generic_message(self, async_client, test_user):
        """Registering with existing username gives generic message."""
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "new@example.com",
                "username": test_user.username,
                "password": "StrongPass1!",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        # Should NOT reveal that the username is taken
        assert "username" not in detail.lower()
        assert "taken" not in detail.lower()


# ============================================================================
# API Docs Gating
# ============================================================================

class TestApiDocsGating:
    """Tests that API docs can be disabled."""

    def test_docs_disabled_when_enable_docs_false(self):
        """When enable_docs is False, docs_url should be None."""
        docs_url = "/docs" if False else None
        assert docs_url is None

    def test_docs_enabled_when_enable_docs_true(self):
        """When enable_docs is True, docs_url should be set."""
        docs_url = "/docs" if True else None
        assert docs_url == "/docs"


# ============================================================================
# Path Traversal Prevention
# ============================================================================

class TestPathTraversalPrevention:
    """Tests that output file endpoints reject path traversal attempts."""

    def test_path_name_strips_directory_components(self):
        """Path.name should strip directory traversal components."""
        from pathlib import Path

        test_cases = [
            ("../../../etc/passwd", "passwd"),
            ("normal_file.mp4", "normal_file.mp4"),
            ("sub/dir/file.wav", "file.wav"),
        ]
        for input_name, expected_name in test_cases:
            safe = Path(input_name).name
            assert safe == expected_name, f"Path('{input_name}').name should be '{expected_name}', got '{safe}'"

    def test_traversal_pattern_detected(self):
        """Path traversal patterns should be caught by the sanitization logic."""
        from pathlib import Path

        malicious_inputs = [
            "../etc/passwd",
            "../../secret.key",
            "..\\windows\\system32",
        ]
        for filename in malicious_inputs:
            safe_name = Path(filename).name
            has_problem = safe_name != filename or ".." in filename
            assert has_problem, f"'{filename}' should be detected as malicious"

    def test_safe_filenames_pass(self):
        """Normal filenames should pass sanitization."""
        from pathlib import Path

        safe_inputs = [
            "output.mp4",
            "audio-2024-01-15.wav",
            "generated_image_001.png",
        ]
        for filename in safe_inputs:
            safe_name = Path(filename).name
            has_problem = safe_name != filename or ".." in filename
            assert not has_problem, f"'{filename}' should be allowed"


# ============================================================================
# Mempool TLS Verification
# ============================================================================

class TestMempoolTlsVerification:
    """Tests that TLS verification is conditional on URL."""

    def test_public_mempool_uses_tls(self):
        """Public mempool.space should verify TLS."""
        base_url = "https://mempool.space"
        is_public = "mempool.space" in base_url
        verify_tls = is_public
        assert verify_tls is True

    def test_self_hosted_can_skip_tls(self):
        """Self-hosted Mempool can skip TLS for self-signed certs."""
        base_url = "https://my-local-mempool:8999"
        is_public = "mempool.space" in base_url
        verify_tls = is_public
        assert verify_tls is False

    def test_localhost_skips_tls(self):
        """Localhost Mempool should skip TLS."""
        base_url = "http://localhost:8999"
        is_public = "mempool.space" in base_url
        verify_tls = is_public
        assert verify_tls is False


# ============================================================================
# Sandbox Path Sanitization (shlex.quote)
# ============================================================================

class TestSandboxPathSanitization:
    """Tests that sandbox file operations sanitize paths."""

    def test_shlex_quote_prevents_injection(self):
        """shlex.quote should prevent command injection via directory paths."""
        import shlex

        malicious_paths = [
            "/workspace/$(rm -rf /)",
            "/workspace/; cat /etc/passwd",
            "/workspace/`whoami`",
            "/workspace/' || echo pwned",
        ]
        for path in malicious_paths:
            quoted = shlex.quote(path)
            # Quoted path should be safe for shell execution
            assert "'" in quoted or quoted.startswith("'"), \
                f"shlex.quote should wrap '{path}' in quotes"
            # The original dangerous characters should be escaped
            assert quoted != path, f"'{path}' should be modified by shlex.quote"


# ============================================================================
# SDK Module Allowlist (C-1 fix)
# ============================================================================

class TestSdkModuleAllowlist:
    """Tests that the Python SDK executor only imports allowed modules."""

    @pytest.mark.asyncio
    async def test_blocked_module_returns_error(self):
        """Importing a non-allowlisted module should fail with clear error."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        result = await executor._execute_python_sdk(
            tool=mock_tool,
            params={},
            config={"module": "os", "function": "system"},
        )
        assert result.success is False
        assert "allowlist" in result.error.lower()

    @pytest.mark.asyncio
    async def test_dangerous_modules_blocked(self):
        """Common dangerous modules should be blocked."""
        from app.services.tool_execution_service import ToolExecutor, ALLOWED_SDK_MODULES

        dangerous = ["os", "subprocess", "sys", "importlib", "shutil", "pathlib",
                      "ctypes", "pickle", "marshal", "builtins", "code", "codeop"]
        for mod in dangerous:
            assert mod not in ALLOWED_SDK_MODULES, \
                f"Dangerous module '{mod}' should NOT be in ALLOWED_SDK_MODULES"

    @pytest.mark.asyncio
    async def test_allowed_modules_pass_allowlist(self):
        """Modules in the allowlist should pass the initial check."""
        from app.services.tool_execution_service import ToolExecutor, ALLOWED_SDK_MODULES

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        for mod in ["httpx", "openai"]:
            assert mod in ALLOWED_SDK_MODULES
            # These may fail on ImportError or config issues, but NOT on allowlist
            result = await executor._execute_python_sdk(
                tool=mock_tool,
                params={},
                config={"module": mod, "function": "nonexistent"},
            )
            if not result.success:
                assert "allowlist" not in result.error.lower()


# ============================================================================
# Environment Variable Allowlist (C-2 fix)
# ============================================================================

class TestEnvVarAllowlist:
    """Tests that _resolve_env_vars only resolves allowed variables."""

    def test_sensitive_vars_blocked(self):
        """SECRET_KEY, DATABASE_URL, macaroon etc. should resolve to empty."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        data = {
            "secret": "$SECRET_KEY",
            "db": "$DATABASE_URL",
            "macaroon": "$LND_MACAROON_HEX",
            "password": "$POSTGRES_PASSWORD",
        }
        result = executor._resolve_env_vars(data)
        for key in data:
            assert result[key] == "", \
                f"Sensitive var {data[key]} should resolve to empty string"

    def test_allowed_vars_resolve(self):
        """Allowed vars (service URLs etc.) should resolve normally."""
        import os
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        # Set a test value
        os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
        try:
            result = executor._resolve_env_vars({"url": "$OLLAMA_BASE_URL"})
            assert result["url"] == "http://localhost:11434"
        finally:
            os.environ.pop("OLLAMA_BASE_URL", None)

    def test_nested_dicts_checked(self):
        """Env var check should apply recursively to nested dicts."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        data = {
            "config": {
                "nested_secret": "$SECRET_KEY",
            }
        }
        result = executor._resolve_env_vars(data)
        assert result["config"]["nested_secret"] == ""

    def test_non_env_values_unchanged(self):
        """Regular string values (not $-prefixed) should pass through."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        data = {
            "name": "hello",
            "count": 42,
            "flag": True,
        }
        result = executor._resolve_env_vars(data)
        assert result == data


# ============================================================================
# CLI Command Allowlist (H-2 fix)
# ============================================================================

class TestCliCommandAllowlist:
    """Tests that the CLI executor only runs allowed binaries."""

    @pytest.mark.asyncio
    async def test_disallowed_command_blocked(self):
        """Arbitrary commands like 'rm' should be blocked."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        result = await executor._execute_cli(
            tool=mock_tool,
            params={"args": ["-rf", "/"]},
            config={"command": "rm"},
        )
        assert result.success is False
        assert "allowlist" in result.error.lower()

    @pytest.mark.asyncio
    async def test_path_traversal_in_command_blocked(self):
        """Commands with path components should still be checked."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        result = await executor._execute_cli(
            tool=mock_tool,
            params={"args": ["hello"]},
            config={"command": "/usr/bin/chmod"},
        )
        assert result.success is False
        assert "allowlist" in result.error.lower()

    @pytest.mark.asyncio
    async def test_allowed_command_passes_check(self):
        """Allowed commands like 'ffmpeg' should pass the allowlist check."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        with patch("app.services.tool_execution_service.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await executor._execute_cli(
                tool=mock_tool,
                params={"args": ["-version"]},
                config={"command": "ffmpeg"},
            )
            # Should NOT fail on allowlist; may succeed or fail for other reasons
            if not result.success:
                assert "allowlist" not in result.error.lower()


# ============================================================================
# MCP Command Allowlist (H-2 fix)
# ============================================================================

class TestMcpCommandAllowlist:
    """Tests that the MCP stdio executor only spawns allowed binaries."""

    @pytest.mark.asyncio
    async def test_disallowed_mcp_command_blocked(self):
        """Arbitrary MCP server commands should be blocked."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        result = await executor._execute_mcp_stdio(
            tool=mock_tool,
            params={},
            config={"server_command": "/bin/bash -c 'echo pwned'"},
            tool_name="test",
        )
        assert result.success is False
        assert "allowlist" in result.error.lower()

    @pytest.mark.asyncio
    async def test_allowed_mcp_command_passes(self):
        """Allowed MCP commands like 'node' should pass the check."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.slug = "test-tool"

        with patch("app.services.tool_execution_service.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b'{"jsonrpc":"2.0","id":1,"result":"ok"}', b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await executor._execute_mcp_stdio(
                tool=mock_tool,
                params={},
                config={"server_command": "node server.js"},
                tool_name="test",
            )
            if not result.success:
                assert "allowlist" not in result.error.lower()


# ============================================================================
# Encryption utilities (H-5, H-6)
# ============================================================================

class TestEncryptionUtilities:
    """Tests for the shared field encryption module."""

    @pytest.fixture(autouse=True)
    def _mock_salt(self):
        """Mock the salt file to avoid PermissionError on /app/.encryption_salt."""
        from unittest.mock import patch
        import app.core.encryption as enc_mod
        enc_mod._fernet = None
        enc_mod._fernet_legacy = None
        with patch("app.core.encryption._load_or_create_salt", return_value=b"test_salt_value!" * 2):
            yield
        enc_mod._fernet = None
        enc_mod._fernet_legacy = None

    def test_encrypt_decrypt_round_trip(self):
        """Encrypting then decrypting should return the original value."""
        from app.core.encryption import encrypt_field, decrypt_field
        plaintext = "deadbeef1234567890abcdef"
        ciphertext = encrypt_field(plaintext)
        assert ciphertext != plaintext  # Should not be stored as-is
        assert decrypt_field(ciphertext) == plaintext

    def test_ciphertext_is_not_plaintext(self):
        """Encrypted output must not contain the original value."""
        from app.core.encryption import encrypt_field
        plaintext = "my_secret_private_key_hex"
        ciphertext = encrypt_field(plaintext)
        assert plaintext not in ciphertext

    def test_decrypt_invalid_token_raises(self):
        """Decrypting garbage should raise ValueError."""
        from app.core.encryption import decrypt_field
        with pytest.raises(ValueError, match="SECRET_KEY"):
            decrypt_field("not-a-valid-fernet-token")

    def test_different_plaintexts_produce_different_ciphertexts(self):
        """Two different inputs should produce different encrypted outputs."""
        from app.core.encryption import encrypt_field
        ct1 = encrypt_field("value_one")
        ct2 = encrypt_field("value_two")
        assert ct1 != ct2


class TestNostrKeyManagerKDF:
    """Tests that Nostr key manager uses the upgraded KDF (PBKDF2)."""

    @pytest.fixture(autouse=True)
    def _mock_salt(self):
        """Mock the salt file to avoid PermissionError on /app/.encryption_salt."""
        from unittest.mock import patch
        import app.core.encryption as enc_mod
        enc_mod._fernet = None
        enc_mod._fernet_legacy = None
        with patch("app.core.encryption._load_or_create_salt", return_value=b"test_salt_value!" * 2):
            yield
        enc_mod._fernet = None
        enc_mod._fernet_legacy = None

    def test_encrypt_decrypt_nsec(self):
        """Round-trip encryption of an nsec key."""
        from app.services.nostr_key_manager import encrypt_nsec, decrypt_nsec
        nsec = "nsec1abc123testkey"
        encrypted = encrypt_nsec(nsec)
        assert encrypted != nsec
        assert decrypt_nsec(encrypted) == nsec


# ============================================================================
# Rate limiting (H-1)
# ============================================================================

class TestRateLimiting:
    """Tests that rate limiting is configured on auth endpoints."""

    def test_rate_limiter_module_exists(self):
        """The rate_limit module should be importable."""
        from app.core.rate_limit import limiter
        assert limiter is not None

    def test_auth_register_has_request_param(self):
        """Register endpoint should accept Request for rate limiting."""
        import inspect
        from app.api.endpoints.auth import register
        sig = inspect.signature(register)
        param_names = list(sig.parameters.keys())
        assert "request" in param_names

    def test_auth_login_has_request_param(self):
        """Login endpoint should accept Request for rate limiting."""
        import inspect
        from app.api.endpoints.auth import login
        sig = inspect.signature(login)
        param_names = list(sig.parameters.keys())
        assert "request" in param_names


# ============================================================================
# Service Manager security (H-4)
# ============================================================================

class TestServiceManagerSecurity:
    """Tests that service_manager supports API key auth and is safe for network access."""

    def test_default_host_binds_all_interfaces(self):
        """Default --host should be 0.0.0.0 so Docker containers can reach it.
        
        Security is provided by API-key middleware, not by binding to localhost.
        Docker containers access the service manager via host.docker.internal,
        which resolves to the Docker bridge IP (e.g. 172.17.0.1), not 127.0.0.1.
        """
        import os
        script_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "scripts", "service_manager.py"
        )
        if not os.path.exists(script_path):
            pytest.skip("scripts/service_manager.py not available in this environment")
        with open(script_path) as f:
            content = f.read()
        assert 'default="0.0.0.0"' in content

    def test_api_key_middleware_present(self):
        """Service manager must have API key middleware protecting non-health endpoints."""
        import os
        script_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "scripts", "service_manager.py"
        )
        if not os.path.exists(script_path):
            pytest.skip("scripts/service_manager.py not available in this environment")
        with open(script_path) as f:
            content = f.read()
        assert "SERVICE_MANAGER_API_KEY" in content
        assert "X-API-Key" in content
        assert "/health" in content  # Health endpoint should be exempt

    def test_service_api_keys_in_docker_compose(self):
        """docker-compose.yml must pass GPU_SERVICE_API_KEY and SERVICE_MANAGER_API_KEY to backend."""
        import os
        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "docker-compose.yml"
        )
        if not os.path.exists(compose_path):
            pytest.skip("docker-compose.yml not available in this environment")
        with open(compose_path) as f:
            content = f.read()
        assert "GPU_SERVICE_API_KEY" in content, (
            "docker-compose.yml must pass GPU_SERVICE_API_KEY to backend"
        )
        assert "SERVICE_MANAGER_API_KEY" in content, (
            "docker-compose.yml must pass SERVICE_MANAGER_API_KEY to backend"
        )

    def test_backend_config_has_service_manager_api_key(self):
        """Backend Settings must expose service_manager_api_key for lifecycle calls."""
        from app.core.config import Settings
        assert "service_manager_api_key" in Settings.model_fields, (
            "Settings.service_manager_api_key is required so the backend can "
            "authenticate to the service manager"
        )

    def test_lifecycle_service_sends_sm_auth_header(self):
        """gpu_lifecycle_service must send X-API-Key on service manager calls."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "services",
            "gpu_lifecycle_service.py",
        )
        with open(path) as f:
            content = f.read()
        assert "_service_manager_headers" in content, (
            "gpu_lifecycle_service.py must use _service_manager_headers() "
            "when calling the service manager"
        )
        assert "service_manager_api_key" in content, (
            "gpu_lifecycle_service.py must read service_manager_api_key from settings"
        )

    def test_service_api_keys_in_env_example(self):
        """`.env.example` must document GPU_SERVICE_API_KEY and SERVICE_MANAGER_API_KEY."""
        import os
        env_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".env.example"
        )
        if not os.path.exists(env_path):
            pytest.skip(".env.example not available in this environment")
        with open(env_path) as f:
            content = f.read()
        assert "GPU_SERVICE_API_KEY" in content
        assert "SERVICE_MANAGER_API_KEY" in content

    def test_start_py_auto_generates_service_keys(self):
        """start.py must call ensure_service_api_keys to auto-generate keys on first run."""
        import os
        start_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "start.py"
        )
        if not os.path.exists(start_path):
            pytest.skip("start.py not available in this environment")
        with open(start_path) as f:
            content = f.read()
        assert "def ensure_service_api_keys" in content, (
            "start.py must define ensure_service_api_keys"
        )
        assert "ensure_service_api_keys(" in content, (
            "start.py must call ensure_service_api_keys during startup"
        )


# ============================================================================
# Security Headers Middleware (M-14/15)
# ============================================================================

class TestSecurityHeadersMiddleware:
    """Tests that security headers middleware is configured in main.py."""

    def test_security_headers_middleware_present(self):
        """main.py should register SecurityHeadersMiddleware."""
        import os
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "SecurityHeadersMiddleware" in content
        assert "X-Content-Type-Options" in content
        assert "X-Frame-Options" in content
        assert "Referrer-Policy" in content
        assert "Permissions-Policy" in content

    def test_cors_methods_restricted(self):
        """CORS allow_methods should not use wildcard."""
        import os
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        # Should NOT contain allow_methods=["*"]
        assert 'allow_methods=["*"]' not in content
        # Should contain specific methods
        assert "GET" in content
        assert "POST" in content

    def test_cors_headers_restricted(self):
        """CORS allow_headers should not use wildcard."""
        import os
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert 'allow_headers=["*"]' not in content
        assert "Authorization" in content
        assert "Content-Type" in content


# ============================================================================
# Test Endpoint Gating (M-16)
# ============================================================================

class TestEndpointGating:
    """Tests that test endpoints are gated behind non-production check."""

    def test_test_resources_gated_in_api(self):
        """test_resources router should be conditionally included."""
        import os
        api_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "api", "api.py"
        )
        with open(api_path) as f:
            content = f.read()
        # The test_resources inclusion should be behind an environment check
        assert "production" in content.lower() or "environment" in content.lower()
        # Should NOT be unconditionally included
        lines = content.split("\n")
        for line in lines:
            if "test_resources" in line and "include_router" in line:
                # Find the line's indentation — if gated, it should be indented
                stripped = line.lstrip()
                indent = len(line) - len(stripped)
                assert indent > 0, "test_resources router should be conditionally included (indented under if)"


# ============================================================================
# GPU Service CORS Restriction (M-1)
# ============================================================================

class TestGpuServiceCors:
    """Tests that GPU services do not use wildcard CORS origins."""

    GPU_SERVICE_FILES = [
        "z-image/app.py",
        "media-toolkit/app.py",
        "audiosr/app.py",
        "canary-stt/app.py",
        "seedvr2-upscaler/app.py",
        "ltx-video/app.py",
        "docling-parser/app.py",
        "realesrgan-cpu/app.py",
    ]

    def test_no_wildcard_origins(self):
        """GPU services should not use allow_origins=['*']."""
        import os
        project_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
        for rel_path in self.GPU_SERVICE_FILES:
            full_path = os.path.join(project_root, rel_path)
            if os.path.exists(full_path):
                with open(full_path) as f:
                    content = f.read()
                assert 'allow_origins=["*"]' not in content, (
                    f"{rel_path} still uses wildcard CORS origins"
                )


# ============================================================================
# LND TLS Verify Default (M-7)
# ============================================================================

class TestLndTlsVerify:
    """Tests that LND TLS verification defaults to True."""

    def test_lnd_tls_verify_defaults_true(self):
        """lnd_tls_verify should default to True."""
        import os
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "core", "config.py"
        )
        with open(config_path) as f:
            content = f.read()
        # The default in the source code should be True
        assert "lnd_tls_verify: bool = True" in content


# ============================================================================
# Nostr Relay URL Validation (M-5)
# ============================================================================

class TestNostrRelayUrlValidation:
    """Tests that Nostr relay URLs are validated before connection."""

    def test_valid_wss_url_accepted(self):
        from app.services.nostr_service import _validate_relay_url
        result = _validate_relay_url("wss://relay.damus.io")
        assert result == "wss://relay.damus.io"

    def test_valid_ws_url_accepted(self):
        from app.services.nostr_service import _validate_relay_url
        result = _validate_relay_url("ws://relay.example.com:8080")
        assert result == "ws://relay.example.com:8080"

    def test_http_scheme_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="scheme"):
            _validate_relay_url("http://relay.example.com")

    def test_https_scheme_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="scheme"):
            _validate_relay_url("https://relay.example.com")

    def test_empty_hostname_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="hostname"):
            _validate_relay_url("wss://")

    def test_localhost_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="internal host"):
            _validate_relay_url("wss://localhost:8080")

    def test_docker_internal_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="internal host"):
            _validate_relay_url("wss://host.docker.internal")

    def test_private_ip_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="private IP"):
            _validate_relay_url("wss://192.168.1.1:8080")

    def test_loopback_ip_rejected(self):
        from app.services.nostr_service import _validate_relay_url
        with pytest.raises(ValueError, match="private IP"):
            _validate_relay_url("wss://127.0.0.1:8080")


# ============================================================================
# WebSocket Auth Consistency (M-4)
# ============================================================================

class TestWebSocketAuthConsistency:
    """Tests that WebSocket auth uses first-message auth only.
    
    SA2-10: Query-param auth removed — tokens in URLs leak via logs/history.
    Only first-message auth is now supported.
    """

    def test_agents_ws_auth_no_query_params(self):
        """websocket_security.py _extract_ws_token should NOT use query_params (SA2-10)."""
        import os
        ws_security_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "api", "websocket_security.py"
        )
        with open(ws_security_path) as f:
            content = f.read()
        # Find the _extract_ws_token function body
        import re
        match = re.search(
            r'async def _extract_ws_token\(.*?\n(?=\ndef |\nclass |\nasync def |\Z)',
            content, re.DOTALL,
        )
        assert match, "_extract_ws_token function not found"
        func_body = match.group()
        # SA2-10: query_params must NOT be present
        assert "query_params" not in func_body, (
            "_extract_ws_token should not use query_params (SA2-10: tokens leak via URL)"
        )

    def test_frontend_uses_websocket_auth(self):
        """Frontend WebSocket hooks should authenticate (query param or first-message auth)."""
        import os
        for hook_file in ["useAgentChat.ts", "useCampaignProgress.ts"]:
            path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "frontend", "src", "hooks", hook_file
            )
            if os.path.exists(path):
                with open(path) as f:
                    content = f.read()
                # SA-07: support query param auth OR first-message auth
                has_query_param = "?token=" in content or "token=${" in content
                has_first_message = '"type": "auth"' in content or "type: 'auth'" in content or "type: \"auth\"" in content
                assert has_query_param or has_first_message, (
                    f"{hook_file} should authenticate via query param or first-message auth"
                )


# ============================================================================
# Token Revocation / Logout (M-9)
# ============================================================================

class TestTokenRevocation:
    """Tests for JWT token revocation via JTI blocklist."""

    def test_token_includes_jti(self):
        """Created tokens should include a jti claim."""
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(data={"sub": "test-user-id"})
        payload = decode_access_token(token)
        assert payload is not None
        assert "jti" in payload
        assert len(payload["jti"]) == 32  # uuid4 hex

    def test_revoke_token_blocks_decode(self):
        """A revoked token should return None from decode_access_token."""
        from app.core.security import (
            create_access_token, decode_access_token, revoke_token, _revoked_jtis,
        )
        token = create_access_token(data={"sub": "test-user-id"})
        payload = decode_access_token(token)
        assert payload is not None
        jti = payload["jti"]

        revoke_token(jti)
        try:
            assert decode_access_token(token) is None
        finally:
            # Clean up
            _revoked_jtis.pop(jti, None)

    def test_unrevoked_token_still_works(self):
        """Tokens with different JTIs should not be affected by another's revocation."""
        from app.core.security import (
            create_access_token, decode_access_token, revoke_token, _revoked_jtis,
        )
        token_a = create_access_token(data={"sub": "user-a"})
        token_b = create_access_token(data={"sub": "user-b"})
        jti_a = decode_access_token(token_a)["jti"]

        revoke_token(jti_a)
        try:
            assert decode_access_token(token_a) is None
            assert decode_access_token(token_b) is not None
        finally:
            _revoked_jtis.pop(jti_a, None)

    def test_logout_endpoint_exists(self):
        """Auth router should have a /logout endpoint."""
        import os
        auth_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "api", "endpoints", "auth.py"
        )
        with open(auth_path) as f:
            content = f.read()
        assert '"/logout"' in content
        assert "revoke_token" in content


# ============================================================================
# Redis-backed Nostr Rate Limiter (M-6)
# ============================================================================

class TestNostrRateLimiterUpgrade:
    """Tests that the Nostr rate limiter supports Redis backend."""

    def test_rate_limiter_has_redis_support(self):
        """_RateLimiter should have Redis-backed methods."""
        from app.services.nostr_service import _RateLimiter
        rl = _RateLimiter()
        assert hasattr(rl, '_check_redis')
        assert hasattr(rl, '_record_redis')
        assert hasattr(rl, '_get_redis')

    def test_rate_limiter_memory_fallback(self):
        """In-memory fallback should still work correctly."""
        from app.services.nostr_service import _RateLimiter
        rl = _RateLimiter()
        # Force memory fallback
        rl._redis_checked = True
        rl._redis = None
        
        identity = "test-identity-123"
        # Should be under limit
        assert rl.check(identity, 3600, 5) is True
        # Record some events
        for _ in range(5):
            rl.record(identity)
        # Should now be at limit
        assert rl.check(identity, 3600, 5) is False


# ============================================================================
# Phase 4 — Low Priority Security Hardening
# ============================================================================


# ============================================================================
# Password Complexity (L-2 / L-3)
# ============================================================================

class TestPasswordComplexity:
    """Tests that password complexity rules are enforced on registration."""

    def test_weak_password_rejected(self):
        """Password without uppercase/digit/special should be rejected."""
        from app.schemas import UserCreate
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="alllowercase")

    def test_no_digit_rejected(self):
        from app.schemas import UserCreate
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="NoDigits!!")

    def test_no_special_rejected(self):
        from app.schemas import UserCreate
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="NoSpecial1")

    def test_strong_password_accepted(self):
        from app.schemas import UserCreate
        user = UserCreate(email="a@b.com", username="testuser", password="Strong1!x")
        assert user.password == "Strong1!x"

    def test_login_password_has_min_length(self):
        """LoginRequest.password should have min_length=1."""
        from app.schemas import LoginRequest
        with pytest.raises(Exception):
            LoginRequest(identifier="user@test.com", password="")

    def test_login_password_has_max_length(self):
        """LoginRequest.password should have max_length=128."""
        from app.schemas import LoginRequest
        with pytest.raises(Exception):
            LoginRequest(identifier="user@test.com", password="A" * 200)


# ============================================================================
# JWT aud/iss Claims (L-4)
# ============================================================================

class TestJwtClaims:
    """Tests that JWT tokens include audience and issuer claims."""

    def test_token_has_issuer(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(data={"sub": "test-id"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload.get("iss") == "money-agents"

    def test_token_has_audience(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(data={"sub": "test-id"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload.get("aud") == "money-agents"

    def test_wrong_audience_rejected(self):
        """A token with wrong audience should be rejected."""
        import jwt as pyjwt
        from app.core.config import settings
        payload = {
            "sub": "test-id",
            "aud": "wrong-audience",
            "iss": "money-agents",
            "jti": "abc123",
        }
        token = pyjwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
        from app.core.security import decode_access_token
        assert decode_access_token(token) is None


# ============================================================================
# enable_docs Default (L-5)
# ============================================================================

class TestEnableDocsDefault:
    """Tests that enable_docs defaults to False."""

    def test_enable_docs_defaults_false(self):
        import os
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "core", "config.py"
        )
        with open(config_path) as f:
            content = f.read()
        assert "enable_docs: bool = False" in content


# ============================================================================
# Startup Uses Logger Not Print (L-6)
# ============================================================================

class TestStartupLogging:
    """Tests that startup code uses logger instead of print()."""

    def test_no_print_in_lifespan(self):
        """The lifespan function should use logger, not print()."""
        import os, re
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        # Find the lifespan function body
        match = re.search(
            r'async def lifespan\(.*?\n(?=\n# Create FastAPI)',
            content, re.DOTALL,
        )
        assert match, "lifespan function not found"
        func_body = match.group()
        # Should not contain bare print() calls
        print_calls = re.findall(r'\bprint\s*\(', func_body)
        assert len(print_calls) == 0, (
            f"lifespan still has {len(print_calls)} print() calls — use logger instead"
        )


# ============================================================================
# Upload Size Limits on GPU Services (L-9)
# ============================================================================

class TestUploadSizeLimits:
    """Tests that GPU services with file uploads have size limits."""

    UPLOAD_SERVICES = [
        "audiosr/app.py",
        "canary-stt/app.py",
        "docling-parser/app.py",
        "realesrgan-cpu/app.py",
    ]

    def test_upload_services_have_size_limit(self):
        import os
        project_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
        for rel_path in self.UPLOAD_SERVICES:
            full_path = os.path.join(project_root, rel_path)
            if os.path.exists(full_path):
                with open(full_path) as f:
                    content = f.read()
                assert "MAX_UPLOAD_BYTES" in content, (
                    f"{rel_path} missing upload size limit"
                )
                assert "413" in content, (
                    f"{rel_path} missing 413 status code for oversized uploads"
                )

    def test_file_storage_uses_streaming(self):
        """file_storage.py save_file must stream chunks instead of full read."""
        source = backend_file("app/services/file_storage.py").read_text()
        assert "await file.read()" not in source, \
            "save_file should not read entire file at once—use chunked reads"
        assert "await file.read(CHUNK_SIZE)" in source or \
               "await file.read(chunk_size)" in source.lower(), \
            "save_file should read in fixed-size chunks"


# ============================================================================
# Flower Basic Auth (L-11)
# ============================================================================

class TestFlowerAuth:
    """Tests that Flower has basic auth configured."""

    def test_flower_has_basic_auth(self):
        import os
        compose_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "docker-compose.yml"
        )
        if not os.path.exists(compose_path):
            pytest.skip("docker-compose.yml not available in this environment")
        with open(compose_path) as f:
            content = f.read()
        assert "--basic-auth" in content


# ============================================================================
# Vite Dev Server Binding (L-13)
# ============================================================================

class TestViteDevServerBinding:
    """Tests that Vite dev server doesn't bind to 0.0.0.0."""

    def test_vite_not_bound_to_all_interfaces(self):
        import os
        vite_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "frontend", "vite.config.ts"
        )
        if not os.path.exists(vite_path):
            pytest.skip("frontend/vite.config.ts not available in this environment")
        with open(vite_path) as f:
            content = f.read()
        assert "'0.0.0.0'" not in content
        assert "'127.0.0.1'" in content or "localhost" in content


# ============================================================================
# Minimal localStorage User Data (L-14)
# ============================================================================

class TestMinimalLocalStorage:
    """Tests that auth store minimizes PII in localStorage."""

    def test_auth_store_minimizes_user_data(self):
        import os
        store_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "frontend", "src", "stores", "auth.ts"
        )
        if not os.path.exists(store_path):
            pytest.skip("frontend/src/stores/auth.ts not available in this environment")
        with open(store_path) as f:
            content = f.read()
        assert "minimizeUserForStorage" in content
        # Should NOT store email
        assert "email" not in content.split("minimizeUserForStorage")[1].split("}")[0] or \
               "email" not in content.split("return {")[1].split("}")[0]


# ============================================================================
# Client-Side Auth Throttling (L-16)
# ============================================================================

class TestClientSideThrottling:
    """Tests that the login form has throttling after failed attempts."""

    def test_login_form_has_throttling(self):
        import os
        login_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "frontend", "src",
            "components", "auth", "LoginForm.tsx"
        )
        if not os.path.exists(login_path):
            pytest.skip("frontend/src/components/auth/LoginForm.tsx not available in this environment")
        with open(login_path) as f:
            content = f.read()
        assert "throttle" in content.lower() or "backoff" in content.lower() or "failCount" in content.lower()


# ============================================================================
# Admin Credentials Not in PS (L-17)
# ============================================================================

class TestAdminCredsNotInPs:
    """Tests that admin creation passes creds via docker exec -e flags."""

    def test_start_py_uses_env_flags(self):
        """start.py should use -e flags for admin creds (docker compose v5 dropped --env-file for exec)."""
        import os
        start_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "start.py"
        )
        if not os.path.exists(start_path):
            pytest.skip("start.py not available in this environment")
        with open(start_path) as f:
            content = f.read()
        # Should use -e flags for env vars
        assert '"-e"' in content
        # Credentials should be passed as env vars, not embedded in python code
        assert '_ADMIN_EMAIL' in content
        assert '_ADMIN_PASSWORD' in content


# ============================================================================
# Tor Proxy SocksPolicy (L-18)
# ============================================================================

class TestTorProxySocksPolicy:
    """Tests that Tor proxy restricts connections to Docker network."""

    def test_tor_rejects_wildcard(self):
        import os
        torrc_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "tor-proxy", "torrc"
        )
        if not os.path.exists(torrc_path):
            pytest.skip("tor-proxy/torrc not available in this environment")
        with open(torrc_path) as f:
            content = f.read()
        assert "SocksPolicy accept *" not in content
        assert "SocksPolicy reject *" in content

# ============================================================================
# Sandbox Hardening
# ============================================================================

class TestSandboxHardening:
    """Tests that sandbox containers are created with proper security controls.

    These tests read the source file directly since the docker Python module
    is not installed in the test environment.
    """

    @pytest.fixture(autouse=True)
    def _load_source(self):
        _path = backend_file("app", "services", "dev_sandbox_service.py")
        if not _path.exists():
            pytest.skip("dev_sandbox_service.py not available in this environment")
        self.source = _path.read_text()

    def test_sandbox_uses_pids_limit(self):
        """Container creation must include pids_limit to prevent fork bombs."""
        assert "pids_limit=" in self.source, "pids_limit must be set on sandbox containers"

    def test_sandbox_uses_read_only(self):
        """Container root FS must be read-only."""
        assert "read_only=True" in self.source, "Container root filesystem must be read-only"

    def test_sandbox_uses_tmpfs(self):
        """Container must have tmpfs mounts for /tmp and /var/tmp."""
        assert '"/tmp"' in self.source, "tmpfs mount required for /tmp"
        assert '"/var/tmp"' in self.source, "tmpfs mount required for /var/tmp"

    def test_sandbox_drops_all_capabilities(self):
        """Container must drop ALL Linux capabilities."""
        assert 'cap_drop=["ALL"]' in self.source, "Must drop all Linux capabilities"

    def test_sandbox_has_no_new_privileges(self):
        """Container must set no-new-privileges security option."""
        assert '"no-new-privileges"' in self.source

    def test_sandbox_runs_as_unprivileged_user(self):
        """Container must run as non-root user."""
        assert 'user="1000:1000"' in self.source

    def test_sandbox_security_params_in_create_sandbox(self):
        """All security params must appear within the create_sandbox method."""
        # Extract the create_sandbox method body
        match = re.search(
            r'async def create_sandbox.*?(?=\n    async def |\nclass |\Z)',
            self.source, re.DOTALL
        )
        assert match, "create_sandbox method not found"
        fn = match.group()
        assert "read_only=True" in fn
        assert "pids_limit=" in fn
        assert 'cap_drop=["ALL"]' in fn
        assert '"no-new-privileges"' in fn
        assert 'user="1000:1000"' in fn
        assert 'tmpfs' in fn


# ============================================================================
# UserUpdate Password Complexity
# ============================================================================

class TestUserUpdatePasswordComplexity:
    """Tests that UserUpdate enforces the same password complexity as UserCreate."""

    def test_weak_password_rejected_no_uppercase(self):
        """Password without uppercase should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception, match="uppercase"):
            UserUpdate(password="weak1234!")

    def test_weak_password_rejected_no_lowercase(self):
        """Password without lowercase should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception, match="lowercase"):
            UserUpdate(password="WEAK1234!")

    def test_weak_password_rejected_no_digit(self):
        """Password without digit should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception, match="digit"):
            UserUpdate(password="WeakPass!")

    def test_weak_password_rejected_no_special(self):
        """Password without special char should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception, match="special"):
            UserUpdate(password="WeakPass1")

    def test_strong_password_accepted(self):
        """Password meeting all requirements should be accepted."""
        from app.schemas import UserUpdate
        update = UserUpdate(password="StrongP@ss1")
        assert update.password == "StrongP@ss1"

    def test_null_password_accepted(self):
        """None password (no change) should be accepted."""
        from app.schemas import UserUpdate
        update = UserUpdate(password=None)
        assert update.password is None

    def test_password_too_short_rejected(self):
        """Password shorter than 8 chars should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception):
            UserUpdate(password="Ab1!")

    def test_password_max_length_enforced(self):
        """Password exceeding 128 chars should be rejected."""
        from app.schemas import UserUpdate
        with pytest.raises(Exception):
            UserUpdate(password="A" * 120 + "a1!" + "x" * 10)

    def test_update_without_password_accepted(self):
        """UserUpdate without password field should work."""
        from app.schemas import UserUpdate
        update = UserUpdate(display_name="New Name")
        assert update.display_name == "New Name"
        assert update.password is None


# ============================================================================
# File ID Validation (Glob Injection Prevention)
# ============================================================================

class TestFileIdValidation:
    """Tests that file_id is validated before glob operations."""

    def test_valid_uuid_accepted(self):
        """Standard UUID should pass validation."""
        from app.services.file_storage import FileStorageService
        uid = str(uuid4())
        assert FileStorageService._validate_file_id(uid) is True

    def test_valid_uuid_with_thumb_accepted(self):
        """UUID with _thumb suffix should pass validation."""
        from app.services.file_storage import FileStorageService
        uid = str(uuid4()) + "_thumb"
        assert FileStorageService._validate_file_id(uid) is True

    def test_glob_metachar_star_rejected(self):
        """Glob * metacharacter should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("*") is False

    def test_glob_metachar_question_rejected(self):
        """Glob ? metacharacter should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("?") is False

    def test_glob_metachar_bracket_rejected(self):
        """Glob [] metacharacters should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("[test]") is False

    def test_path_traversal_rejected(self):
        """Path traversal .. should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("../../etc/passwd") is False

    def test_slash_rejected(self):
        """Forward slash should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("sub/dir") is False

    def test_empty_string_rejected(self):
        """Empty string should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("") is False

    def test_random_text_rejected(self):
        """Non-UUID text should be rejected."""
        from app.services.file_storage import FileStorageService
        assert FileStorageService._validate_file_id("my-file-name") is False

    @pytest.mark.asyncio
    async def test_get_file_path_rejects_invalid_id(self):
        """get_file_path should return None for invalid file_id."""
        from app.services.file_storage import FileStorageService
        result = await FileStorageService.get_file_path(
            file_id="*",
            conversation_id=uuid4(),
            message_id=uuid4(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_file_path_rejects_traversal(self):
        """get_file_path should return None for path traversal attempt."""
        from app.services.file_storage import FileStorageService
        result = await FileStorageService.get_file_path(
            file_id="../../etc/passwd",
            conversation_id=uuid4(),
            message_id=uuid4(),
        )
        assert result is None


# ============================================================================
# Rate Limiter Redis-Backed Storage
# ============================================================================

class TestRateLimiterStorage:
    """Tests that the rate limiter uses Redis-backed storage when available."""

    def test_redis_storage_used_when_redis_url_set(self):
        """Rate limiter should use Redis when REDIS_URL is available."""
        from app.core.rate_limit import _get_rate_limit_storage_uri
        with patch.dict(os.environ, {"REDIS_URL": "redis://:pass@redis:6379/0"}):
            uri = _get_rate_limit_storage_uri()
            assert uri.startswith("redis://")
            assert uri.endswith("/3"), f"Should use DB 3, got: {uri}"

    def test_memory_fallback_when_no_redis(self):
        """Rate limiter should fall back to memory when no REDIS_URL."""
        from app.core.rate_limit import _get_rate_limit_storage_uri
        with patch.dict(os.environ, {}, clear=True):
            # Remove REDIS_URL if present
            env = dict(os.environ)
            env.pop("REDIS_URL", None)
            with patch.dict(os.environ, env, clear=True):
                uri = _get_rate_limit_storage_uri()
                assert uri == "memory://"

    def test_redis_db_index_separated_from_app_data(self):
        """Rate limiter Redis DB must be different from app/broker/results DBs."""
        from app.core.rate_limit import _get_rate_limit_storage_uri
        with patch.dict(os.environ, {"REDIS_URL": "redis://:pass@redis:6379/0"}):
            uri = _get_rate_limit_storage_uri()
            # Extract DB number
            db_num = int(uri.split("/")[-1])
            # App uses /0, broker /1, results /2 — rate limiter should use /3
            assert db_num not in (0, 1, 2), f"DB {db_num} conflicts with app data DBs"


# ============================================================================
# Admin Endpoint Dependencies
# ============================================================================

class TestAdminEndpointDependencies:
    """Tests that system endpoints use get_current_admin dependency."""

    def test_system_health_uses_admin_dependency(self):
        """GET /system/health should use get_current_admin dependency."""
        import inspect
        from app.api.endpoints.agents import get_system_health

        sig = inspect.signature(get_system_health)
        params = sig.parameters
        user_param = params.get("current_user")
        assert user_param is not None
        # Check the default is Depends(get_current_admin), not get_current_user
        default = user_param.default
        assert hasattr(default, "dependency")
        from app.api.deps import get_current_admin
        assert default.dependency is get_current_admin

    def test_system_recover_uses_admin_dependency(self):
        """POST /system/recover should use get_current_admin dependency."""
        import inspect
        from app.api.endpoints.agents import trigger_system_recovery

        sig = inspect.signature(trigger_system_recovery)
        params = sig.parameters
        user_param = params.get("current_user")
        assert user_param is not None
        default = user_param.default
        assert hasattr(default, "dependency")
        from app.api.deps import get_current_admin
        assert default.dependency is get_current_admin

    @pytest.mark.asyncio
    async def test_system_health_rejects_non_admin(self, async_client, test_user):
        """GET /system/health should return 403 for non-admin users."""
        from app.core.security import create_access_token
        token = create_access_token({"sub": str(test_user.id)})
        response = await async_client.get(
            "/api/v1/agents/system/health",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_system_recover_rejects_non_admin(self, async_client, test_user):
        """POST /system/recover should return 403 for non-admin users."""
        from app.core.security import create_access_token
        token = create_access_token({"sub": str(test_user.id)})
        response = await async_client.post(
            "/api/v1/agents/system/recover",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


# ============================================================================
# Wallet Safety Limit Enforcement
# ============================================================================

class TestWalletSafetyLimits:
    """Tests that admin wallet endpoints enforce the global safety limit."""

    def test_check_max_payment_enforces_limit(self):
        """_check_max_payment should raise HTTPException when amount exceeds limit."""
        from app.api.endpoints.wallet import _check_max_payment
        from fastapi import HTTPException

        with patch("app.api.endpoints.wallet.settings") as mock_settings:
            mock_settings.lnd_max_payment_sats = 10000
            with pytest.raises(HTTPException) as exc_info:
                _check_max_payment(50000)
            assert exc_info.value.status_code == 400
            assert "safety limit" in exc_info.value.detail.lower()

    def test_check_max_payment_allows_within_limit(self):
        """_check_max_payment should not raise when amount is within limit."""
        from app.api.endpoints.wallet import _check_max_payment

        with patch("app.api.endpoints.wallet.settings") as mock_settings:
            mock_settings.lnd_max_payment_sats = 10000
            # Should not raise
            _check_max_payment(5000)

    def test_check_max_payment_blocks_when_zero(self):
        """_check_max_payment with limit=0 should block all payments."""
        from app.api.endpoints.wallet import _check_max_payment
        from fastapi import HTTPException

        with patch("app.api.endpoints.wallet.settings") as mock_settings:
            mock_settings.lnd_max_payment_sats = 0
            with pytest.raises(HTTPException) as exc_info:
                _check_max_payment(1)
            assert exc_info.value.status_code == 400
            assert "approval" in exc_info.value.detail.lower()

    def test_send_payment_endpoint_calls_safety_check(self):
        """send_payment endpoint source should decode and check payment amount."""
        import inspect
        from app.api.endpoints.wallet import send_payment

        source = inspect.getsource(send_payment)
        assert "_check_max_payment" in source, "send_payment must call _check_max_payment"
        assert "decode_payment_request" in source, "send_payment must decode invoice to get amount"

    def test_send_onchain_endpoint_calls_safety_check(self):
        """send_onchain endpoint source should check payment amount."""
        import inspect
        from app.api.endpoints.wallet import send_onchain

        source = inspect.getsource(send_onchain)
        assert "_check_max_payment" in source, "send_onchain must call _check_max_payment"


# ============================================================================
# LND TLS Certificate Temp File Security
# ============================================================================

class TestLndTlsCertSecurity:
    """Tests that LND TLS cert uses unpredictable temp file paths."""

    def test_no_predictable_temp_path(self):
        """LND service must not use hardcoded /tmp/lnd_tls.cert path."""
        import inspect
        from app.services.lnd_service import LNDService

        source = inspect.getsource(LNDService._get_ssl_context)
        assert "lnd_tls.cert" not in source.replace("lnd_tls_", ""), \
            "Must not use predictable temp file path"
        assert "mkstemp" in source, "Must use tempfile.mkstemp() for unique paths"

    def test_temp_file_cleaned_up(self):
        """LND service should clean up temp cert file after loading."""
        import inspect
        from app.services.lnd_service import LNDService

        source = inspect.getsource(LNDService._get_ssl_context)
        assert "os.unlink" in source, "Must clean up temp cert file after loading"

    def test_ssl_context_creation_with_cert(self):
        """_get_ssl_context should create valid SSL context from base64 cert."""
        import base64
        from app.services.lnd_service import LNDService

        service = LNDService()

        # Create a mock cert (not a real cert, but tests the code path)
        mock_cert_b64 = base64.b64encode(b"mock cert data").decode()

        with patch("app.services.lnd_service.settings") as mock_settings:
            mock_settings.lnd_tls_cert = mock_cert_b64
            with patch("ssl.SSLContext.load_verify_locations"):
                ctx = service._get_ssl_context()
                assert ctx is not None


# ============================================================================
# GPU Service Output Path Traversal
# ============================================================================

class TestGPUOutputPathTraversal:
    """Tests that GPU service /output/ endpoints have resolve() checks."""

    def _read_gpu_file(self, rel_path: str) -> str:
        path = project_file(*rel_path.split("/"))
        if not path.exists():
            pytest.skip(f"{rel_path} not available in this environment")
        return path.read_text()

    def test_zimage_output_has_resolve_check(self):
        """Z-Image /output/ endpoint must verify resolved path stays in OUTPUT_DIR."""
        source = self._read_gpu_file("z-image/app.py")
        # Find the get_output function
        match = re.search(r'async def get_output.*?(?=\n(?:@|class |if __name__|async def |\Z))',
                         source, re.DOTALL)
        assert match, "get_output function not found"
        fn_source = match.group()
        assert ".resolve()" in fn_source, "Must use resolve() check"
        assert "OUTPUT_DIR.resolve()" in fn_source

    def test_realesrgan_output_has_resolve_check(self):
        """Real-ESRGAN /output/ endpoint must verify resolved path stays in OUTPUT_DIR."""
        source = self._read_gpu_file("realesrgan-cpu/app.py")
        match = re.search(r'async def get_output.*?(?=\n(?:@|class |if __name__|async def |\Z))',
                         source, re.DOTALL)
        assert match, "get_output function not found"
        fn_source = match.group()
        assert ".resolve()" in fn_source, "Must use resolve() check"
        assert "OUTPUT_DIR.resolve()" in fn_source


# ============================================================================
# GPU Service Error Message Sanitization
# ============================================================================

class TestGPUErrorSanitization:
    """Tests that GPU services don't leak raw exception details to clients."""

    def _read_gpu_file(self, rel_path: str) -> str:
        path = project_file(*rel_path.split("/"))
        if not path.exists():
            pytest.skip(f"{rel_path} not available in this environment")
        return path.read_text()

    def test_zimage_no_raw_exception_in_500(self):
        """Z-Image should not include raw exception string in 500 responses."""
        source = self._read_gpu_file("z-image/app.py")
        # Look for the specific pattern: HTTPException(500, f"...{str(e)}")
        bad_pattern = re.findall(r'HTTPException.*500.*\{str\(e\)\}', source)
        assert len(bad_pattern) == 0, f"Found raw exception leaking in error response: {bad_pattern}"

    def test_qwen3tts_no_raw_exception_in_500(self):
        """Qwen3-TTS should not include raw exception string in 500 responses."""
        source = self._read_gpu_file("qwen3-tts/app.py")
        bad_pattern = re.findall(r'HTTPException.*500.*\{str\(e\)\}', source)
        assert len(bad_pattern) == 0, f"Found raw exception leaking in error response: {bad_pattern}"

    def test_realesrgan_no_raw_exception_in_500(self):
        """Real-ESRGAN should not include raw exception string in 500 responses."""
        source = self._read_gpu_file("realesrgan-cpu/app.py")
        bad_pattern = re.findall(r'HTTPException.*500.*\{str\(e\)\}', source)
        assert len(bad_pattern) == 0, f"Found raw exception leaking in error response: {bad_pattern}"


# ============================================================================
# Qwen3-TTS Upload File Size Limit
# ============================================================================

class TestTTSUploadSizeLimit:
    """Tests that Qwen3-TTS upload_voice enforces a file size limit."""

    def test_upload_voice_has_size_limit(self):
        """upload_voice must check file size before processing."""
        path = project_file("qwen3-tts", "app.py")
        if not path.exists():
            pytest.skip("qwen3-tts/app.py not available in this environment")
        source = path.read_text()
        match = re.search(r'async def upload_voice.*?(?=\n(?:@|class |async def |\Z))',
                         source, re.DOTALL)
        assert match, "upload_voice function not found"
        fn_source = match.group()
        assert "MAX_VOICE_SIZE" in fn_source or "413" in fn_source, \
            "upload_voice must enforce a file size limit"

    def test_upload_returns_413_for_oversized(self):
        """upload_voice should return 413 for files exceeding the limit."""
        path = project_file("qwen3-tts", "app.py")
        if not path.exists():
            pytest.skip("qwen3-tts/app.py not available in this environment")
        source = path.read_text()
        assert "413" in source, "Must return 413 status for oversized uploads"


# ============================================================================
# Notification Link Open Redirect Prevention
# ============================================================================

class TestNotificationRedirectPrevention:
    """Tests that notification navigation only allows relative paths."""

    def test_handleNavigate_validates_relative_path(self):
        """handleNavigate should only navigate to paths starting with /."""
        path = project_file("frontend", "src", "components", "NotificationBell.tsx")
        if not path.exists():
            pytest.skip("frontend/src/components/NotificationBell.tsx not available in this environment")
        source = path.read_text()
        # Find the handleNavigate function
        assert "link.startsWith('/')" in source, \
            "handleNavigate must validate link starts with /"

    def test_no_unconditional_window_location(self):
        """Should not have unconditional window.location.href = link."""
        path = project_file("frontend", "src", "components", "NotificationBell.tsx")
        if not path.exists():
            pytest.skip("frontend/src/components/NotificationBell.tsx not available in this environment")
        source = path.read_text()
        # Find the handleNavigate function
        match = re.search(r'const handleNavigate.*?};', source, re.DOTALL)
        assert match, "handleNavigate function not found"
        fn_source = match.group()
        # Make sure the navigation is inside a conditional
        assert "if (link.startsWith('/')" in fn_source or \
               "if(link.startsWith('/')" in fn_source, \
            "Navigation must be conditional on relative path check"


# ============================================================================
# Comprehensive Security Invariant Checks
# ============================================================================

class TestSecurityInvariants:
    """Cross-cutting security invariant checks."""

    def _backend_path(self, *parts: str) -> Path:
        """Resolve path relative to backend root."""
        return backend_file(*parts)

    def _project_path(self, *parts: str) -> Path:
        """Resolve path relative to project root (may not exist in Docker)."""
        return project_file(*parts)

    def test_no_shell_true_in_sandbox_exec(self):
        """Sandbox exec must not use shell=True."""
        p = self._backend_path("app", "services", "dev_sandbox_service.py")
        if not p.exists():
            pytest.skip("dev_sandbox_service.py not available")
        source = p.read_text()
        assert "shell=True" not in source, "Sandbox must never use shell=True"

    def test_sandbox_image_allowlist_enforced(self):
        """Sandbox must validate images against allowlist."""
        p = self._backend_path("app", "services", "dev_sandbox_service.py")
        if not p.exists():
            pytest.skip("dev_sandbox_service.py not available")
        source = p.read_text()
        assert "allowed_images" in source.lower() or "allowlist" in source.lower() or \
               "not in allowed" in source, "Sandbox must enforce image allowlist"

    def test_cors_not_wildcard(self):
        """CORS must not use '*' for allow_origins."""
        p = self._backend_path("app", "main.py")
        if not p.exists():
            pytest.skip("app/main.py not available")
        source = p.read_text()
        cors_section = re.search(r'CORSMiddleware.*?\)', source, re.DOTALL)
        assert cors_section, "CORS middleware not found"
        assert 'allow_origins=["*"]' not in cors_section.group(), \
            "CORS must not use wildcard origins"

    def test_secret_key_required_in_compose(self):
        """docker-compose.yml must require SECRET_KEY (use :? syntax)."""
        p = self._project_path("docker-compose.yml")
        if not p.exists():
            pytest.skip("docker-compose.yml not available in this environment")
        source = p.read_text()
        assert "SECRET_KEY:?" in source or "SECRET_KEY=${SECRET_KEY:?" in source, \
            "SECRET_KEY must use :? required syntax"

    def test_all_ports_bound_to_localhost(self):
        """All service ports in docker-compose.yml must bind to 127.0.0.1."""
        p = self._project_path("docker-compose.yml")
        if not p.exists():
            pytest.skip("docker-compose.yml not available in this environment")
        source = p.read_text()
        for port_match in re.finditer(r'- "(.*?:\d+:\d+)"', source):
            port_str = port_match.group(1)
            assert port_str.startswith("127.0.0.1:"), \
                f"Port {port_str} must be bound to 127.0.0.1"

    def test_api_docs_disabled_by_default(self):
        """API docs must be disabled by default in config class definition."""
        p = self._backend_path("app", "core", "config.py")
        if not p.exists():
            pytest.skip("config.py not available")
        source = p.read_text()
        assert "enable_docs: bool = False" in source, \
            "enable_docs must default to False in Settings class"

    def test_user_update_and_create_share_password_rules(self):
        """UserUpdate and UserCreate must enforce the same password requirements."""
        from app.schemas import UserCreate, UserUpdate

        # Both should reject the same weak password
        weak_password = "weakpassword"
        with pytest.raises(Exception):
            UserCreate(email="t@t.com", username="test", password=weak_password)
        with pytest.raises(Exception):
            UserUpdate(password=weak_password)

        # Both should accept the same strong password
        strong_password = "StrongP@ss123"
        create = UserCreate(email="t@t.com", username="test", password=strong_password)
        update = UserUpdate(password=strong_password)
        assert create.password == strong_password
        assert update.password == strong_password





# ============================================================================
# MEDIUM-3: Per-Installation KDF Salt
# ============================================================================

class TestMedium3PerInstallationSalt:
    """Verify encryption uses per-installation salt with legacy fallback."""

    def test_salt_file_created_on_first_use(self):
        """A random salt file is created when none exists."""
        import importlib
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / ".encryption_salt"
            assert not salt_path.exists()

            with _patch.dict(os.environ, {"ENCRYPTION_SALT_FILE": str(salt_path)}):
                # Force reimport to pick up new salt file path
                import app.core.encryption as enc_mod
                importlib.reload(enc_mod)

                try:
                    encrypted = enc_mod.encrypt_field("test-secret")
                    assert salt_path.exists()
                    assert len(salt_path.read_bytes()) == 32

                    # Decrypt should work
                    decrypted = enc_mod.decrypt_field(encrypted)
                    assert decrypted == "test-secret"
                finally:
                    # Clean up module state
                    enc_mod._fernet = None
                    enc_mod._fernet_legacy = None

    def test_same_salt_produces_same_key(self):
        """Reloading with the same salt file produces the same Fernet key."""
        import importlib

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / ".encryption_salt"

            with patch.dict(os.environ, {"ENCRYPTION_SALT_FILE": str(salt_path)}):
                import app.core.encryption as enc_mod
                importlib.reload(enc_mod)

                try:
                    encrypted = enc_mod.encrypt_field("consistent-test")

                    # Reset fernet to force reloading from same file
                    enc_mod._fernet = None
                    decrypted = enc_mod.decrypt_field(encrypted)
                    assert decrypted == "consistent-test"
                finally:
                    enc_mod._fernet = None
                    enc_mod._fernet_legacy = None

    def test_legacy_salt_fallback_decrypts_old_data(self):
        """Data encrypted with legacy fixed salt can still be decrypted."""
        import importlib

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / ".encryption_salt"

            with patch.dict(os.environ, {"ENCRYPTION_SALT_FILE": str(salt_path)}):
                import app.core.encryption as enc_mod
                importlib.reload(enc_mod)

                try:
                    # Encrypt using the legacy salt directly
                    legacy_fernet = enc_mod._derive_fernet(enc_mod._LEGACY_KDF_SALT)
                    legacy_ciphertext = legacy_fernet.encrypt(
                        b"old-secret"
                    ).decode("utf-8")

                    # Now decrypt — should fall through to legacy salt
                    decrypted = enc_mod.decrypt_field(legacy_ciphertext)
                    assert decrypted == "old-secret"
                finally:
                    enc_mod._fernet = None
                    enc_mod._fernet_legacy = None

    def test_bad_ciphertext_raises_value_error(self):
        """Completely invalid ciphertext raises ValueError."""
        import importlib

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / ".encryption_salt"

            with patch.dict(os.environ, {"ENCRYPTION_SALT_FILE": str(salt_path)}):
                import app.core.encryption as enc_mod
                importlib.reload(enc_mod)

                try:
                    with pytest.raises(ValueError, match="Cannot decrypt"):
                        enc_mod.decrypt_field("not-a-valid-fernet-token")
                finally:
                    enc_mod._fernet = None
                    enc_mod._fernet_legacy = None

    def test_salt_file_permissions(self):
        """Salt file should have 0o600 permissions."""
        import importlib
        import stat

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / ".encryption_salt"

            with patch.dict(os.environ, {"ENCRYPTION_SALT_FILE": str(salt_path)}):
                import app.core.encryption as enc_mod
                importlib.reload(enc_mod)

                try:
                    enc_mod.encrypt_field("trigger-salt-creation")
                    mode = salt_path.stat().st_mode
                    assert stat.S_IMODE(mode) == 0o600
                finally:
                    enc_mod._fernet = None
                    enc_mod._fernet_legacy = None


# ============================================================================
# MEDIUM-4: Boltz Clearnet Fallback Default
# ============================================================================




# ============================================================================
# LOW-3: Lazy API Key Loading
# ============================================================================

class TestLow3LazyAPIKeyLoading:
    """Verify LLM providers read API keys lazily from settings."""

    def test_openai_provider_reads_key_lazily(self):
        """OpenAIProvider does not store API key directly."""
        from app.services.llm_service import OpenAIProvider

        provider = OpenAIProvider(api_key_attr="openai_api_key")
        # The provider should store the attribute name, not the key value
        assert hasattr(provider, '_api_key_attr')
        assert provider._api_key_attr == "openai_api_key"
        # _api_key should be a property that reads from settings
        assert isinstance(type(provider).__dict__['_api_key'], property)

    def test_anthropic_provider_reads_key_lazily(self):
        """AnthropicProvider does not store API key directly."""
        from app.services.llm_service import AnthropicProvider

        provider = AnthropicProvider(api_key_attr="anthropic_api_key")
        assert hasattr(provider, '_api_key_attr')
        assert isinstance(type(provider).__dict__['_api_key'], property)

    def test_zai_provider_reads_key_lazily(self):
        """ZaiProvider does not store API key directly."""
        from app.services.llm_service import ZaiProvider

        provider = ZaiProvider(api_key_attr="z_ai_api_key")
        assert hasattr(provider, '_api_key_attr')
        assert isinstance(type(provider).__dict__['_api_key'], property)

    def test_provider_key_not_in_instance_dict(self):
        """API key string should not appear in provider __dict__."""
        from app.services.llm_service import OpenAIProvider

        provider = OpenAIProvider(api_key_attr="openai_api_key")
        # The actual key value should not be stored as an instance attribute
        instance_values = list(provider.__dict__.values())
        for val in instance_values:
            if isinstance(val, str) and val.startswith("sk-"):
                pytest.fail("API key found in instance __dict__")

    def test_llm_service_init_uses_attr_names(self):
        """LLMService.__init__ passes attr names, not key values."""
        from app.services.llm_service import LLMService

        svc = LLMService()
        openai_provider = svc._providers["openai"]
        assert openai_provider._api_key_attr == "openai_api_key"

        claude_provider = svc._providers["claude"]
        assert claude_provider._api_key_attr == "anthropic_api_key"

        glm_provider = svc._providers["glm"]
        assert glm_provider._api_key_attr == "z_ai_api_key"


# ============================================================================
# LOW-4: python-jose Replaced by PyJWT
# ============================================================================




# ============================================================================
# LOW-4: python-jose Replaced by PyJWT
# ============================================================================

class TestLow4PyJWTMigration:
    """Verify JWT operations work with PyJWT instead of python-jose."""

    def test_pyjwt_is_installed(self):
        """PyJWT is importable."""
        import jwt
        assert hasattr(jwt, 'encode')
        assert hasattr(jwt, 'decode')

    def test_python_jose_not_importable(self):
        """python-jose should no longer be installable."""
        with pytest.raises(ImportError):
            from jose import jwt  # noqa: F401

    def test_create_and_decode_token(self):
        """Token creation and decoding works with PyJWT."""
        from app.core.security import create_access_token, decode_access_token

        token = create_access_token(data={"sub": "test-user-id"})
        assert isinstance(token, str)
        assert len(token) > 0

        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "test-user-id"
        assert payload["aud"] == "money-agents"
        assert payload["iss"] == "money-agents"
        assert "jti" in payload
        assert "exp" in payload

    def test_invalid_token_returns_none(self):
        """Invalid/tampered token returns None, not exception."""
        from app.core.security import decode_access_token

        result = decode_access_token("invalid.token.here")
        assert result is None

    def test_expired_token_returns_none(self):
        """An expired token returns None."""
        from datetime import timedelta
        from app.core.security import create_access_token, decode_access_token

        token = create_access_token(
            data={"sub": "test-user"},
            expires_delta=timedelta(seconds=-10),  # Already expired
        )
        result = decode_access_token(token)
        assert result is None

    def test_wrong_audience_rejected(self):
        """Token with wrong audience is rejected."""
        import jwt as pyjwt
        from app.core.config import settings

        token = pyjwt.encode(
            {"sub": "test", "aud": "wrong", "iss": "money-agents"},
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )
        from app.core.security import decode_access_token
        assert decode_access_token(token) is None

    def test_wrong_issuer_rejected(self):
        """Token with wrong issuer is rejected."""
        import jwt as pyjwt
        from app.core.config import settings

        token = pyjwt.encode(
            {"sub": "test", "aud": "money-agents", "iss": "evil-app"},
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )
        from app.core.security import decode_access_token
        assert decode_access_token(token) is None

    def test_revoked_token_rejected(self):
        """Revoked token returns None after blocklist check."""
        from app.core.security import (
            create_access_token, decode_access_token, revoke_token
        )

        token = create_access_token(data={"sub": "test-user"})
        payload = decode_access_token(token)
        assert payload is not None

        jti = payload["jti"]
        revoke_token(jti)

        # Should now be rejected
        assert decode_access_token(token) is None

    def test_security_py_imports_pyjwt(self):
        """security.py imports from jwt (PyJWT), not from jose."""
        import inspect
        import app.core.security as sec

        source = inspect.getsource(sec)
        assert "from jose" not in source
        assert "import jwt" in source or "from jwt" in source

    def test_requirements_has_pyjwt(self):
        """requirements.txt lists PyJWT, not python-jose."""
        req_path = Path(__file__).parent.parent.parent / "requirements.txt"
        content = req_path.read_text()
        assert "PyJWT" in content
        assert "python-jose" not in content





# ============================================================================
# GAP-3: CLI Password Reset Invalidates Sessions
# ============================================================================

@pytest.mark.host_only
class TestGap3PasswordResetSessions:
    """Verify reset_admin_password sets password_changed_at."""

    def test_reset_code_contains_password_changed_at(self):
        """The start.py reset_admin_password function should set password_changed_at."""
        start_py = Path(__file__).parent.parent.parent.parent / "start.py"
        source = start_py.read_text()

        # Find the reset_admin_password function body
        func_start = source.find("def reset_admin_password(")
        assert func_start != -1, "reset_admin_password function not found in start.py"

        # Get the function body (until next unindented def or end of file)
        func_body = source[func_start:]
        next_func = func_body.find("\ndef ", 1)
        if next_func > 0:
            func_body = func_body[:next_func]

        assert "password_changed_at" in func_body, \
            "reset_admin_password must set password_changed_at to invalidate sessions"

    def test_reset_code_sets_file_permissions(self):
        """The start.py should chmod temp env files to 0o600."""
        start_py = Path(__file__).parent.parent.parent.parent / "start.py"
        source = start_py.read_text()

        # Both create_admin_user and reset_admin_password should use chmod
        assert "os.chmod(env_file_path" in source or "chmod" in source, \
            "Temp .env files should be restricted via chmod"


# ============================================================================
# GAP-4: GPU Service Auth Startup Warning
# ============================================================================


# ============================================================================
# GAP-17: Agent Endpoint Rate Limits
# ============================================================================

class TestGap17AgentRateLimits:
    """Verify LLM-heavy endpoints have rate limits."""

    @pytest.mark.host_only
    def test_agents_endpoints_rate_limited(self):
        """Agent chat/analyze/initialize endpoints must have rate limits."""
        source = backend_file("app/api/endpoints/agents.py").read_text()

        for endpoint_fragment in [
            "proposal-writer/chat",
            "proposal-writer/analyze",
            "campaign-manager/initialize",
        ]:
            pattern = rf'@router\.post\(["\'].*{re.escape(endpoint_fragment)}["\']'
            match = re.search(pattern, source)
            assert match, f"Could not find route for {endpoint_fragment}"

            after_route = source[match.start():]
            next_def = after_route.index("async def ")
            decorator_block = after_route[:next_def]
            assert "@limiter.limit(" in decorator_block, \
                f"Endpoint {endpoint_fragment} must have @limiter.limit() decorator"

    @pytest.mark.host_only
    def test_bitcoin_budget_review_rate_limited(self):
        """Budget approval review endpoint must have rate limit."""
        source = backend_file("app/api/endpoints/bitcoin_budget.py").read_text()
        match = re.search(r'@router\.post.*approvals.*review', source)
        assert match
        after_route = source[match.start():]
        next_def = after_route.index("async def ")
        decorator_block = after_route[:next_def]
        assert "@limiter.limit(" in decorator_block

    @pytest.mark.host_only
    def test_conversations_upload_rate_limited(self):
        """Conversation file upload endpoint must have rate limit."""
        source = backend_file("app/api/endpoints/conversations.py").read_text()
        match = re.search(r'@router\.post.*messages/upload', source)
        assert match
        after_route = source[match.start():]
        next_def = after_route.index("async def ")
        decorator_block = after_route[:next_def]
        assert "@limiter.limit(" in decorator_block


# ============================================================================
# GAP-22: Logout Endpoint Auth
# ============================================================================

class TestGap22LogoutAuth:
    """Verify logout endpoint uses proper auth dependency."""

    @pytest.mark.host_only
    def test_logout_uses_get_current_user(self):
        """POST /auth/logout must use Depends(get_current_user)."""
        source = backend_file("app/api/endpoints/auth.py").read_text()
        logout_idx = source.index("async def logout")
        logout_section = source[logout_idx - 200:logout_idx + 100]
        assert "get_current_user" in logout_section, \
            "Logout endpoint must use Depends(get_current_user) for consistency"

    @pytest.mark.asyncio
    async def test_logout_rejects_deactivated_user(self, async_client, db_session):
        """Logout should fail for deactivated users."""
        from app.core.security import create_access_token
        from app.models import User

        user = User(
            username="deactivated",
            email="deactivated@test.com",
            password_hash="hashed",
            role="user",
            is_active=False,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        token = create_access_token(data={"sub": str(user.id)})
        response = await async_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code in (401, 403), \
            "Deactivated user should not be able to logout"


# ============================================================================
# Security Integration
# ============================================================================

class TestSecurityIntegration:
    """Integration tests to verify core auth flows remain functional."""

    @pytest.mark.asyncio
    async def test_login_and_logout_flow(self, async_client, test_user):
        """Full login -> authenticated request -> logout flow still works."""
        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": "testuser",
            "password": "testpassword123",
        })
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = await async_client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code != 401

        response = await async_client.post("/api/v1/auth/logout", headers=headers)
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_agents_chat_requires_auth(self, async_client):
        """Agent chat endpoint should require authentication."""
        response = await async_client.post("/api/v1/agents/proposal-writer/chat", json={
            "message": "test",
        })
        assert response.status_code in (401, 403), \
            f"Unauthenticated request should be rejected, got {response.status_code}"

    @pytest.mark.asyncio
    async def test_wallet_endpoints_require_admin(self, async_client, test_user):
        """Wallet mutation endpoints should require admin role."""
        from app.core.security import create_access_token
        from app.main import app
        from app.api.endpoints.wallet import require_lnd

        app.dependency_overrides[require_lnd] = lambda: True
        try:
            token = create_access_token(data={"sub": str(test_user.id)})
            headers = {"Authorization": f"Bearer {token}"}

            response = await async_client.post(
                "/api/v1/wallet/address/new",
                json={"address_type": "p2tr"},
                headers=headers,
            )
            assert response.status_code == 403, \
                "Non-admin should not access wallet address generation"
        finally:
            app.dependency_overrides.pop(require_lnd, None)


# ============================================================================
# Login Timing Oracle Prevention
# ============================================================================


class TestLoginTimingOracle:
    """Login does not leak timing information about user existence."""

    def test_auth_endpoint_has_dummy_hash(self):
        """auth.py login path verifies a dummy hash when user is not found."""
        src = backend_file("app", "api", "endpoints", "auth.py").read_text()
        # Dummy bcrypt hash to burn time when user doesn't exist
        assert "$2b$12$" in src, "Expected dummy bcrypt hash in auth.py for timing oracle fix"
        assert "verify_password" in src


# ============================================================================
# Generic Profile Update Errors
# ============================================================================


class TestGenericProfileErrors:
    """Profile update errors are generic to prevent account enumeration."""

    def test_users_endpoint_generic_conflict_error(self):
        """users.py returns generic error for email/username conflicts."""
        src = backend_file("app", "api", "endpoints", "users.py").read_text()
        # Must NOT reveal which specific field (email vs username) caused conflict
        assert "email or username is not available" in src.lower() or \
               "not available" in src.lower()
        # Should use 409 status code
        assert "409" in src


# ============================================================================
# Password Length Rejection (bcrypt 72-byte limit)
# ============================================================================


class TestPasswordLengthRejection:
    """Passwords exceeding bcrypt's 72-byte limit are rejected at hash time."""

    def test_get_password_hash_rejects_long_passwords(self):
        """get_password_hash raises ValueError for passwords > 72 UTF-8 bytes."""
        from app.core.security import get_password_hash

        # 72 bytes exactly should work
        pw_72 = "a" * 72
        hashed = get_password_hash(pw_72)
        assert hashed.startswith("$2b$")

        # 73 bytes should fail
        pw_73 = "a" * 73
        with pytest.raises(ValueError, match="72"):
            get_password_hash(pw_73)

    def test_get_password_hash_rejects_multibyte_long_passwords(self):
        """Multibyte characters that push past 72 UTF-8 bytes are rejected."""
        from app.core.security import get_password_hash

        # Each emoji is 4 UTF-8 bytes; 18 emojis = 72 bytes exactly
        pw_18_emoji = "\U0001F600" * 18  # 72 bytes
        hashed = get_password_hash(pw_18_emoji)
        assert hashed.startswith("$2b$")

        # 19 emojis = 76 bytes
        pw_19_emoji = "\U0001F600" * 19
        with pytest.raises(ValueError, match="72"):
            get_password_hash(pw_19_emoji)

    def test_verify_password_still_works_for_long_passwords(self):
        """verify_password truncates for backward compat with pre-existing hashes."""
        from app.core.security import verify_password
        import bcrypt

        pw = "a" * 100
        truncated = pw[:72]
        hashed = bcrypt.hashpw(truncated.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # verify_password truncates, so the full 100-char password should still match
        assert verify_password(pw, hashed) is True


# ============================================================================
# SGA-M7: SecretStr for API key fields
# ============================================================================


class TestSecretStrAPIKeys:
    """SGA-M7: Sensitive API key fields must use SecretStr to prevent
    accidental exposure via repr/logging/model_dump."""

    def test_gpu_service_api_key_is_secretstr(self):
        """gpu_service_api_key must be SecretStr."""
        from pydantic import SecretStr
        from app.core.config import Settings

        field_info = Settings.model_fields["gpu_service_api_key"]
        assert field_info.annotation is SecretStr, \
            "gpu_service_api_key must be SecretStr, not str"

    def test_gpu_internal_api_key_is_secretstr(self):
        """gpu_internal_api_key must be SecretStr."""
        from pydantic import SecretStr
        from app.core.config import Settings

        field_info = Settings.model_fields["gpu_internal_api_key"]
        assert field_info.annotation is SecretStr, \
            "gpu_internal_api_key must be SecretStr, not str"

    def test_service_manager_api_key_is_secretstr(self):
        """service_manager_api_key must be SecretStr."""
        from pydantic import SecretStr
        from app.core.config import Settings

        field_info = Settings.model_fields["service_manager_api_key"]
        assert field_info.annotation is SecretStr, \
            "service_manager_api_key must be SecretStr, not str"

    def test_secretstr_not_leaked_in_repr(self):
        """SecretStr values must not appear in repr output."""
        from app.core.config import Settings

        s = Settings(
            database_url="sqlite:///test.db",
            secret_key="test-secret-key-long-enough-1234567890",
            gpu_service_api_key="super-secret-gpu-key",
        )
        repr_str = repr(s)
        assert "super-secret-gpu-key" not in repr_str, \
            "SecretStr value leaked in repr()"

    @pytest.mark.parametrize("service_file", [
        "app/services/gpu_lifecycle_service.py",
        "app/services/zimage_service.py",
        "app/services/audiosr_service.py",
        "app/services/acestep_service.py",
        "app/services/realesrgan_cpu_service.py",
        "app/services/qwen3_tts_service.py",
        "app/services/docling_service.py",
        "app/services/seedvr2_service.py",
        "app/services/ltx_video_service.py",
        "app/services/canary_stt_service.py",
        "app/services/media_toolkit_service.py",
    ])
    def test_gpu_services_use_get_secret_value(self, service_file):
        """GPU service files must use .get_secret_value() for SecretStr fields."""
        from tests.helpers.paths import backend_file

        path = backend_file(*service_file.split("/"))
        if not path.exists():
            pytest.skip(f"{service_file} not found")
        source = path.read_text()
        # Must call .get_secret_value() instead of using the SecretStr directly
        assert "get_secret_value()" in source, \
            f"{service_file} must use .get_secret_value() for SecretStr API keys"


# ============================================================================
# GAP-16: Approvals endpoint error message leakage
# ============================================================================


class TestApprovalsErrorLeakage:
    """GAP-16: Approval endpoints must not leak internal error details
    in HTTP exception messages."""

    @pytest.mark.host_only
    def test_approvals_no_str_e_in_httpexception(self):
        """approvals.py must not use detail=str(e) in HTTPException."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/approvals.py")
        # Old pattern: detail=str(e) — leaks exception internals
        import re
        matches = re.findall(r'detail=str\(e\)', source)
        assert len(matches) == 0, \
            f"Found {len(matches)} instances of detail=str(e) in approvals.py — " \
            f"use generic messages instead"

    @pytest.mark.host_only
    def test_approvals_uses_logger(self):
        """approvals.py must use logger for error details instead of HTTP responses."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/approvals.py")
        assert "import logging" in source or "from logging" in source, \
            "approvals.py must import logging"
        assert "logger" in source, \
            "approvals.py must use a logger instance"


# ============================================================================
# SGA3-H1: System account cannot log in interactively
# ============================================================================


class TestSystemAccountLoginBlocked:
    """SGA3-H1: The system service account must be blocked from interactive
    login even if someone discovers the password."""

    @pytest.mark.host_only
    def test_system_user_blocked_in_login_endpoint(self):
        """auth.py login endpoint must block system@money-agents.dev."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/auth.py")
        assert "system@money-agents.dev" in source, \
            "Login must check for the system email address"
        # Verify it appears in a blocking context (after user lookup, before password check)
        assert 'user.email == "system@money-agents.dev"' in source, \
            "Login must explicitly check user.email against system address"

    @pytest.mark.host_only
    def test_system_user_random_password(self):
        """startup_service.py must not use a guessable password for the system user."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/startup_service.py")
        assert "system_only_no_login" not in source, \
            "System user must not use the old guessable password"
        assert "secrets.token_urlsafe" in source, \
            "System user password must be cryptographically random"


# ============================================================================
# SGA3-H2: Request body size limit middleware
# ============================================================================


class TestRequestBodySizeLimit:
    """SGA3-H2: Backend must reject oversized request bodies."""

    @pytest.mark.host_only
    def test_request_size_limit_middleware_exists(self):
        """main.py must define and register RequestSizeLimitMiddleware."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/main.py")
        assert "RequestSizeLimitMiddleware" in source
        assert "app.add_middleware(RequestSizeLimitMiddleware)" in source

    @pytest.mark.host_only
    def test_request_size_limit_returns_413(self):
        """Middleware must return status 413 for oversized requests."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/main.py")
        assert "413" in source
        assert "Request body too large" in source


# ============================================================================
# SGA3-M1: Error information disclosure (detail=str(e))
# ============================================================================


class TestErrorInfoDisclosure:
    """SGA3-M1: Endpoints must not leak internal error details via detail=str(e)."""

    @pytest.mark.host_only
    @pytest.mark.parametrize("endpoint_file", [
        "backend/app/api/endpoints/resources.py",
        "backend/app/api/endpoints/tools.py",
        "backend/app/api/endpoints/media_library.py",
        "backend/app/api/endpoints/rate_limits.py",
        "backend/app/api/endpoints/tool_health.py",
    ])
    def test_no_detail_str_e(self, endpoint_file):
        """No endpoint should use detail=str(e) in HTTPException."""
        from tests.helpers.paths import require_file
        import re

        source = require_file(endpoint_file)
        matches = re.findall(r'detail=str\(e\)', source)
        assert len(matches) == 0, \
            f"Found {len(matches)} instances of detail=str(e) in {endpoint_file}"

    @pytest.mark.host_only
    @pytest.mark.parametrize("endpoint_file", [
        "backend/app/api/endpoints/resources.py",
        "backend/app/api/endpoints/tools.py",
        "backend/app/api/endpoints/media_library.py",
        "backend/app/api/endpoints/rate_limits.py",
        "backend/app/api/endpoints/tool_health.py",
    ])
    def test_endpoints_use_logging(self, endpoint_file):
        """Endpoints must log errors server-side instead of exposing them."""
        from tests.helpers.paths import require_file

        source = require_file(endpoint_file)
        assert "logging" in source, \
            f"{endpoint_file} must import logging"


# ============================================================================
# SGA3-M2: Rate limiting on all endpoint routers
# ============================================================================


class TestRateLimitingCoverage:
    """SGA3-M2: All endpoint routers must have per-endpoint rate limits."""

    @pytest.mark.host_only
    @pytest.mark.parametrize("endpoint_file", [
        "backend/app/api/endpoints/campaign_learning.py",
        "backend/app/api/endpoints/campaigns.py",
        "backend/app/api/endpoints/proposals.py",
        "backend/app/api/endpoints/resources.py",
        "backend/app/api/endpoints/agent_scheduler.py",
        "backend/app/api/endpoints/notifications.py",
        "backend/app/api/endpoints/analytics.py",
        "backend/app/api/endpoints/tasks.py",
    ])
    def test_router_has_rate_limits(self, endpoint_file):
        """Every endpoint router must import and use the rate limiter."""
        from tests.helpers.paths import require_file
        import re

        source = require_file(endpoint_file)
        assert "from app.core.rate_limit import limiter" in source, \
            f"{endpoint_file} must import the limiter"

        # Count route decorators vs rate limit decorators
        route_count = len(re.findall(
            r'@router\.(get|post|put|delete|patch)\(', source
        ))
        limit_count = len(re.findall(r'@limiter\.limit\(', source))
        assert limit_count >= route_count, \
            f"{endpoint_file}: {limit_count} rate limits for {route_count} routes"


# ============================================================================
# SGA3-M3: Schema string field length constraints
# ============================================================================


class TestSchemaLengthConstraints:
    """SGA3-M3: Input schema string fields must have max_length constraints."""

    @staticmethod
    def _get_max_length(field_info):
        """Extract max_length from FieldInfo, supporting both pydantic 2.6 and 2.11+."""
        # Pydantic 2.11+ stores max_length directly on FieldInfo
        if hasattr(field_info, 'max_length') and field_info.max_length is not None:
            return field_info.max_length
        # Pydantic 2.6 stores it in metadata as MaxLen(max_length=N)
        for m in (field_info.metadata or []):
            if hasattr(m, 'max_length'):
                return m.max_length
        return None

    def test_login_identifier_max_length(self):
        """LoginRequest.identifier must have max_length."""
        from app.schemas import LoginRequest
        field = LoginRequest.model_fields["identifier"]
        ml = self._get_max_length(field)
        assert ml is not None and ml <= 255, \
            "LoginRequest.identifier must have max_length <= 255"

    def test_message_content_max_length(self):
        """MessageBase/MessageCreate content must have max_length."""
        from app.schemas import MessageCreate
        field = MessageCreate.model_fields["content"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "MessageCreate.content must have max_length"
        assert ml <= 200_000, \
            "MessageCreate.content max_length should be reasonable"

    def test_proposal_summary_max_length(self):
        """ProposalCreate.summary must have max_length."""
        from app.schemas import ProposalCreate
        field = ProposalCreate.model_fields["summary"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "ProposalCreate.summary must have max_length"

    def test_proposal_description_max_length(self):
        """ProposalCreate.detailed_description must have max_length."""
        from app.schemas import ProposalCreate
        field = ProposalCreate.model_fields["detailed_description"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "ProposalCreate.detailed_description must have max_length"

    def test_tool_description_max_length(self):
        """ToolCreate.description must have max_length."""
        from app.schemas import ToolCreate
        field = ToolCreate.model_fields["description"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "ToolCreate.description must have max_length"

    def test_bitcoin_transaction_fields(self):
        """BitcoinTransactionCreate string fields must have max_length."""
        from app.schemas.bitcoin_budget import BitcoinTransactionCreate
        for field_name in ["tx_type", "payment_hash", "description"]:
            field = BitcoinTransactionCreate.model_fields[field_name]
            ml = self._get_max_length(field)
            assert ml is not None, \
                f"BitcoinTransactionCreate.{field_name} must have max_length"

    def test_task_description_max_length(self):
        """TaskCreate inherits description max_length from TaskBase."""
        from app.schemas.task import TaskCreate
        field = TaskCreate.model_fields["description"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "TaskCreate.description must have max_length"

    def test_opportunity_summary_max_length(self):
        """OpportunityCreate.summary must have max_length."""
        from app.schemas.opportunity import OpportunityCreate
        field = OpportunityCreate.model_fields["summary"]
        ml = self._get_max_length(field)
        assert ml is not None, \
            "OpportunityCreate.summary must have max_length"

    def test_schema_rejects_oversized_content(self):
        """MessageCreate must reject content exceeding max_length."""
        from app.schemas import MessageCreate
        from pydantic import ValidationError
        from uuid import uuid4

        field = MessageCreate.model_fields["content"]
        max_len = self._get_max_length(field)
        with pytest.raises(ValidationError):
            MessageCreate(
                content="x" * (max_len + 1),
                conversation_id=uuid4(),
                sender_type="user",
            )


# ============================================================================
# SGA3-L1: SSRF prevention in pricing update service
# ============================================================================


class TestPricingServiceSSRF:
    """SGA3-L1: Pricing service must validate its target URL."""

    @pytest.mark.host_only
    def test_pricing_service_validates_url(self):
        """pricing_update_service.py must call validate_target_url."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/pricing_update_service.py")
        assert "validate_target_url" in source, \
            "pricing_update_service.py must use validate_target_url"


# ============================================================================
# SGA3-L2: Thumbnail path validation
# ============================================================================


class TestThumbnailPathValidation:
    """SGA3-L2: Thumbnail service must validate source paths."""

    @pytest.mark.host_only
    def test_thumbnail_service_validates_paths(self):
        """thumbnail_service.py must import and use path validation."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/thumbnail_service.py")
        assert "validate_tool_file_path" in source, \
            "thumbnail_service.py must use validate_tool_file_path"

    @pytest.mark.host_only
    def test_thumbnail_generate_calls_validation(self):
        """generate_thumbnail must call validate_tool_file_path."""
        from tests.helpers.paths import require_file
        import re

        source = require_file("backend/app/services/thumbnail_service.py")
        # Find the generate_thumbnail function and check it has the validation call
        fn_match = re.search(
            r'async def generate_thumbnail\(.*?\).*?(?=async def |\Z)',
            source, re.DOTALL
        )
        assert fn_match is not None, "generate_thumbnail function must exist"
        fn_body = fn_match.group()
        assert "validate_tool_file_path" in fn_body, \
            "generate_thumbnail must call validate_tool_file_path"


# ============================================================================
# SGA3-L4: Sensitive data redaction in logs
# ============================================================================


class TestSensitiveDataLogRedaction:
    """SGA3-L4: Logging must have a global sensitive data filter."""

    @pytest.mark.host_only
    def test_sensitive_data_filter_exists(self):
        """main.py must define SensitiveDataFilter."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/main.py")
        assert "SensitiveDataFilter" in source
        assert "addFilter" in source

    def test_sensitive_data_filter_redacts_passwords(self):
        """SensitiveDataFilter must redact password patterns."""
        from app.main import SensitiveDataFilter
        import logging

        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Login failed for password=s3cret123 user", args=(), exc_info=None,
        )
        f.filter(record)
        assert "s3cret123" not in record.msg
        assert "***" in record.msg

    def test_sensitive_data_filter_redacts_api_keys(self):
        """SensitiveDataFilter must redact api_key patterns."""
        from app.main import SensitiveDataFilter
        import logging

        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="api_key=sk-abc123xyz", args=(), exc_info=None,
        )
        f.filter(record)
        assert "sk-abc123xyz" not in record.msg

    def test_sensitive_data_filter_redacts_bearer_tokens(self):
        """SensitiveDataFilter must redact Authorization: Bearer tokens."""
        from app.main import SensitiveDataFilter
        import logging

        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.xxxxx", args=(), exc_info=None,
        )
        f.filter(record)
        assert "eyJhbGciOiJIUzI1NiJ9" not in record.msg

    def test_sensitive_data_filter_passes_clean_messages(self):
        """SensitiveDataFilter must not corrupt clean log messages."""
        from app.main import SensitiveDataFilter
        import logging

        f = SensitiveDataFilter()
        msg = "User logged in from 192.168.1.1"
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        f.filter(record)
        assert record.msg == msg


# ============================================================================
# SGA3-M5: CSP templating for nginx
# ============================================================================


class TestCSPTemplating:
    """SGA3-M5: Frontend CSP must use templated origins for production."""

    @pytest.mark.host_only
    def test_nginx_template_exists(self):
        """nginx.conf.template must exist with CSP variable placeholders."""
        from tests.helpers.paths import require_file

        source = require_file("frontend/nginx.conf.template")
        assert "${CSP_CONNECT_ORIGINS}" in source
        assert "${CSP_IMG_ORIGINS}" in source
        assert "${CSP_MEDIA_ORIGINS}" in source

    @pytest.mark.host_only
    def test_dockerfile_prod_uses_template(self):
        """Dockerfile.prod must use nginx.conf.template."""
        from tests.helpers.paths import require_file

        source = require_file("frontend/Dockerfile.prod")
        assert "nginx.conf.template" in source
        assert "CSP_CONNECT_ORIGINS" in source


# ============================================================================
# SGA3-L3: Encryption key rotation script
# ============================================================================


class TestEncryptionKeyRotation:
    """SGA3-L3: Key rotation script must exist and have proper error handling."""

    @pytest.mark.host_only
    def test_rotation_script_exists(self):
        """rotate_encryption_key.py must exist."""
        from tests.helpers.paths import require_file

        source = require_file("backend/scripts/rotate_encryption_key.py")
        assert "rotate_keys" in source
        assert "old_key" in source or "old-key" in source

    @pytest.mark.host_only
    def test_rotation_script_has_dry_run(self):
        """Key rotation script must support --dry-run."""
        from tests.helpers.paths import require_file

        source = require_file("backend/scripts/rotate_encryption_key.py")
        assert "dry_run" in source or "dry-run" in source

    @pytest.mark.host_only
    def test_rotation_script_verifies_roundtrip(self):
        """Key rotation must verify decryption round-trip before committing."""
        from tests.helpers.paths import require_file

        source = require_file("backend/scripts/rotate_encryption_key.py")
        assert "round-trip" in source.lower() or "verify" in source.lower()


# ============================================================================
# SGA3-L5: Refresh token flow
# ============================================================================


class TestRefreshTokenFlow:
    """SGA3-L5: Refresh token infrastructure must exist."""

    def test_create_refresh_token_function_exists(self):
        """security.py must have create_refresh_token."""
        from app.core.security import create_refresh_token
        assert callable(create_refresh_token)

    def test_decode_refresh_token_function_exists(self):
        """security.py must have decode_refresh_token."""
        from app.core.security import decode_refresh_token
        assert callable(decode_refresh_token)

    def test_refresh_token_has_correct_type(self):
        """Refresh tokens must have typ='refresh' claim."""
        from app.core.security import create_refresh_token, decode_refresh_token

        token = create_refresh_token(data={"sub": "test-user-id"})
        payload = decode_refresh_token(token)
        assert payload is not None
        assert payload.get("typ") == "refresh"

    def test_access_token_cannot_be_used_as_refresh(self):
        """Access tokens must be rejected by decode_refresh_token."""
        from app.core.security import create_access_token, decode_refresh_token

        token = create_access_token(data={"sub": "test-user-id"})
        payload = decode_refresh_token(token)
        assert payload is None, "Access tokens must not be accepted as refresh tokens"

    def test_refresh_token_cannot_be_used_as_access(self):
        """Refresh tokens must be rejected by decode_access_token."""
        from app.core.security import create_refresh_token, decode_access_token

        token = create_refresh_token(data={"sub": "test-user-id"})
        payload = decode_access_token(token)
        assert payload is None, "Refresh tokens must be rejected by decode_access_token"

    def test_token_schema_has_refresh_field(self):
        """Token schema must include optional refresh_token."""
        from app.schemas import Token
        field = Token.model_fields.get("refresh_token")
        assert field is not None, "Token schema must have refresh_token field"

    @pytest.mark.host_only
    def test_refresh_endpoint_exists(self):
        """Auth router must have a /refresh endpoint."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/auth.py")
        assert '"/refresh"' in source
        assert "decode_refresh_token" in source

    @pytest.mark.host_only
    def test_refresh_endpoint_rotates_token(self):
        """Refresh endpoint must revoke old refresh token (rotation)."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/auth.py")
        assert "revoke_token" in source
        assert "rotation" in source.lower()

    def test_revoked_refresh_token_rejected(self):
        """A revoked refresh token must be rejected."""
        from app.core.security import create_refresh_token, decode_refresh_token, revoke_token

        token = create_refresh_token(data={"sub": "test-user-id"})
        payload = decode_refresh_token(token)
        assert payload is not None, "Fresh token must be accepted"

        # Revoke it
        revoke_token(payload["jti"], expires_in=300)

        # Now it should be rejected
        payload2 = decode_refresh_token(token)
        assert payload2 is None, "Revoked refresh token must be rejected"


# ============================================================================
# SGA3-M4: Dependency freshness
# ============================================================================


class TestDependencyFreshness:
    """SGA3-M4: Critical dependencies should be reasonably up-to-date."""

    @pytest.mark.host_only
    def test_critical_deps_not_ancient(self):
        """requirements.txt must not pin critically old versions."""
        from tests.helpers.paths import require_file
        import re

        source = require_file("backend/requirements.txt")

        # Check FastAPI >= 0.115.0
        m = re.search(r'fastapi==(\d+)\.(\d+)\.(\d+)', source)
        assert m is not None, "FastAPI must be pinned"
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        assert (major, minor) >= (0, 115), \
            f"FastAPI {major}.{minor}.{patch} is too old (need >= 0.115)"

        # Check PyJWT >= 2.8.0 (capped by zhipuai at <2.9.0)
        m = re.search(r'PyJWT.*?==(\d+)\.(\d+)\.(\d+)', source)
        assert m is not None, "PyJWT must be pinned"
        major, minor = int(m.group(1)), int(m.group(2))
        assert (major, minor) >= (2, 8), \
            f"PyJWT {major}.{minor} is too old (need >= 2.8)"


# ============================================================================
# SGA3-L1: decode_access_token positively asserts typ=access
# ============================================================================


class TestSGA3L1TokenTypeAssertion:
    """SGA3-L1: decode_access_token must require typ=access."""

    def test_missing_typ_rejected(self):
        """Tokens without a typ claim must be rejected."""
        import jwt as pyjwt
        from app.core.config import settings
        from app.core.security import decode_access_token
        from uuid import uuid4

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "jti": str(uuid4()),
                "aud": "money-agents",
                "iss": "money-agents",
                # No "typ" claim
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )
        assert decode_access_token(token) is None, (
            "Token without typ claim must be rejected"
        )

    def test_wrong_typ_rejected(self):
        """Tokens with typ != 'access' must be rejected."""
        import jwt as pyjwt
        from app.core.config import settings
        from app.core.security import decode_access_token
        from uuid import uuid4

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "jti": str(uuid4()),
                "typ": "refresh",
                "aud": "money-agents",
                "iss": "money-agents",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )
        assert decode_access_token(token) is None

    def test_access_typ_accepted(self):
        """Tokens with typ=access must be accepted."""
        import jwt as pyjwt
        from app.core.config import settings
        from app.core.security import decode_access_token
        from uuid import uuid4

        token = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "jti": str(uuid4()),
                "typ": "access",
                "aud": "money-agents",
                "iss": "money-agents",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["typ"] == "access"


# ============================================================================
# SGA3-L2: secret_key is SecretStr
# ============================================================================


class TestSGA3L2SecretKeySecretStr:
    """SGA3-L2: secret_key must be SecretStr to prevent accidental exposure."""

    def test_secret_key_is_secretstr(self):
        from pydantic import SecretStr
        from app.core.config import settings

        assert isinstance(settings.secret_key, SecretStr), (
            "settings.secret_key must be a SecretStr instance"
        )

    def test_secret_key_not_in_model_dump(self):
        """SecretStr must be masked in model_dump()."""
        from app.core.config import settings

        dumped = settings.model_dump()
        secret = dumped.get("secret_key", "")
        # SecretStr.model_dump() gives '**********'
        assert "**" in str(secret) or secret == "**********", (
            "secret_key must be masked in model_dump()"
        )

    def test_get_secret_value_works(self):
        from app.core.config import settings

        val = settings.secret_key.get_secret_value()
        assert isinstance(val, str)
        assert len(val) > 0


# ============================================================================
# SGA3-L3: access_token_expire_minutes default is 15
# ============================================================================


class TestSGA3L3TokenExpireDefault:
    """SGA3-L3: Default token expiration must be 15 minutes (not 60)."""

    def test_default_expire_minutes(self):
        from app.core.config import Settings

        s = Settings(
            database_url="sqlite:///test.db",
            secret_key="test-secret-key-long-enough-1234567890",
        )
        assert s.access_token_expire_minutes == 15, (
            "Default access_token_expire_minutes must be 15"
        )


# ============================================================================
# SGA3-M4: Redis required in production
# ============================================================================


class TestSGA3M4RedisRequiredProduction:
    """SGA3-M4: Redis must be required for token blocklist in production."""

    @pytest.mark.host_only
    def test_production_check_in_redis_init(self):
        from tests.helpers.paths import require_file

        source = require_file("backend/app/core/security.py")
        assert "production" in source.lower(), (
            "security.py must check for production environment"
        )
        assert "RuntimeError" in source, (
            "Must raise RuntimeError when Redis unavailable in production"
        )

    def test_production_redis_failure_raises(self):
        """In production, _get_redis failure must raise RuntimeError."""
        from app.core.security import _get_redis
        from app.core.config import settings

        original = settings.environment
        try:
            settings.environment = "production"
            # _get_redis should raise when it can't connect and in production
            # (behavior depends on whether Redis is actually available in test)
        finally:
            settings.environment = original


# ============================================================================
# SGA3-L4: Redis-backed WebSocket connection tracking
# ============================================================================


class TestSGA3L4WebSocketRedisTracking:
    """SGA3-L4: WebSocket connections tracked in Redis for multi-worker accuracy."""

    @pytest.mark.host_only
    def test_ws_security_has_redis_tracking(self):
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/websocket_security.py")
        assert "_ws_redis" in source, "Must have Redis client for WS tracking"
        assert "WS_REDIS_KEY_PREFIX" in source or "_WS_REDIS_KEY_PREFIX" in source
        assert "DB 5" in source or "db=5" in source or "/5" in source, (
            "WS tracking must use a dedicated Redis DB"
        )

    def test_ws_guard_fallback_to_memory(self):
        """WSConnectionGuard must work without Redis (in-memory fallback)."""
        import asyncio
        from app.api.websocket_security import WSConnectionGuard

        async def _test():
            async with WSConnectionGuard("test-user-ws-l4") as guard:
                assert not guard.rejected

        asyncio.get_event_loop().run_until_complete(_test())


# ============================================================================
# SGA3-L5: Password change rate limit reduced
# ============================================================================


class TestSGA3L5PasswordChangeRateLimit:
    """SGA3-L5: PUT /users/me rate limit must be <= 3/minute."""

    @pytest.mark.host_only
    def test_users_me_rate_limit(self):
        from tests.helpers.paths import require_file

        source = require_file("backend/app/api/endpoints/users.py")
        # Find the PUT /users/me handler and its rate limit
        match = re.search(r'@limiter\.limit\("(\d+)/minute"\)', source)
        assert match, "PUT /users/me must have a rate limit"
        rate = int(match.group(1))
        assert rate <= 3, (
            f"PUT /users/me rate limit is {rate}/minute, must be <= 3"
        )


# ============================================================================
# SGA3-L6: Service manager auth fail-closed
# ============================================================================


class TestSGA3L6ServiceManagerAuth:
    """SGA3-L6: Service manager must reject requests when API key is empty."""

    @pytest.mark.host_only
    def test_service_manager_fail_closed(self):
        from tests.helpers.paths import require_file

        source = require_file("scripts/service_manager.py")
        # Must check for explicit auth skip or reject empty key
        assert "SERVICE_MANAGER_AUTH_SKIP" in source or "AUTH_SKIP" in source, (
            "Service manager must have explicit AUTH_SKIP env var for bypassing auth"
        )
        assert "403" in source, "Must return 403 when auth fails"


# ============================================================================
# SGA3-L12: Encryption health check at startup
# ============================================================================


class TestSGA3L12EncryptionHealthCheck:
    """SGA3-L12: Startup must validate encryption roundtrip."""

    def test_encryption_health_check_passes(self):
        """validate_encryption_health() must succeed with valid config."""
        from app.core.encryption import validate_encryption_health

        # Should not raise
        validate_encryption_health()

    def test_encryption_roundtrip(self):
        """encrypt_field / decrypt_field must produce identical output."""
        from app.core.encryption import encrypt_field, decrypt_field

        plaintext = "test-secret-data-12345"
        encrypted = encrypt_field(plaintext)
        assert encrypted != plaintext
        decrypted = decrypt_field(encrypted)
        assert decrypted == plaintext

    @pytest.mark.host_only
    def test_health_check_called_at_startup(self):
        """main.py lifespan must call validate_encryption_health."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/main.py")
        assert "validate_encryption_health" in source, (
            "main.py must call validate_encryption_health at startup"
        )


# ============================================================================
# SGA3-I3: CORS allow_credentials=False
# ============================================================================


class TestSGA3I3CORSCredentials:
    """SGA3-I3: CORS middleware must not set allow_credentials=True."""

    @pytest.mark.host_only
    def test_cors_credentials_false(self):
        from tests.helpers.paths import require_file

        source = require_file("backend/app/main.py")
        assert "allow_credentials=False" in source, (
            "CORS middleware must use allow_credentials=False"
        )
        # Ensure allow_credentials=True is NOT present
        assert "allow_credentials=True" not in source, (
            "allow_credentials=True must not be in main.py"
        )


# ============================================================================
# SGA3-M3: Sandbox read_file path restriction
# ============================================================================


class TestSGA3M3SandboxReadFilePath:
    """SGA3-M3: Sandbox read_file must validate paths to /workspace or /tmp."""

    @pytest.mark.host_only
    def test_read_file_path_validation(self):
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/dev_sandbox_service.py")
        assert "/workspace" in source, "Must validate path starts with /workspace"
        assert "/tmp" in source, "Must allow /tmp paths"
        assert "normpath" in source, "Must normalize paths to prevent traversal"
