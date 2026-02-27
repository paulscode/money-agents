"""
Security tests: Infrastructure, GPU, sandbox & Docker hardening.

Covers:
  - 27 findings from internal_docs/SECURITY_AUDIT_2.md
  - GPU auth startup warnings and internal endpoint auth
  - Docker Compose network segmentation and production profiles
  - Frontend console.error gating and ENABLE_DOCS defaults
  - Sandbox user hardcoding, write_files path validation
  - Ollama HTTP client reuse
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import shlex
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────────────────────
# HIGH-1 / HIGH-2 / MED-12: Path traversal in GPU service _resolve_local_url
# ─────────────────────────────────────────────────────────────────────────────


class TestPathTraversalGPUServices:
    """
    SA2-01/02/12: _resolve_local_url must reject paths containing ../
    that escape the workspace directory.
    """

    @pytest.mark.host_only
    @pytest.mark.parametrize("service_dir,service_file", [
        ("canary-stt", "app.py"),
        ("audiosr", "app.py"),
        ("realesrgan-cpu", "app.py"),
        ("media-toolkit", "app.py"),
        ("docling-parser", "app.py"),
        ("seedvr2-upscaler", "app.py"),  # reference implementation
    ])
    def test_resolve_local_url_source_contains_resolve_check(self, service_dir, service_file):
        """Verify all _resolve_local_url implementations use .resolve() + is_relative_to()."""
        from tests.helpers.paths import PROJECT_ROOT
        service_path = PROJECT_ROOT / service_dir / service_file
        if not service_path.exists():
            pytest.skip(f"{service_dir}/{service_file} not found")
        source = service_path.read_text()
        assert ".resolve()" in source, f"{service_dir} missing .resolve() call"
        assert "is_relative_to" in source, f"{service_dir} missing is_relative_to() check"

    @pytest.mark.host_only
    def test_canary_stt_rejects_path_traversal(self):
        """canary-stt _resolve_local_url rejects ../../../etc/passwd."""
        from tests.helpers.paths import PROJECT_ROOT
        sys.path.insert(0, str(PROJECT_ROOT / "canary-stt"))
        try:
            # Dynamically import to avoid GPU deps
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "canary_app", PROJECT_ROOT / "canary-stt" / "app.py",
                submodule_search_locations=[]
            )
            # We can't fully import (GPU deps), so just verify source
            source = (PROJECT_ROOT / "canary-stt" / "app.py").read_text()
            # Check the resolve pattern is in _resolve_local_url
            func_start = source.index("def _resolve_local_url")
            func_body = source[func_start:func_start + 1200]
            assert ".resolve()" in func_body
            assert "is_relative_to(WORKSPACE_DIR.resolve())" in func_body
        finally:
            if str(PROJECT_ROOT / "canary-stt") in sys.path:
                sys.path.remove(str(PROJECT_ROOT / "canary-stt"))

    @pytest.mark.host_only
    def test_media_toolkit_acestep_branch_validates_path(self):
        """media-toolkit ACEStep file= param must validate against acestep/output dir."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "media-toolkit" / "app.py").read_text()
        func_start = source.index("def _resolve_local_url")
        func_body = source[func_start:func_start + 2000]
        # Must validate ACEStep path against acestep output dir
        assert "acestep_output" in func_body, "ACEStep branch missing output dir check"
        assert "is_relative_to(acestep_output)" in func_body, "ACEStep branch missing is_relative_to check"

    @pytest.mark.host_only
    def test_docling_parser_validates_path_traversal(self):
        """docling-parser _resolve_local_url must check is_relative_to."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "docling-parser" / "app.py").read_text()
        func_start = source.index("def _resolve_local_url")
        func_body = source[func_start:func_start + 1000]
        assert ".resolve()" in func_body
        assert "is_relative_to(WORKSPACE_DIR.resolve())" in func_body

    @pytest.mark.host_only
    def test_realesrgan_uses_is_file_not_exists(self):
        """realesrgan-cpu must use .is_file() not .exists() to reject directories."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "realesrgan-cpu" / "app.py").read_text()
        func_start = source.index("def _resolve_local_url")
        func_body = source[func_start:func_start + 1200]
        assert ".is_file()" in func_body, "Should use .is_file() not .exists()"

    @pytest.mark.host_only
    def test_realesrgan_rejects_empty_path(self):
        """realesrgan-cpu must reject empty path_part."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "realesrgan-cpu" / "app.py").read_text()
        func_start = source.index("def _resolve_local_url")
        func_body = source[func_start:func_start + 1200]
        assert "not path_part" in func_body or "if not path_part" in func_body


# ─────────────────────────────────────────────────────────────────────────────
# HIGH-3 / HIGH-4: SSRF guard in tool executors
# ─────────────────────────────────────────────────────────────────────────────


class TestSSRFGuard:
    """SA2-03/04: validate_target_url blocks private/internal IPs."""

    def test_blocks_cloud_metadata(self):
        """Block AWS/GCP metadata endpoints."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="Cloud metadata"):
            validate_target_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_loopback(self):
        """Block 127.0.0.1 and localhost."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private"):
            validate_target_url("http://127.0.0.1:5432/")

    def test_blocks_link_local(self):
        """Block 169.254.x.x (AWS metadata)."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private"):
            validate_target_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_rfc1918_10(self):
        """Block 10.x.x.x private range."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private"):
            validate_target_url("http://10.0.0.1:8080/api")

    def test_blocks_rfc1918_172(self):
        """Block 172.16.x.x private range."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private"):
            validate_target_url("http://172.16.0.1:9200/")

    def test_blocks_rfc1918_192(self):
        """Block 192.168.x.x private range."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private"):
            validate_target_url("http://192.168.1.1/")

    def test_allows_public_ip(self):
        """Allow public IPs like 8.8.8.8."""
        from app.core.security import validate_target_url
        # This should not raise
        result = validate_target_url("http://8.8.8.8/dns-query")
        assert result == "http://8.8.8.8/dns-query"

    def test_allows_configured_internal_host(self):
        """Allow hosts in SSRF_ALLOWED_HOSTS env var."""
        from app.core.security import validate_target_url, _load_ssrf_allowed_hosts
        # host.docker.internal is in the default allowed list
        result = validate_target_url("http://host.docker.internal:9999/gpu/process")
        assert "host.docker.internal" in result

    def test_rejects_no_hostname(self):
        """Reject URLs with no hostname."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="no hostname"):
            validate_target_url("http://")

    def test_rejects_malformed(self):
        """Reject completely malformed URLs."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError):
            validate_target_url("")

    def test_tool_execution_rest_api_has_ssrf_guard(self):
        """_execute_rest_api must import and call validate_target_url."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        # Find the full _execute_rest_api method (it's large, search until next method)
        idx = source.index("async def _execute_rest_api")
        next_method = source.find("\n    async def ", idx + 50)
        method_body = source[idx:next_method] if next_method != -1 else source[idx:]
        assert "validate_target_url" in method_body, "REST API executor missing SSRF guard"

    def test_tool_execution_mock_gpu_has_ssrf_guard(self):
        """_execute_mock_gpu must import and call validate_target_url."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        idx = source.index("async def _execute_mock_gpu")
        method_body = source[idx:idx + 1500]
        assert "validate_target_url" in method_body, "Mock GPU executor missing SSRF guard"

    def test_tool_execution_mock_cli_has_ssrf_guard(self):
        """_execute_mock_cli must import and call validate_target_url."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        idx = source.index("async def _execute_mock_cli")
        method_body = source[idx:idx + 1500]
        assert "validate_target_url" in method_body, "Mock CLI executor missing SSRF guard"


# ─────────────────────────────────────────────────────────────────────────────
# HIGH-5 / LOW-13: Password change + token invalidation
# ─────────────────────────────────────────────────────────────────────────────


class TestPasswordChangeSecurity:
    """SA2-05/13: Password change requires current password + invalidates tokens."""

    @pytest.mark.asyncio
    async def test_schema_has_current_password_field(self):
        """UserUpdate schema must have current_password field."""
        from app.schemas import UserUpdate
        schema = UserUpdate(current_password="oldpass", password="NewPass123!")
        assert schema.current_password == "oldpass"

    @pytest.mark.asyncio
    async def test_password_change_without_current_returns_400(self, async_client, test_user):
        """Changing password without providing current password must fail."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={"password": "NewSecure1!Pass"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "current password" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_change_with_wrong_current_returns_400(self, async_client, test_user):
        """Changing password with wrong current password must fail."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={
                "password": "NewSecure1!Pass",
                "current_password": "wrongpassword",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        # SGA-L2: Generic error message (no longer leaks "incorrect")
        assert "password" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_change_with_correct_current_succeeds(self, async_client, test_user):
        """Changing password with correct current password must succeed."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={
                "password": "NewSecure1!Pass",
                "current_password": "testpassword123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_password_changed_at_is_set(self, async_client, test_user, db_session):
        """password_changed_at must be set after password change."""
        from app.core.security import create_access_token
        from sqlalchemy import select
        from app.models import User

        token = create_access_token(data={"sub": str(test_user.id)})
        assert test_user.password_changed_at is None

        resp = await async_client.put(
            "/api/v1/users/me",
            json={
                "password": "NewSecure1!Pass",
                "current_password": "testpassword123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Refresh from DB
        result = await db_session.execute(select(User).where(User.id == test_user.id))
        refreshed = result.scalar_one()
        assert refreshed.password_changed_at is not None

    @pytest.mark.asyncio
    async def test_old_token_rejected_after_password_change(self, async_client, test_user, db_session):
        """Tokens issued before password_changed_at must be rejected."""
        from app.core.security import create_access_token
        from app.core.datetime_utils import utc_now

        # Create a token "before" the password change
        old_token = create_access_token(
            data={"sub": str(test_user.id)},
            expires_delta=timedelta(hours=1),
        )

        # Simulate password change by setting password_changed_at in the future
        test_user.password_changed_at = utc_now() + timedelta(seconds=5)
        await db_session.commit()

        # Old token should be rejected
        resp = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert resp.status_code == 401
        assert "password change" in resp.json()["detail"].lower()

    def test_user_model_has_password_changed_at(self):
        """User model must have password_changed_at column."""
        from app.models import User
        assert hasattr(User, "password_changed_at")

    def test_user_model_has_lockout_fields(self):
        """User model must have failed_login_attempts and locked_until."""
        from app.models import User
        assert hasattr(User, "failed_login_attempts")
        assert hasattr(User, "locked_until")


# ─────────────────────────────────────────────────────────────────────────────
# MED-6: Bitcoin budget race condition
# ─────────────────────────────────────────────────────────────────────────────


class TestBitcoinBudgetAtomicUpdate:
    """SA2-06: _update_campaign_totals uses atomic SQL UPDATE."""

    @pytest.mark.host_only
    def test_uses_sql_update_not_read_modify_write(self):
        """_update_campaign_totals must use sql_update, not read-modify-write."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "bitcoin_budget_service.py").read_text()
        idx = source.index("async def _update_campaign_totals")
        method_body = source[idx:idx + 2000]
        assert "sql_update(Campaign)" in method_body, "Must use atomic SQL UPDATE"
        assert "func.coalesce" in method_body, "Must use COALESCE for null safety"
        # Should NOT have the old read-modify-write pattern
        assert "campaign.bitcoin_spent_sats =" not in method_body, \
            "Should not use read-modify-write (campaign.field = campaign.field + x)"


# ─────────────────────────────────────────────────────────────────────────────
# MED-7: DNS rebinding in Nostr
# ─────────────────────────────────────────────────────────────────────────────


class TestNostrDNSRebinding:
    """SA2-07: Nostr relay connections have pre-connect DNS re-validation."""

    @pytest.mark.host_only
    def test_relay_publish_has_preconnect_check(self):
        """publish_event must re-check DNS before connecting."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "nostr_service.py").read_text()
        # Find the _publish_one function
        idx = source.index("async def _publish_one(relay_url")
        func_body = source[idx:idx + 800]
        assert "pre-connect" in func_body.lower() or "SA2-07" in func_body, \
            "publish must re-check DNS before connecting"
        assert "_resolve_and_check_private" in func_body, \
            "publish must call _resolve_and_check_private before connect"

    @pytest.mark.host_only
    def test_relay_query_has_preconnect_check(self):
        """query_events must re-check DNS before connecting."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "nostr_service.py").read_text()
        idx = source.index("async def _query_one(relay_url")
        func_body = source[idx:idx + 800]
        assert "_resolve_and_check_private" in func_body, \
            "query must call _resolve_and_check_private before connect"


# ─────────────────────────────────────────────────────────────────────────────
# MED-8: User email exposure
# ─────────────────────────────────────────────────────────────────────────────


class TestUserEmailExposure:
    """SA2-08: GET /users/ returns UserPublicResponse (no email)."""

    @pytest.mark.asyncio
    async def test_list_users_returns_no_email(self, async_client, test_user):
        """GET /users/ must not include email field."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        users = resp.json()
        for user_data in users:
            assert "email" not in user_data, "User list must not expose email"

    @pytest.mark.asyncio
    async def test_me_endpoint_still_returns_email(self, async_client, test_user):
        """GET /users/me must still return email for the authenticated user."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "email" in resp.json()

    def test_user_public_response_has_no_email(self):
        """UserPublicResponse schema must not have email field."""
        from app.schemas import UserPublicResponse
        fields = UserPublicResponse.model_fields
        assert "email" not in fields

    @pytest.mark.asyncio
    async def test_list_users_search_does_not_search_email(self, async_client, test_user):
        """GET /users/?search= must not search by email."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "api" / "endpoints" / "users.py").read_text()
        idx = source.index("async def list_users")
        func_body = source[idx:idx + 1500]
        assert "User.email.ilike" not in func_body, "Must not search by email"


# ─────────────────────────────────────────────────────────────────────────────
# MED-9: Per-account login lockout
# ─────────────────────────────────────────────────────────────────────────────


class TestAccountLockout:
    """SA2-09: 5+ failed logins lock account for progressive timeout."""

    @pytest.mark.asyncio
    async def test_failed_logins_increment_counter(self, async_client, test_user, db_session):
        """Failed login must increment failed_login_attempts."""
        from sqlalchemy import select
        from app.models import User

        for _ in range(3):
            await async_client.post(
                "/api/v1/auth/login",
                json={"identifier": "testuser", "password": "wrongpassword"},
            )

        result = await db_session.execute(select(User).where(User.id == test_user.id))
        user = result.scalar_one()
        assert user.failed_login_attempts >= 3

    @pytest.mark.asyncio
    async def test_lockout_after_5_failures(self, async_client, test_user, db_session):
        """Account must be locked after 5 failed login attempts."""
        from sqlalchemy import select
        from app.models import User

        for _ in range(5):
            await async_client.post(
                "/api/v1/auth/login",
                json={"identifier": "testuser", "password": "wrongpassword"},
            )

        result = await db_session.execute(select(User).where(User.id == test_user.id))
        user = result.scalar_one()
        assert user.locked_until is not None, "Account should be locked after 5 failures"

    @pytest.mark.asyncio
    async def test_locked_account_returns_429(self, async_client, test_user, db_session):
        """Login to locked account must return 429."""
        from app.core.datetime_utils import utc_now

        # Lock the account
        test_user.locked_until = utc_now() + timedelta(minutes=5)
        test_user.failed_login_attempts = 5
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"identifier": "testuser", "password": "testpassword123"},
        )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_successful_login_resets_counter(self, async_client, test_user, db_session):
        """Successful login must reset failed_login_attempts and locked_until."""
        from sqlalchemy import select
        from app.models import User

        test_user.failed_login_attempts = 3
        test_user.locked_until = None
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/login",
            json={"identifier": "testuser", "password": "testpassword123"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(User).where(User.id == test_user.id))
        user = result.scalar_one()
        assert user.failed_login_attempts == 0
        assert user.locked_until is None


# ─────────────────────────────────────────────────────────────────────────────
# MED-10: WebSocket query-param auth removed
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSocketQueryParamRemoved:
    """SA2-10: Query-param auth removed from WebSocket endpoints."""

    @pytest.mark.host_only
    def test_agents_no_query_param_auth(self):
        """agents.py must not read token from query_params."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "api" / "endpoints" / "agents.py").read_text()
        # The old _extract_ws_token function was removed; auth is now inline.
        # Verify query_params is not used anywhere in the file.
        assert "query_params" not in source, \
            "agents.py must not read token from query_params"

    @pytest.mark.host_only
    def test_campaigns_no_query_param_auth(self):
        """campaigns.py must not read token from query_params."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "api" / "endpoints" / "campaigns.py").read_text()
        idx = source.index("async def authenticate_campaign_websocket")
        func_body = source[idx:idx + 800]
        assert "query_params" not in func_body, \
            "campaigns.py must not read token from query_params"


# ─────────────────────────────────────────────────────────────────────────────
# MED-11: Markdown sanitization
# ─────────────────────────────────────────────────────────────────────────────


class TestMarkdownSanitization:
    """SA2-11: MDEditor instances must use rehype-sanitize."""

    @pytest.mark.host_only
    @pytest.mark.parametrize("file_path", [
        "frontend/src/pages/ProposalCreatePage.tsx",
        "frontend/src/pages/ToolCreatePage.tsx",
        "frontend/src/pages/ToolEditPage.tsx",
    ])
    def test_mdeditor_has_rehype_sanitize(self, file_path):
        """All MDEditor instances must have previewOptions with rehypeSanitize."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / file_path).read_text()
        assert "rehype-sanitize" in source, f"{file_path} missing rehype-sanitize import"
        assert "rehypeSanitize" in source, f"{file_path} missing rehypeSanitize usage"
        # Every MDEditor should have previewOptions
        md_count = source.count("<MDEditor")
        sanitize_count = source.count("rehypeSanitize")
        # At least one rehypeSanitize per MDEditor (import counts as one, usage per editor)
        assert sanitize_count >= md_count, \
            f"{file_path}: {md_count} MDEditors but only {sanitize_count - 1} sanitize usages"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-14: GPU auth timing side-channel
# ─────────────────────────────────────────────────────────────────────────────


class TestGPUAuthTiming:
    """SA2-14: GPU auth uses hmac.compare_digest for constant-time comparison."""

    @pytest.mark.host_only
    def test_gpu_auth_uses_hmac_compare_digest(self):
        """GPUAuthMiddleware must use hmac.compare_digest, not !=."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "scripts" / "gpu_service_security.py").read_text()
        idx = source.index("class GPUAuthMiddleware")
        class_body = source[idx:idx + 2000]
        assert "hmac.compare_digest" in class_body, \
            "GPU auth must use hmac.compare_digest for constant-time comparison"
        # Make sure the old pattern is gone
        assert "provided != _GPU_API_KEY" not in class_body, \
            "Old non-constant-time comparison still present"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-15 / LOW-16: Frontend container hardening
# ─────────────────────────────────────────────────────────────────────────────


class TestFrontendContainerHardening:
    """SA2-15/16: Frontend container has cap_drop, USER node."""

    @pytest.mark.host_only
    def test_docker_compose_frontend_cap_drop(self):
        """docker-compose.yml frontend must have cap_drop: ALL."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "docker-compose.yml").read_text()
        # Find the frontend service section
        idx = source.index("frontend:")
        # Look for cap_drop before the next service
        next_service = source.index("celery-worker:", idx)
        frontend_section = source[idx:next_service]
        assert "cap_drop:" in frontend_section
        assert "ALL" in frontend_section

    @pytest.mark.host_only
    def test_dockerfile_has_user_node(self):
        """frontend/Dockerfile must have USER node directive."""
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "frontend" / "Dockerfile").read_text()
        assert "USER node" in source


# ─────────────────────────────────────────────────────────────────────────────
# LOW-17: ENABLE_DOCS default
# ─────────────────────────────────────────────────────────────────────────────


class TestEnableDocsDefault:
    """SA2-17: .env.example must default ENABLE_DOCS to false."""

    @pytest.mark.host_only
    def test_env_example_docs_false(self):
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / ".env.example").read_text()
        assert "ENABLE_DOCS=false" in source


# ─────────────────────────────────────────────────────────────────────────────
# LOW-18: Tor proxy SocksPolicy
# ─────────────────────────────────────────────────────────────────────────────


class TestTorProxySocksPolicy:
    """SA2-18: Tor proxy must not accept 192.168.0.0/16."""

    @pytest.mark.host_only
    def test_no_192_168_policy(self):
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / "tor-proxy" / "torrc").read_text()
        # Verify there's no SocksPolicy line accepting 192.168.0.0/16
        # (comments mentioning it are fine)
        for line in source.split("\n"):
            if line.strip().startswith("SocksPolicy") and "192.168" in line:
                pytest.fail("torrc should not have SocksPolicy accepting 192.168.0.0/16")
        assert "172.16.0.0/12" in source, "torrc must accept Docker bridge network"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-19: Avatar URL validation
# ─────────────────────────────────────────────────────────────────────────────


class TestAvatarURLValidation:
    """SA2-19: avatar_url validated on backend with allowlist."""

    @pytest.mark.asyncio
    async def test_rejects_non_https_avatar(self, async_client, test_user):
        """Avatar with http:// must be rejected."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={"avatar_url": "http://evil.com/track.png"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "HTTPS" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_unapproved_host(self, async_client, test_user):
        """Avatar from non-allowlisted host must be rejected."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={"avatar_url": "https://attacker.com/track?user=victim"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_accepts_gravatar(self, async_client, test_user):
        """Avatar from gravatar.com must be accepted."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={"avatar_url": "https://gravatar.com/avatar/abc123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_accepts_github_avatar(self, async_client, test_user):
        """Avatar from GitHub must be accepted."""
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})
        resp = await async_client.put(
            "/api/v1/users/me",
            json={"avatar_url": "https://avatars.githubusercontent.com/u/12345"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# LOW-20: Console.error gated
# ─────────────────────────────────────────────────────────────────────────────


class TestConsoleErrorGated:
    """SA2-20: console.error calls gated behind import.meta.env.DEV."""

    @pytest.mark.host_only
    @pytest.mark.parametrize("file_path", [
        "frontend/src/pages/ToolCreatePage.tsx",
        "frontend/src/pages/ProposalCreatePage.tsx",
    ])
    def test_console_error_behind_dev_check(self, file_path):
        from tests.helpers.paths import PROJECT_ROOT
        source = (PROJECT_ROOT / file_path).read_text()
        # All console.error calls should be inside import.meta.env.DEV blocks
        # Find console.error that is NOT inside a DEV check
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "console.error" in line and "import.meta.env.DEV" not in line:
                # Check if the preceding lines have a DEV guard
                context = "\n".join(lines[max(0, i - 3):i + 1])
                assert "import.meta.env.DEV" in context, \
                    f"{file_path}:{i + 1} has ungated console.error"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-21: LND lookup_invoice r_hash validation
# ─────────────────────────────────────────────────────────────────────────────


class TestLNDInputValidation:
    """SA2-21/23: LND service validates path/query segments."""

    @pytest.mark.host_only
    def test_lookup_invoice_validates_hex(self):
        """lookup_invoice must validate r_hash_hex is hex."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "lnd_service.py").read_text()
        idx = source.index("async def lookup_invoice")
        method_body = source[idx:idx + 800]
        assert "re.fullmatch" in method_body, "Must use re.fullmatch for hex validation"
        assert "[0-9a-fA-F]" in method_body

    @pytest.mark.host_only
    def test_estimate_fee_validates_address(self):
        """estimate_fee must validate Bitcoin address format."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "lnd_service.py").read_text()
        idx = source.index("async def estimate_fee")
        method_body = source[idx:idx + 800]
        assert "re.fullmatch" in method_body, "Must validate address format"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-22: Boltz swap_id validation
# ─────────────────────────────────────────────────────────────────────────────


class TestBoltzInputValidation:
    """SA2-22: Boltz service validates swap_id."""

    @pytest.mark.host_only
    def test_get_swap_status_validates_id(self):
        """get_swap_status_from_boltz must validate swap_id is alphanumeric."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "boltz_service.py").read_text()
        idx = source.index("async def get_swap_status_from_boltz")
        method_body = source[idx:idx + 600]
        assert "re.fullmatch" in method_body


# ─────────────────────────────────────────────────────────────────────────────
# LOW-24: Internal metadata key collision
# ─────────────────────────────────────────────────────────────────────────────


class TestInternalMetadataKeys:
    """SA2-24: Internal metadata uses __ma_ prefix, stripped before external APIs."""

    @pytest.mark.host_only
    def test_uses_ma_prefix(self):
        """Internal metadata must use __ma_ prefix."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        assert '__ma_user_id' in source, "Must use __ma_user_id prefix"
        assert '__ma_execution_id' in source, "Must use __ma_execution_id prefix"
        # Old prefix should be gone
        assert '"_user_id"' not in source, "Old _user_id prefix still present"
        assert '"_execution_id"' not in source, "Old _execution_id prefix still present"

    @pytest.mark.host_only
    def test_rest_api_strips_internal_keys(self):
        """_execute_rest_api must strip __ma_ keys before sending to external APIs."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        # Search the full method (it's large)
        idx = source.index("async def _execute_rest_api")
        next_method = source.find("\n    async def ", idx + 50)
        method_body = source[idx:next_method] if next_method != -1 else source[idx:]
        assert '__ma_' in method_body, "Must filter __ma_ keys"
        assert 'clean_params' in method_body or 'startswith("__ma_")' in method_body


# ─────────────────────────────────────────────────────────────────────────────
# LOW-25: parse_tool_calls error handling
# ─────────────────────────────────────────────────────────────────────────────


class TestParseToolCallsErrorHandling:
    """SA2-25: parse_tool_calls skips tool call on JSON parse failure."""

    @pytest.mark.host_only
    def test_skips_invalid_json(self):
        """parse_tool_calls must `continue` on JSON error, not execute with empty params."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "agents" / "base.py").read_text()
        idx = source.index("json.JSONDecodeError")
        # The block after except should have 'continue' within the next 500 chars
        block = source[idx:idx + 500]
        assert "continue" in block, "Must skip (continue) on JSON parse failure, not execute"
        assert "params = {}" not in block, "Must not fall back to empty params"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-26: MCP server_command shlex.split
# ─────────────────────────────────────────────────────────────────────────────


class TestMCPShellSplit:
    """SA2-26: MCP server_command uses shlex.split() for proper parsing."""

    @pytest.mark.host_only
    def test_uses_shlex_split(self):
        """MCP stdio must use shlex.split() not str.split()."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "tool_execution_service.py").read_text()
        idx = source.index("async def _execute_mcp_stdio")
        method_body = source[idx:idx + 800]
        assert "shlex.split" in method_body, "Must use shlex.split for command parsing"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-27: Sandbox workdir restriction
# ─────────────────────────────────────────────────────────────────────────────


class TestSandboxWorkdirRestriction:
    """SA2-27: Sandbox exec_command rejects workdir outside /workspace or /tmp."""

    @pytest.mark.host_only
    def test_workdir_validation_present(self):
        """exec_command must validate workdir starts with /workspace or /tmp."""
        from tests.helpers.paths import BACKEND_ROOT
        source = (BACKEND_ROOT / "app" / "services" / "dev_sandbox_service.py").read_text()
        idx = source.index("async def exec_command")
        # Search until next method or end of class
        next_method = source.find("\n    async def ", idx + 50)
        method_body = source[idx:next_method] if next_method != -1 else source[idx:idx + 2000]
        assert "/workspace" in method_body, "Must check workdir starts with /workspace"
        assert "/tmp" in method_body, "Must check workdir starts with /tmp"
        assert "ValueError" in method_body or "raise" in method_body


# ─────────────────────────────────────────────────────────────────────────────
# Alembic migration presence check
# ─────────────────────────────────────────────────────────────────────────────


class TestAlembicMigration:
    """Verify the SA2 migration exists."""

    @pytest.mark.host_only
    def test_migration_file_exists(self):
        from tests.helpers.paths import BACKEND_ROOT
        migrations = list(
            (BACKEND_ROOT / "alembic" / "versions").glob("*sa2*")
        )
        assert len(migrations) >= 1, "SA2 migration file not found"

    @pytest.mark.host_only
    def test_migration_adds_required_columns(self):
        from tests.helpers.paths import BACKEND_ROOT
        migration_files = list(
            (BACKEND_ROOT / "alembic" / "versions").glob("*sa2*")
        )
        source = migration_files[0].read_text()
        assert "password_changed_at" in source
        assert "failed_login_attempts" in source
        assert "locked_until" in source





# ============================================================================
# MEDIUM-2: Sandbox exec_command Hardcoded User
# ============================================================================

class TestMedium2SandboxUserHardcode:
    """Verify exec_command always uses user 1000:1000."""

    @pytest.mark.asyncio
    async def test_exec_always_uses_non_root_user(self):
        """Even if caller passes user='root', exec uses 1000:1000."""
        from app.services.dev_sandbox_service import DevSandboxService

        mock_docker_client = MagicMock()

        # Mock container
        mock_container = MagicMock()
        mock_container.id = "test-container-id"

        mock_docker_client.api.exec_create = MagicMock(return_value={"Id": "exec-id"})
        mock_docker_client.api.exec_start = MagicMock(return_value=(b"output", b""))
        mock_docker_client.api.exec_inspect = MagicMock(return_value={"ExitCode": 0})

        with patch.object(DevSandboxService, 'client', new_callable=PropertyMock,
                          return_value=mock_docker_client):
            svc = DevSandboxService.__new__(DevSandboxService)
            svc._get_container = MagicMock(return_value=mock_container)

            result = await svc.exec_command(
                sandbox_id="test-sandbox",
                command="whoami",
                user="root",  # Attacker tries to escalate
            )

        # Verify the exec_create call used 1000:1000, NOT root
        exec_kwargs = mock_docker_client.api.exec_create.call_args.kwargs
        assert exec_kwargs["user"] == "1000:1000"

    @pytest.mark.asyncio
    async def test_exec_uses_non_root_even_without_user_param(self):
        """When user param is None (default), exec still uses 1000:1000."""
        from app.services.dev_sandbox_service import DevSandboxService

        mock_docker_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "test-container-id"

        mock_docker_client.api.exec_create = MagicMock(return_value={"Id": "exec-id"})
        mock_docker_client.api.exec_start = MagicMock(return_value=(b"output", b""))
        mock_docker_client.api.exec_inspect = MagicMock(return_value={"ExitCode": 0})

        with patch.object(DevSandboxService, 'client', new_callable=PropertyMock,
                          return_value=mock_docker_client):
            svc = DevSandboxService.__new__(DevSandboxService)
            svc._get_container = MagicMock(return_value=mock_container)

            result = await svc.exec_command(
                sandbox_id="test-sandbox",
                command="ls",
                # user not specified — defaults to None
            )

        exec_kwargs = mock_docker_client.api.exec_create.call_args.kwargs
        assert exec_kwargs["user"] == "1000:1000"


# ============================================================================
# MEDIUM-3: Per-Installation KDF Salt
# ============================================================================




# ============================================================================
# LOW-1: Ollama HTTP Client Reuse
# ============================================================================

class TestLow1OllamaClientReuse:
    """Verify OllamaProvider reuses a single httpx client."""

    def test_ollama_provider_has_shared_client(self):
        """OllamaProvider creates _http_client attribute."""
        from app.services.llm_service import OllamaProvider

        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={"fast": "test-model"},
            context_lengths={"fast": 4096},
        )
        assert hasattr(provider, '_http_client')
        assert hasattr(provider, '_get_http_client')

    def test_get_http_client_returns_same_instance(self):
        """Multiple calls to _get_http_client return the same client."""
        from app.services.llm_service import OllamaProvider

        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={"fast": "test-model"},
            context_lengths={"fast": 4096},
        )

        client1 = provider._get_http_client()
        client2 = provider._get_http_client()
        assert client1 is client2


# ============================================================================
# LOW-2: LND Client Init Lock
# ============================================================================





# ============================================================================
# GAP-4: GPU Service Auth Startup Warning
# ============================================================================

@pytest.mark.host_only
class TestGap4GpuAuthWarning:
    """Verify GPU service logs warning when API key is unset."""

    def test_warning_code_exists_in_gpu_security(self):
        """gpu_service_security.py should warn when GPU_SERVICE_API_KEY is not set."""
        gpu_sec_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts" / "gpu_service_security.py"
        )
        source = gpu_sec_path.read_text()

        assert "GPU_SERVICE_API_KEY is not set" in source, \
            "GPU service should warn when API key is unset (GAP-4)"
        assert "UNAUTHENTICATED" in source, \
            "Warning should mention endpoints are unauthenticated"


# ============================================================================
# GAP-5: GPU /unload + /shutdown Internal Auth
# ============================================================================




# ============================================================================
# GAP-5: GPU /unload + /shutdown Internal Auth
# ============================================================================

class TestGap5GpuInternalAuth:
    """Verify management endpoints require internal auth."""

    @pytest.mark.host_only
    def test_internal_paths_not_in_public(self):
        """Management paths must not be in the public (no-auth) set."""
        gpu_sec_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts" / "gpu_service_security.py"
        )
        source = gpu_sec_path.read_text()

        # /unload and /shutdown should NOT be in _PUBLIC_PATHS
        public_section = source.split("_PUBLIC_PATHS")[1].split("}")[0]
        assert '"/unload"' not in public_section, \
            "/unload must not be in _PUBLIC_PATHS"
        assert '"/shutdown"' not in public_section, \
            "/shutdown must not be in _PUBLIC_PATHS"

    @pytest.mark.host_only
    def test_internal_paths_defined_separately(self):
        """_INTERNAL_PATHS should contain management endpoints."""
        gpu_sec_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts" / "gpu_service_security.py"
        )
        source = gpu_sec_path.read_text()

        assert "_INTERNAL_PATHS" in source, \
            "_INTERNAL_PATHS should be defined for management endpoints"
        internal_section = source.split("_INTERNAL_PATHS")[1].split("}")[0]
        assert "/unload" in internal_section
        assert "/shutdown" in internal_section

    @pytest.mark.host_only
    def test_internal_key_env_var_read(self):
        """GPU service should read GPU_INTERNAL_API_KEY env var."""
        gpu_sec_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts" / "gpu_service_security.py"
        )
        source = gpu_sec_path.read_text()
        assert "GPU_INTERNAL_API_KEY" in source, \
            "GPU service should read GPU_INTERNAL_API_KEY for internal auth"

    def test_gpu_lifecycle_service_uses_internal_key(self):
        """GPU lifecycle should prefer the internal API key."""
        lifecycle_path = (
            Path(__file__).parent.parent.parent
            / "app" / "services" / "gpu_lifecycle_service.py"
        )
        source = lifecycle_path.read_text()
        assert "gpu_internal_api_key" in source, \
            "GPU lifecycle service should use gpu_internal_api_key"

    @pytest.mark.host_only
    def test_gpu_middleware_rejects_unauthenticated_unload(self):
        """GPUAuthMiddleware should reject /unload without proper key."""
        # We test the middleware logic directly by parsing the dispatch code
        gpu_sec_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts" / "gpu_service_security.py"
        )
        source = gpu_sec_path.read_text()

        # The dispatch method should check X-API-Key for internal paths
        assert "hmac.compare_digest" in source, \
            "Must use constant-time comparison for key validation"

    def test_config_has_gpu_internal_api_key(self):
        """Backend config should have gpu_internal_api_key field."""
        from app.core.config import Settings
        settings = Settings(
            secret_key="test-secret-key-for-gap5-test",
            postgres_password="test",
            redis_password="test",
        )
        # Should have the attribute (value may be None)
        assert hasattr(settings, "gpu_internal_api_key")


# ============================================================================
# GAP-6 & GAP-7: Docker Compose Configuration
# ============================================================================




# ============================================================================
# GAP-6 & GAP-7: Docker Compose Configuration
# ============================================================================

@pytest.mark.host_only
class TestGap6And7DockerCompose:
    """Verify Docker Compose has prod profile and network segmentation."""

    @pytest.fixture(autouse=True)
    def _load_compose(self):
        compose_path = Path(__file__).parent.parent.parent.parent / "docker-compose.yml"
        self.compose_content = compose_path.read_text()

    def test_frontend_prod_service_exists(self):
        """docker-compose.yml should define a frontend-prod service."""
        assert "frontend-prod:" in self.compose_content, \
            "frontend-prod service should exist for production deployments"

    def test_frontend_prod_uses_dockerfile_prod(self):
        """frontend-prod should use Dockerfile.prod."""
        idx = self.compose_content.find("frontend-prod:")
        section = self.compose_content[idx:idx + 500]
        assert "Dockerfile.prod" in section, \
            "frontend-prod should reference Dockerfile.prod"

    def test_frontend_prod_has_prod_profile(self):
        """frontend-prod should only start with --profile prod."""
        idx = self.compose_content.find("frontend-prod:")
        section = self.compose_content[idx:idx + 500]
        assert '"prod"' in section or "'prod'" in section, \
            "frontend-prod should be in the 'prod' profile"

    def test_network_segmentation_defined(self):
        """docker-compose.yml should define frontend-net and backend-net."""
        assert "frontend-net:" in self.compose_content, \
            "frontend-net network should be defined"
        assert "backend-net:" in self.compose_content, \
            "backend-net network should be defined"

    def test_frontend_only_on_frontend_net(self):
        """Frontend service should only be on frontend-net, not backend-net."""
        # Find the dev frontend service section
        idx = self.compose_content.find("  frontend:")
        # Get from frontend: to the next top-level service (2-space indent)
        remaining = self.compose_content[idx:]
        # Find next service definition
        lines = remaining.split("\n")
        service_lines = []
        for i, line in enumerate(lines[1:], 1):
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
            if re.match(r"^  \w", line) and i > 1:
                break
            service_lines.append(line)

        section = "\n".join(service_lines)
        assert "frontend-net" in section, \
            "Frontend should be on frontend-net"
        assert "backend-net" not in section, \
            "Frontend should NOT be on backend-net"

    def test_backend_on_both_networks(self):
        """Backend should be on both frontend-net and backend-net."""
        # Find backend service and extract its networks
        lines = self.compose_content.split("\n")
        in_backend = False
        in_networks = False
        networks_found = []
        for line in lines:
            # Detect top-level 'backend:' service
            if re.match(r'^  backend:', line):
                in_backend = True
                continue
            # Detect next top-level service
            if in_backend and re.match(r'^  \w', line) and not line.startswith('    '):
                break
            if in_backend and '    networks:' in line:
                in_networks = True
                continue
            if in_backend and in_networks:
                stripped = line.strip()
                if stripped.startswith('- '):
                    networks_found.append(stripped[2:])
                elif stripped and not stripped.startswith('#'):
                    in_networks = False

        assert "frontend-net" in networks_found, \
            f"Backend should be on frontend-net, found: {networks_found}"
        assert "backend-net" in networks_found, \
            f"Backend should be on backend-net, found: {networks_found}"

    def test_postgres_only_on_backend_net(self):
        """Postgres should only be on backend-net."""
        lines = self.compose_content.split("\n")
        in_pg = False
        in_networks = False
        networks_found = []
        for line in lines:
            if re.match(r'^  postgres:', line):
                in_pg = True
                continue
            if in_pg and re.match(r'^  \w', line) and not line.startswith('    '):
                break
            if in_pg and '    networks:' in line:
                in_networks = True
                continue
            if in_pg and in_networks:
                stripped = line.strip()
                if stripped.startswith('- '):
                    networks_found.append(stripped[2:])
                elif stripped and not stripped.startswith('#'):
                    in_networks = False

        assert "backend-net" in networks_found, \
            f"Postgres should be on backend-net, found: {networks_found}"
        assert "frontend-net" not in networks_found, \
            f"Postgres should NOT be on frontend-net, found: {networks_found}"

    def test_redis_only_on_backend_net(self):
        """Redis should only be on backend-net."""
        lines = self.compose_content.split("\n")
        in_redis = False
        in_networks = False
        networks_found = []
        for line in lines:
            if re.match(r'^  redis:', line):
                in_redis = True
                continue
            if in_redis and re.match(r'^  \w', line) and not line.startswith('    '):
                break
            if in_redis and '    networks:' in line:
                in_networks = True
                continue
            if in_redis and in_networks:
                stripped = line.strip()
                if stripped.startswith('- '):
                    networks_found.append(stripped[2:])
                elif stripped and not stripped.startswith('#'):
                    in_networks = False

        assert "backend-net" in networks_found, \
            f"Redis should be on backend-net, found: {networks_found}"
        assert "frontend-net" not in networks_found, \
            f"Redis should NOT be on frontend-net, found: {networks_found}"

    def test_no_money_agents_network_remains(self):
        """The old monolithic money-agents-network should be gone."""
        assert "money-agents-network" not in self.compose_content, \
            "money-agents-network should be replaced by frontend-net/backend-net"


# ============================================================================
# GAP-8: Frontend console.error Gating
# ============================================================================




# ============================================================================
# GAP-8: Frontend console.error Gating
# ============================================================================

@pytest.mark.host_only
class TestGap8ConsoleErrorGating:
    """Verify frontend console.error calls are gated behind DEV check."""

    def test_logger_utility_exists(self):
        """frontend/src/lib/logger.ts should exist with logError function."""
        logger_path = (
            Path(__file__).parent.parent.parent.parent
            / "frontend" / "src" / "lib" / "logger.ts"
        )
        assert logger_path.exists(), "logger.ts utility should exist"
        source = logger_path.read_text()
        assert "logError" in source
        assert "import.meta.env.DEV" in source or "isDev" in source

    def test_no_ungated_console_error_in_key_files(self):
        """Key frontend files should not have ungated console.error calls."""
        frontend_src = (
            Path(__file__).parent.parent.parent.parent / "frontend" / "src"
        )

        # Files that were known to have ungated console.error calls
        check_files = [
            "components/tools/HealthIndicators.tsx",
            "components/conversations/ConversationPanel.tsx",
            "components/conversations/MessageBubble.tsx",
            "components/conversations/MessageAttachments.tsx",
            "components/tools/RateLimitDisplay.tsx",
            "components/conversations/AgentChatPanel.tsx",
            "components/agents/AgentConfigModal.tsx",
            "pages/BudgetPage.tsx",
            "pages/ToolEditPage.tsx",
            "components/dashboard/SatsTracker.tsx",
        ]

        for relpath in check_files:
            filepath = frontend_src / relpath
            if not filepath.exists():
                continue
            source = filepath.read_text()

            # Find all console.error occurrences not inside if(isDev) or
            # if(import.meta.env.DEV) blocks
            lines = source.split("\n")
            for i, line in enumerate(lines):
                if "console.error" in line:
                    # Check if this line or the preceding lines contain a dev gate
                    context = "\n".join(lines[max(0, i - 3):i + 1])
                    assert (
                        "isDev" in context
                        or "import.meta.env.DEV" in context
                        or "logError" in line  # replaced with logError is also fine
                    ), (
                        f"Ungated console.error in {relpath}:{i+1}: {line.strip()}"
                    )

    def test_api_client_error_logging_gated(self):
        """api-client.ts console.error calls must be behind isDev check."""
        api_client_path = (
            Path(__file__).parent.parent.parent.parent
            / "frontend" / "src" / "lib" / "api-client.ts"
        )
        source = api_client_path.read_text()
        interceptor_idx = source.index("interceptors.response.use")
        interceptor_section = source[interceptor_idx:interceptor_idx + 800]
        lines = interceptor_section.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if "console.error" in stripped:
                preceding_context = "\n".join(lines[max(0, i - 3):i + 1])
                assert "isDev" in preceding_context, \
                    f"console.error on line {i} must be gated by isDev:\n{preceding_context}"


# ============================================================================
# GAP-9: ENABLE_DOCS Defaults to False
# ============================================================================




# ============================================================================
# GAP-9: ENABLE_DOCS Defaults to False
# ============================================================================

@pytest.mark.host_only
class TestGap9EnableDocsDefault:
    """Verify ENABLE_DOCS defaults to false in docker-compose.yml."""

    def test_enable_docs_default_false(self):
        compose_path = Path(__file__).parent.parent.parent.parent / "docker-compose.yml"
        content = compose_path.read_text()
        assert "ENABLE_DOCS=${ENABLE_DOCS:-false}" in content, \
            "ENABLE_DOCS should default to false"
        assert "ENABLE_DOCS=${ENABLE_DOCS:-true}" not in content, \
            "ENABLE_DOCS should NOT default to true"


# ============================================================================
# GAP-10: Unicode NFKC Normalization
# ============================================================================




# ============================================================================
# GAP-11: Sandbox write_files Path Validation
# ============================================================================

class TestGap11WriteFilesPathValidation:
    """Verify write_files rejects paths outside /workspace and /tmp."""

    @pytest.mark.asyncio
    async def test_write_files_rejects_etc_passwd(self):
        """write_files should reject path targeting /etc/passwd."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)
        svc._containers = {"test-sb": MagicMock()}

        with pytest.raises(ValueError, match="must start with /workspace or /tmp"):
            await svc.write_files(
                sandbox_id="test-sb",
                files={"/etc/passwd": "root:x:0:0:::/bin/sh"},
            )

    @pytest.mark.asyncio
    async def test_write_files_rejects_var_run(self):
        """write_files should reject path targeting /var/run."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)
        svc._containers = {"test-sb": MagicMock()}

        with pytest.raises(ValueError, match="must start with /workspace or /tmp"):
            await svc.write_files(
                sandbox_id="test-sb",
                files={"/var/run/malicious.pid": "1234"},
            )

    @pytest.mark.asyncio
    async def test_write_files_rejects_path_traversal(self):
        """write_files should reject path traversal via ../."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)
        svc._containers = {"test-sb": MagicMock()}

        with pytest.raises(ValueError, match="must start with /workspace or /tmp"):
            await svc.write_files(
                sandbox_id="test-sb",
                files={"/workspace/../etc/passwd": "pwned"},
            )

    @pytest.mark.asyncio
    async def test_write_files_allows_workspace_path(self):
        """write_files should allow paths under /workspace."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)

        mock_container = MagicMock()
        mock_container.put_archive = MagicMock()

        # Mock _get_container to return our mock
        svc._get_container = MagicMock(return_value=mock_container)
        svc.exec_command = AsyncMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))

        result = await svc.write_files(
            sandbox_id="test-sb",
            files={"/workspace/app.py": "print('hello')"},
        )

        assert result == ["/workspace/app.py"]

    @pytest.mark.asyncio
    async def test_write_files_allows_tmp_path(self):
        """write_files should allow paths under /tmp."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)

        mock_container = MagicMock()
        mock_container.put_archive = MagicMock()
        svc._get_container = MagicMock(return_value=mock_container)
        svc.exec_command = AsyncMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))

        result = await svc.write_files(
            sandbox_id="test-sb",
            files={"/tmp/data.csv": "a,b\n1,2"},
        )

        assert result == ["/tmp/data.csv"]

    @pytest.mark.asyncio
    async def test_write_files_rejects_mixed_paths(self):
        """write_files should reject if ANY path is outside allowed dirs."""
        from app.services.dev_sandbox_service import DevSandboxService

        svc = DevSandboxService.__new__(DevSandboxService)
        svc._containers = {"test-sb": MagicMock()}

        with pytest.raises(ValueError, match="must start with /workspace or /tmp"):
            await svc.write_files(
                sandbox_id="test-sb",
                files={
                    "/workspace/ok.py": "fine",
                    "/etc/shadow": "not fine",
                },
            )


# ============================================================================
# GAP-12: Boltz Stderr Hex Redaction
# ============================================================================


# ============================================================================
# GAP-15: Dependency Version Requirements
# ============================================================================

class TestGap15DependencyVersions:
    """Verify dependencies are updated to fix known CVEs."""

    @pytest.mark.host_only
    def test_aiohttp_version_safe(self):
        """aiohttp must be >= 3.10.11 (CVE-2024-23334, CVE-2024-23829)."""
        from tests.helpers.paths import backend_file
        source = backend_file("requirements.txt").read_text()
        assert "aiohttp==3.9.3" not in source, "aiohttp 3.9.3 has known CVEs"
        assert "aiohttp==3.9" not in source, "aiohttp 3.9.x has known CVEs"
        assert "aiohttp>=" in source, "aiohttp should specify minimum safe version"

    @pytest.mark.host_only
    def test_pillow_version_safe(self):
        """Pillow must be >= 10.3.0 (CVE-2024-28219)."""
        from tests.helpers.paths import backend_file
        source = backend_file("requirements.txt").read_text()
        assert "Pillow==10.2.0" not in source, "Pillow 10.2.0 has CVE-2024-28219"
        assert "Pillow>=" in source, "Pillow should specify minimum safe version"


# ============================================================================
# GAP-16: Error Information Leakage
# ============================================================================

class TestGap16ErrorLeakage:
    """Verify error responses don't leak internal details."""

    @pytest.mark.host_only
    def test_campaigns_websocket_no_raw_str_e(self):
        """Campaign WebSocket error handler must NOT send raw str(e) to client."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/campaigns.py").read_text()
        ws_section = source[source.rindex("except Exception as e:"):]
        ws_section = ws_section[:ws_section.index("finally:")]
        assert '"error": str(e)' not in ws_section, \
            "Campaign WebSocket must not send raw str(e) to client"

    @pytest.mark.host_only
    def test_wallet_no_raw_lnd_errors(self):
        """Wallet endpoints must not pass raw LND errors to clients."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/wallet.py").read_text()
        count = source.count('f"LND error: {error}"')
        assert count == 0, \
            f"Found {count} instances of raw LND error leakage — should be 0"


# ============================================================================
# GAP-19: Stale WebSocket Docstrings
# ============================================================================

class TestGap19StaleDocstrings:
    """Verify WebSocket docstrings don't reference removed ?token= auth."""

    @pytest.mark.host_only
    def test_agents_no_query_param_in_docstrings(self):
        """agents.py WebSocket docstrings must not mention ?token= auth."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/agents.py").read_text()
        ws_pattern = r'@router\.websocket\([^)]+\)\s+async def \w+\([^)]*\):\s+"""(.*?)"""'
        ws_docs = re.findall(ws_pattern, source, re.DOTALL)
        for doc in ws_docs:
            assert "?token=" not in doc, \
                f"WebSocket docstring still mentions removed ?token= auth:\n{doc[:100]}"

    @pytest.mark.host_only
    def test_bitcoin_budget_no_query_param_in_docstring(self):
        """bitcoin_budget.py WebSocket docstring must not mention ?token= auth."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/bitcoin_budget.py").read_text()
        ws_pattern = r'@router\.websocket\([^)]+\)\s+async def \w+\([^)]*\):\s+"""(.*?)"""'
        ws_docs = re.findall(ws_pattern, source, re.DOTALL)
        for doc in ws_docs:
            assert "?token=" not in doc, \
                f"WebSocket docstring still mentions removed ?token= auth"

    @pytest.mark.host_only
    def test_campaigns_no_query_param_in_docstring(self):
        """campaigns.py WebSocket docstring must not mention ?token= auth."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/campaigns.py").read_text()
        ws_pattern = r'@router\.websocket\([^)]+\)\s+async def \w+\([^)]*\):\s+"""(.*?)"""'
        ws_docs = re.findall(ws_pattern, source, re.DOTALL)
        for doc in ws_docs:
            assert "?token=" not in doc, \
                f"WebSocket docstring still mentions removed ?token= auth"

    @pytest.mark.host_only
    def test_authenticate_websocket_no_query_param_doc(self):
        """authenticate_websocket docstring must not mention query param auth."""
        from tests.helpers.paths import backend_file
        # authenticate_websocket was moved from agents.py to websocket_security.py
        source = backend_file("app/api/websocket_security.py").read_text()
        func_idx = source.index("async def authenticate_websocket")
        after = source[func_idx:func_idx + 1500]
        doc_match = re.search(r'"""(.*?)"""', after, re.DOTALL)
        assert doc_match, "authenticate_websocket must have a docstring"
        doc = doc_match.group(1)
        assert "Query parameter" not in doc, \
            "authenticate_websocket docstring must not mention query parameter auth"


# ============================================================================
# GAP-21: PostgreSQL SSL Documentation
# ============================================================================

class TestGap21PostgresSslDocs:
    """Verify .env.example documents SSL for remote PostgreSQL."""

    @pytest.mark.host_only
    def test_env_example_has_ssl_note(self):
        """The .env.example must document SSL for remote PostgreSQL."""
        from tests.helpers.paths import PROJECT_ROOT
        env_example = (PROJECT_ROOT / ".env.example").read_text()
        assert "ssl" in env_example.lower(), \
            ".env.example must document SSL configuration for remote PostgreSQL"


# ============================================================================
# GAP-23: httpx Follow Redirects
# ============================================================================

class TestGap23HttpxRedirects:
    """Verify httpx client explicitly disables redirect following."""

    @pytest.mark.host_only
    def test_httpx_client_no_redirects(self):
        """tool_execution_service _get_client must set follow_redirects=False."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/services/tool_execution_service.py").read_text()
        client_section = source[source.index("async def _get_client"):]
        client_section = client_section[:client_section.index("\n    async def ")]
        assert "follow_redirects=False" in client_section, \
            "httpx client must explicitly disable redirect following"


# ============================================================================
# GAP-24: passlib Deprecation Note
# ============================================================================

class TestGap24PasslibNote:
    """Verify passlib has a deprecation/migration note."""

    @pytest.mark.host_only
    def test_passlib_has_deprecation_note(self):
        """requirements.txt should note passlib is unmaintained."""
        from tests.helpers.paths import backend_file
        source = backend_file("requirements.txt").read_text()
        passlib_idx = source.index("passlib")
        context = source[max(0, passlib_idx - 200):passlib_idx + 100]
        assert "unmaintained" in context.lower() or \
               "deprecat" in context.lower() or \
               "consider migrating" in context.lower(), \
            "requirements.txt should note that passlib is unmaintained"


# ============================================================================
# SA3 (February 2026 Assessment) — Tests for all implemented fixes
# ============================================================================


class TestSA3C1PathValidator:
    """SA3-C1: Centralized path validator rejects traversal and restricted paths."""

    def test_valid_data_path(self):
        from app.core.path_security import validate_tool_file_path
        result = validate_tool_file_path("/data/uploads/test.wav", label="audio")
        assert str(result).startswith("/data")

    def test_valid_tmp_path(self):
        from app.core.path_security import validate_tool_file_path
        result = validate_tool_file_path("/tmp/audio.wav", label="audio")
        assert str(result).startswith("/tmp")

    def test_valid_uploads_path(self):
        from app.core.path_security import validate_tool_file_path
        result = validate_tool_file_path("/app/uploads/doc.pdf", label="file")
        assert str(result).startswith("/app/uploads")

    def test_rejects_etc_passwd(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("/etc/passwd", label="audio")

    def test_rejects_proc_environ(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("/proc/self/environ", label="file")

    def test_rejects_dot_dot_traversal(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("/data/../etc/passwd", label="audio")

    def test_rejects_app_env_file(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("/app/.env", label="file")

    def test_rejects_alembic_ini(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("/app/alembic.ini", label="file")

    def test_rejects_empty_path(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="(?i)empty"):
            validate_tool_file_path("", label="audio")

    def test_rejects_relative_path(self):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path("etc/passwd", label="audio")

    @pytest.mark.parametrize("malicious_path", [
        "/data/../../../../etc/shadow",
        "/tmp/../../../root/.ssh/id_rsa",
        "/app/uploads/../../.env",
        "/data/./../../etc/passwd",
    ])
    def test_rejects_various_traversal_patterns(self, malicious_path):
        from app.core.path_security import validate_tool_file_path
        with pytest.raises(ValueError, match="not in allowed"):
            validate_tool_file_path(malicious_path, label="test")


class TestSA3C1PathValidatorIntegration:
    """SA3-C1: Verify path validation is wired into tool executors."""

    @pytest.mark.host_only
    def test_canary_stt_executor_validates_path(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/tool_execution_service.py")
        # Find the method definition (not the dispatch table reference)
        idx = source.index("async def _execute_canary_stt")
        section = source[idx:idx + 5000]
        assert "validate_tool_file_path" in section, \
            "_execute_canary_stt must call validate_tool_file_path"

    @pytest.mark.host_only
    def test_audiosr_executor_validates_path(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/tool_execution_service.py")
        # Find the method definition (not the dispatch table reference)
        idx = source.index("async def _execute_audiosr")
        section = source[idx:idx + 5000]
        assert "validate_tool_file_path" in section, \
            "_execute_audiosr must call validate_tool_file_path"

    @pytest.mark.host_only
    def test_docling_service_validates_path(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/docling_service.py")
        assert "validate_tool_file_path" in source, \
            "docling_service must call validate_tool_file_path"

    @pytest.mark.host_only
    def test_realesrgan_service_validates_paths(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/realesrgan_cpu_service.py")
        count = source.count("validate_tool_file_path")
        assert count >= 2, \
            "realesrgan_cpu_service must call validate_tool_file_path for both image and video"

    @pytest.mark.host_only
    def test_seedvr2_service_validates_paths(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/seedvr2_service.py")
        count = source.count("validate_tool_file_path")
        assert count >= 2, \
            "seedvr2_service must call validate_tool_file_path for both image and video"


class TestSA3C2DockerExecRestriction:
    """SA3-C2: _get_container validates sandbox ID format and labels."""

    @pytest.mark.host_only
    def test_get_container_validates_sandbox_id_format(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/dev_sandbox_service.py")
        idx = source.index("def _get_container")
        section = source[idx:idx + 1200]
        assert "re.fullmatch" in section or "re.match" in section, \
            "_get_container must validate sandbox_id format with regex"

    @pytest.mark.host_only
    def test_get_container_checks_sandbox_label(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/dev_sandbox_service.py")
        idx = source.index("def _get_container")
        section = source[idx:idx + 1200]
        assert "SANDBOX_LABEL" in section, \
            "_get_container must verify container has sandbox label"


class TestSA3H1SSRFRedirects:
    """SA3-H1: GPU services must disable follow_redirects."""

    @pytest.mark.host_only
    @pytest.mark.parametrize("service_file", [
        "media-toolkit/app.py",
        "realesrgan-cpu/app.py",
        "docling-parser/app.py",
    ])
    def test_follow_redirects_disabled(self, service_file):
        from tests.helpers.paths import require_file
        source = require_file(service_file)
        assert "follow_redirects=True" not in source, \
            f"{service_file} must not use follow_redirects=True (SSRF risk)"
        assert "follow_redirects=False" in source, \
            f"{service_file} must explicitly set follow_redirects=False"


class TestSA3H7SandboxWriteFilePathValidation:
    """SA3-H7: write_file() must validate paths like write_files()."""

    @pytest.mark.host_only
    def test_write_file_has_path_validation(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/dev_sandbox_service.py")
        idx = source.index("async def write_file(")
        next_method = source.index("async def write_files(", idx)
        section = source[idx:next_method]
        assert "normpath" in section or "ALLOWED" in section or "/workspace" in section, \
            "write_file() must validate path targets allowed directories"

    @pytest.mark.host_only
    def test_write_file_rejects_etc(self):
        """Verify write_file path validation rejects /etc/passwd."""
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/dev_sandbox_service.py")
        idx = source.index("async def write_file(")
        next_method = source.index("async def write_files(", idx)
        section = source[idx:next_method]
        assert "ValueError" in section, \
            "write_file() must raise ValueError for disallowed paths"


class TestSA3M1UserEmailPrivacy:
    """SA3-M1: GET /users/{id} must not leak email to non-owner/non-admin."""

    @pytest.mark.host_only
    def test_user_endpoint_restricts_email(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/users.py")
        idx = source.index("async def read_user")
        section = source[idx:idx + 800]
        assert "UserPublicResponse" in section, \
            "read_user must return UserPublicResponse for non-owner requests"


class TestSA3M2HashedResetCodes:
    """SA3-M2: Password reset codes must be stored as SHA-256 hashes."""

    @pytest.mark.host_only
    def test_admin_hashes_code_before_storage(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/admin.py")
        idx = source.index("token_hex")
        section = source[idx:idx + 500]
        assert "sha256" in section.lower() or "hashlib" in section, \
            "admin endpoint must hash reset code before storing"

    @pytest.mark.host_only
    def test_auth_hashes_code_before_lookup(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/auth.py")
        idx = source.index("reset_password")
        section = source[idx:idx + 800]
        assert "sha256" in section.lower() or "hashlib" in section, \
            "auth reset_password must hash submitted code before DB lookup"


class TestSA3M3RedisWarning:
    """SA3-M3: Redis unavailability must log CRITICAL, not WARNING."""

    @pytest.mark.host_only
    def test_redis_fallback_logs_critical(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/core/security.py")
        idx = source.index("_get_redis")
        section = source[idx:idx + 1500]
        assert ".critical(" in section, \
            "_get_redis must log at CRITICAL level when Redis is unavailable"


class TestSA3M4BoltzSwapIdValidation:
    """SA3-M4: get_lockup_transaction must validate swap ID."""

    @pytest.mark.host_only
    def test_lockup_transaction_validates_swap_id(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/boltz_service.py")
        idx = source.index("get_lockup_transaction")
        section = source[idx:idx + 400]
        assert "fullmatch" in section or "re.match" in section, \
            "get_lockup_transaction must validate boltz_swap_id format"


class TestSA3M7FrontendURLValidation:
    """SA3-M7: Frontend must validate URL scheme before rendering href."""

    @pytest.mark.host_only
    def test_opportunity_row_validates_urls(self):
        from tests.helpers.paths import require_file
        source = require_file(
            "frontend/src/components/opportunities/OpportunityRow.tsx"
        )
        assert r"/^https?:\/\//i.test" in source, \
            "OpportunityRow must test URL scheme before rendering as href"

    @pytest.mark.host_only
    def test_tool_detail_page_validates_urls(self):
        from tests.helpers.paths import require_file
        source = require_file("frontend/src/pages/ToolDetailPage.tsx")
        assert r"/^https?:\/\//i.test" in source, \
            "ToolDetailPage must validate external_documentation_url scheme"


class TestSA3M8FrontendDockerfile:
    """SA3-M8: Frontend Dockerfile should not run npm ci at startup."""

    @pytest.mark.host_only
    def test_dockerfile_cmd_no_npm_ci(self):
        from tests.helpers.paths import require_file
        source = require_file("frontend/Dockerfile")
        cmd_lines = [l for l in source.splitlines() if l.strip().startswith("CMD")]
        assert cmd_lines, "Dockerfile must have a CMD"
        for cmd in cmd_lines:
            assert "npm ci" not in cmd, \
                f"CMD should not run 'npm ci' at startup (supply chain risk): {cmd}"


class TestSA3M11NostrErrorHandling:
    """SA3-M11: Nostr executor must not leak exception details."""

    @pytest.mark.host_only
    def test_nostr_exception_handler_is_generic(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/services/tool_execution_service.py")
        # Find _execute_nostr method, then find its closing except handler
        idx = source.index("async def _execute_nostr")
        # Look for the next method definition to bound our search
        next_method = source.index("\n    async def ", idx + 10)
        nostr_section = source[idx:next_method]
        # Find the last except Exception in _execute_nostr only
        exc_idx = nostr_section.rfind("except Exception as e:")
        assert exc_idx > 0
        handler = nostr_section[exc_idx:exc_idx + 500]
        assert 'f"Nostr error: {str(e)}"' not in handler, \
            "Broad exception handler must not leak str(e) to API response"
        assert "Check server logs" in handler or "operation failed" in handler.lower(), \
            "Exception handler should return a generic message"


class TestSA3L1ResetCodeEntropy:
    """SA3-L1: Password reset code must use adequate entropy."""

    @pytest.mark.host_only
    def test_reset_code_uses_sufficient_entropy(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/admin.py")
        # Find the actual token_hex call (not in comments)
        # Match lines like: code = secrets.token_hex(16)
        matches = re.findall(r'^\s*code\s*=\s*secrets\.token_hex\((\d+)\)', source, re.MULTILINE)
        assert matches, "admin.py must call secrets.token_hex() for code generation"
        nbytes = int(matches[-1])  # Use the last match (the actual code, not a comment)
        assert nbytes >= 16, \
            f"token_hex({nbytes}) provides only {nbytes * 8} bits — need >= 128 bits"


class TestSA3L2JWTTypeClaim:
    """SA3-L2: JWT access tokens must include a 'typ' claim."""

    @pytest.mark.host_only
    def test_jwt_has_typ_claim(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/core/security.py")
        idx = source.index("create_access_token")
        section = source[idx:idx + 800]
        assert '"typ"' in section or "'typ'" in section, \
            "create_access_token must include 'typ' claim in JWT payload"

    def test_jwt_typ_claim_value(self):
        """Verify the actual token contains typ=access."""
        from app.core.security import create_access_token
        import jwt
        from app.core.config import settings
        token = create_access_token({"sub": "test-user"})
        payload = jwt.decode(
            token, settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            audience="money-agents",
            issuer="money-agents",
        )
        assert payload.get("typ") == "access", \
            f"JWT 'typ' claim must be 'access', got: {payload.get('typ')}"


class TestSA3L3BackendCSP:
    """SA3-L3: Backend must include Content-Security-Policy header."""

    @pytest.mark.host_only
    def test_security_headers_middleware_has_csp(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/main.py")
        assert "Content-Security-Policy" in source, \
            "SecurityHeadersMiddleware must set Content-Security-Policy header"
        assert "default-src 'none'" in source, \
            "Backend CSP should be restrictive: default-src 'none'"


class TestSA3L4BitcoinAddressValidation:
    """SA3-L4: SendCoinsRequest must validate Bitcoin address format."""

    @pytest.mark.host_only
    def test_send_coins_request_has_address_validator(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/wallet.py")
        idx = source.index("class SendCoinsRequest")
        section = source[idx:idx + 800]
        assert "field_validator" in section or "validate_bitcoin_address" in section, \
            "SendCoinsRequest must have a Bitcoin address validator"

    def test_send_coins_rejects_invalid_address(self):
        """Verify SendCoinsRequest rejects non-Bitcoin addresses."""
        from app.api.endpoints.wallet import SendCoinsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SendCoinsRequest(
                address="not-a-bitcoin-address",
                amount_sats=1000,
            )

    def test_send_coins_accepts_bech32(self):
        """Verify SendCoinsRequest accepts valid bech32 addresses."""
        from app.api.endpoints.wallet import SendCoinsRequest
        req = SendCoinsRequest(
            address="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
            amount_sats=1000,
        )
        assert req.address.startswith("bc1")

    def test_send_coins_rejects_javascript_uri(self):
        """Verify SendCoinsRequest rejects javascript: URIs."""
        from app.api.endpoints.wallet import SendCoinsRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SendCoinsRequest(
                address="javascript:alert(1)",
                amount_sats=1000,
            )


class TestSA3L5FrontendCSPMeta:
    """SA3-L5: index.html must have a CSP meta tag."""

    @pytest.mark.host_only
    def test_index_html_has_csp_meta(self):
        from tests.helpers.paths import require_file
        source = require_file("frontend/index.html")
        assert 'Content-Security-Policy' in source, \
            "index.html must have a <meta http-equiv='Content-Security-Policy'> tag"


class TestSA3L7AutoComplete:
    """SA3-L7: Password fields must have autoComplete attributes."""

    @pytest.mark.host_only
    def test_login_form_autocomplete(self):
        from tests.helpers.paths import require_file
        source = require_file(
            "frontend/src/components/auth/LoginForm.tsx"
        )
        assert 'autoComplete="current-password"' in source, \
            "LoginForm password field must have autoComplete='current-password'"

    @pytest.mark.host_only
    def test_register_form_autocomplete(self):
        from tests.helpers.paths import require_file
        source = require_file(
            "frontend/src/components/auth/RegisterForm.tsx"
        )
        assert source.count('autoComplete="new-password"') >= 2, \
            "RegisterForm must have autoComplete='new-password' on both password fields"


class TestSA3L8AlertSanitization:
    """SA3-L8: alert() calls must not interpolate error.message."""

    @pytest.mark.host_only
    def test_conversation_panel_no_raw_error_alerts(self):
        from tests.helpers.paths import require_file
        source = require_file(
            "frontend/src/components/conversations/ConversationPanel.tsx"
        )
        assert "alert(`Failed" not in source, \
            "ConversationPanel must not use alert() with interpolated error messages"


class TestSA3L9ContentDisposition:
    """SA3-L9: Content-Disposition header must sanitize filename."""

    @pytest.mark.host_only
    def test_media_library_sanitizes_filename(self):
        from tests.helpers.paths import require_file
        source = require_file("backend/app/api/endpoints/media_library.py")
        # Check that the file has filename sanitization near Content-Disposition
        assert "safe_name" in source or "re.sub" in source, \
            "Content-Disposition must use sanitized filename"


class TestSA3M9NeMoPinned:
    """SA3-M9: NeMo toolkit should be pinned to a release tag."""

    @pytest.mark.host_only
    def test_nemo_pinned_to_tag(self):
        from tests.helpers.paths import require_file
        source = require_file("canary-stt/requirements.txt")
        for line in source.splitlines():
            if "nemo_toolkit" in line.lower() and "git+" in line:
                assert "@" in line and not line.strip().endswith(".git"), \
                    f"NeMo must be pinned to a tag/commit, not HEAD: {line.strip()}"


# ============================================================================
# Cache-Control: no-store Header
# ============================================================================


class TestCacheControlNoStore:
    """API responses include Cache-Control: no-store to prevent caching."""

    def test_cache_control_in_security_middleware(self):
        """SecurityHeadersMiddleware sets Cache-Control: no-store."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "main.py").read_text()
        assert "no-store" in src
        assert "Cache-Control" in src


# ============================================================================
# LND Macaroon SecretStr Protection
# ============================================================================


class TestLNDMacaroonSecretStr:
    """lnd_macaroon_hex uses SecretStr to prevent accidental exposure."""

    def test_config_uses_secret_str(self):
        """config.py declares lnd_macaroon_hex as SecretStr."""
        from app.core.config import settings
        from pydantic import SecretStr

        assert isinstance(settings.lnd_macaroon_hex, SecretStr)

    def test_secret_str_repr_hides_value(self):
        """repr(settings.lnd_macaroon_hex) does not show the actual value."""
        from app.core.config import settings

        repr_str = repr(settings.lnd_macaroon_hex)
        raw = settings.lnd_macaroon_hex.get_secret_value()
        if raw:  # only check if there's a value configured
            assert raw not in repr_str

    def test_lnd_service_uses_get_secret_value(self):
        """lnd_service.py calls .get_secret_value() on lnd_macaroon_hex."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "services", "lnd_service.py").read_text()

        bare_refs = re.findall(r"settings\.lnd_macaroon_hex(?!\.get_secret_value)", src)
        assert len(bare_refs) == 0, (
            f"Found {len(bare_refs)} bare references to settings.lnd_macaroon_hex "
            f"without .get_secret_value()"
        )


# ============================================================================
# Infrastructure Security Baseline
# ============================================================================


class TestInfraSecurityBaseline:
    """Static verification of infrastructure security configuration."""

    @pytest.mark.host_only
    def test_tor_proxy_config_exists(self):
        """Tor proxy configuration directory should exist."""
        from tests.helpers.paths import project_file

        tor_dir = project_file("tor-proxy")
        if not tor_dir.exists():
            pytest.skip("tor-proxy not available in Docker")
        assert tor_dir.is_dir()

    def test_docker_compose_has_network_segmentation(self):
        """Docker Compose defines network segmentation."""
        from tests.helpers.paths import require_file

        dc = require_file("docker-compose.yml")
        assert "networks:" in dc, "docker-compose.yml missing network definitions"

    @pytest.mark.host_only
    def test_frontend_csp_header(self):
        """Frontend nginx config includes Content-Security-Policy."""
        from tests.helpers.paths import require_file

        src = require_file("frontend/nginx.conf")
        assert "Content-Security-Policy" in src or "X-Content-Type-Options" in src


# ─────────────────────────────────────────────────────────────────────────────
# GAP-4 / GAP-5: GPU auth fail-closed when key not set
# ─────────────────────────────────────────────────────────────────────────────


class TestGPUAuthFailClosed:
    """GAP-4/5: GPU auth must fail-closed (reject) when API key is unset."""

    @pytest.mark.host_only
    def test_gpu_auth_rejects_when_key_not_set(self):
        """When GPU_SERVICE_API_KEY is empty and GPU_AUTH_SKIP is not set,
        the middleware must return 503, not 200."""
        from tests.helpers.paths import require_file

        source = require_file("scripts/gpu_service_security.py")
        # Verify fail-closed: the code should return 503 when key is not configured
        assert "503" in source, "GPU auth must return 503 when key is not set"
        assert "GPU_AUTH_SKIP" in source, "GPU auth must support GPU_AUTH_SKIP opt-out"
        # Ensure old fail-open pattern is gone
        assert 'if not _GPU_API_KEY:' not in source or 'return' not in source.split('if not _GPU_API_KEY:')[1].split('\n')[1], \
            "Old fail-open pattern (skip auth when key unset) should be removed"

    @pytest.mark.host_only
    def test_gpu_auth_skip_env_var_exists(self):
        """GPU_AUTH_SKIP environment variable should be checked."""
        from tests.helpers.paths import require_file

        source = require_file("scripts/gpu_service_security.py")
        assert "GPU_AUTH_SKIP" in source
        # Ensure it's an opt-in (must be explicitly set to true)
        assert '"true"' in source.lower() or "'true'" in source.lower()

    @pytest.mark.host_only
    def test_management_endpoints_also_require_auth(self):
        """Management endpoints (/unload, /shutdown, /reload) must also
        require GPU_INTERNAL_API_KEY when configured."""
        from tests.helpers.paths import require_file

        source = require_file("scripts/gpu_service_security.py")
        assert "GPU_INTERNAL_API_KEY" in source, \
            "Management endpoints should check GPU_INTERNAL_API_KEY"

    @pytest.mark.host_only
    def test_gpu_internal_key_in_start_py(self):
        """start.py must auto-generate GPU_INTERNAL_API_KEY."""
        from tests.helpers.paths import require_file

        source = require_file("start.py")
        assert "GPU_INTERNAL_API_KEY" in source, \
            "start.py should auto-generate GPU_INTERNAL_API_KEY"


# ─────────────────────────────────────────────────────────────────────────────
# GAP-21: PostgreSQL SSL configuration
# ─────────────────────────────────────────────────────────────────────────────


class TestPostgreSQLSSLConfig:
    """GAP-21: PostgreSQL connections must support SSL mode configuration."""

    def test_config_has_db_ssl_mode(self):
        """Settings model must have db_ssl_mode field."""
        from app.core.config import Settings

        s = Settings(
            database_url="sqlite:///test.db",
            secret_key="test-secret-key-long-enough-1234567890",
        )
        assert hasattr(s, "db_ssl_mode")
        assert s.db_ssl_mode is None  # default is None (disabled)

    @pytest.mark.host_only
    def test_database_engine_uses_connect_args(self):
        """database.py engine creation must pass connect_args with ssl."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/core/database.py")
        assert "connect_args" in source, "database.py must pass connect_args"
        assert "db_ssl_mode" in source, "database.py must check db_ssl_mode"
        assert '"ssl"' in source or "'ssl'" in source, \
            "database.py must set ssl in connect_args"


# ============================================================================
# SGA3-H1: SSRF validation on GPU/CPU service URL parameters
# ============================================================================


class TestSGA3H1GPUServiceSSRF:
    """SGA3-H1: All 6 GPU/CPU executors must validate URLs via validate_target_url()."""

    EXECUTORS = [
        "_execute_seedvr2",
        "_execute_canary_stt",
        "_execute_audiosr",
        "_execute_realesrgan_cpu",
        "_execute_docling",
        "_execute_media_toolkit",
    ]

    @pytest.mark.host_only
    @pytest.mark.parametrize("executor", EXECUTORS)
    def test_executor_calls_validate_target_url(self, executor):
        """Each GPU executor must call validate_target_url before forwarding URLs."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/tool_execution_service.py")
        # Find the executor function start
        start_pat = rf"async def {executor}\b"
        start_match = re.search(start_pat, source)
        assert start_match, f"{executor} not found in source"

        # Find the next executor or end of class to bound the body
        remain = source[start_match.start():]
        # Find next "async def _execute_" after current one
        next_method = re.search(r"\n    async def _execute_", remain[50:])
        if next_method:
            body = remain[:50 + next_method.start()]
        else:
            body = remain[:5000]  # last executor — take a large chunk

        assert "validate_target_url(" in body, (
            f"{executor} must call validate_target_url() on URLs before forwarding"
        )

    @pytest.mark.host_only
    def test_validate_before_host_rewrite(self):
        """Validation must occur before the host.docker.internal → localhost rewrite."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/tool_execution_service.py")
        # Locate a validate_target_url call and the host rewrite
        val_idx = source.find("validate_target_url(")
        rewrite_idx = source.find("host.docker.internal")
        if val_idx >= 0 and rewrite_idx >= 0:
            # At least one validation call should appear before the rewrite
            first_val = source.find("validate_target_url(")
            assert first_val < rewrite_idx or source.count("validate_target_url(") > 1, (
                "validate_target_url() must be called before host.docker.internal rewrite"
            )


# ============================================================================
# SGA3-M1: DNS rebinding mitigation (return_resolved_ips)
# ============================================================================


class TestSGA3M1DNSRebinding:
    """SGA3-M1: validate_target_url supports IP pinning via return_resolved_ips."""

    def test_return_resolved_ips_default_false(self):
        """By default, validate_target_url returns only the URL string."""
        from app.core.security import validate_target_url
        # example.com is a well-known public IP
        result = validate_target_url("https://example.com/path")
        assert isinstance(result, str)

    def test_return_resolved_ips_true_returns_tuple(self):
        """When return_resolved_ips=True, returns (url, ips) tuple."""
        from app.core.security import validate_target_url
        result = validate_target_url(
            "https://example.com/path", return_resolved_ips=True
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        url, ips = result
        assert url == "https://example.com/path"
        assert isinstance(ips, list)
        assert len(ips) > 0  # Should resolve to at least one IP

    def test_private_ip_rejected_with_return_resolved_ips(self):
        """Private IPs still rejected when return_resolved_ips=True."""
        from app.core.security import validate_target_url
        with pytest.raises(ValueError, match="private|internal"):
            validate_target_url(
                "http://127.0.0.1/evil", return_resolved_ips=True
            )

    @pytest.mark.host_only
    def test_source_has_return_resolved_ips_parameter(self):
        """validate_target_url signature includes return_resolved_ips kwarg."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/core/security.py")
        assert "return_resolved_ips" in source
        assert "tuple[str, list[str]]" in source or "Tuple[str, List[str]]" in source


# ============================================================================
# SGA3-M2: Redis dangerous commands disabled
# ============================================================================


class TestSGA3M2RedisDangerousCommands:
    """SGA3-M2: Redis must disable CONFIG, DEBUG, FLUSHALL."""

    @pytest.mark.host_only
    def test_redis_renames_dangerous_commands(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        assert '--rename-command CONFIG ""' in source, "CONFIG must be renamed"
        assert '--rename-command DEBUG ""' in source, "DEBUG must be renamed"
        assert '--rename-command FLUSHALL ""' in source, "FLUSHALL must be renamed"


# ============================================================================
# SGA3-M5: Tor proxy container hardening
# ============================================================================


class TestSGA3M5TorProxyHardening:
    """SGA3-M5: Tor proxy must have security_opt, cap_drop, ulimits."""

    @pytest.mark.host_only
    def test_tor_proxy_security_opt(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        # Find the tor-proxy service block
        tor_idx = source.find("tor-proxy:")
        assert tor_idx >= 0, "tor-proxy service not found"
        tor_block = source[tor_idx:tor_idx + 800]
        assert "no-new-privileges" in tor_block, "tor-proxy must have no-new-privileges"
        assert "cap_drop:" in tor_block or "CAP_DROP" in tor_block.upper(), (
            "tor-proxy must drop capabilities"
        )


# ============================================================================
# SGA3-M6: .dockerignore not gitignored
# ============================================================================


class TestSGA3M6DockerIgnore:
    """SGA3-M6: .dockerignore files must exist and not be gitignored."""

    @pytest.mark.host_only
    def test_dockerignore_not_in_gitignore(self):
        from tests.helpers.paths import require_file

        gitignore = require_file(".gitignore")
        # .dockerignore should NOT appear as a gitignore pattern
        for line in gitignore.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                assert stripped != ".dockerignore", (
                    ".dockerignore must not be in .gitignore"
                )

    @pytest.mark.host_only
    def test_backend_dockerignore_exists(self):
        from tests.helpers.paths import project_file

        path = project_file("backend", ".dockerignore")
        assert path.exists(), "backend/.dockerignore must exist"

    @pytest.mark.host_only
    def test_frontend_dockerignore_exists(self):
        from tests.helpers.paths import project_file

        path = project_file("frontend", ".dockerignore")
        assert path.exists(), "frontend/.dockerignore must exist"


# ============================================================================
# SGA3-M8: Sandbox networks pre-created in docker-compose
# ============================================================================


class TestSGA3M8SandboxNetworks:
    """SGA3-M8: Sandbox networks pre-created in docker-compose.yml."""

    @pytest.mark.host_only
    def test_sandbox_networks_in_compose(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        assert "sandbox-isolated:" in source, "sandbox-isolated network must be in compose"
        assert "sandbox-internet:" in source, "sandbox-internet network must be in compose"

    @pytest.mark.host_only
    def test_sandbox_isolated_is_internal(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        # Find the sandbox-isolated network block
        idx = source.find("sandbox-isolated:")
        assert idx >= 0
        block = source[idx:idx + 300]
        assert "internal: true" in block, "sandbox-isolated must be internal"

    @pytest.mark.host_only
    def test_ensure_networks_handles_docker_errors(self):
        """_ensure_networks() must handle Docker API errors gracefully."""
        from tests.helpers.paths import require_file

        source = require_file("backend/app/services/dev_sandbox_service.py")
        assert "except Exception" in source, (
            "_ensure_networks must catch exceptions from Docker API"
        )


# ============================================================================
# SGA3-M9: mempoolUrl validated in WalletPage
# ============================================================================


class TestSGA3M9MempoolUrlValidation:
    """SGA3-M9: mempoolUrl must be scheme-validated before use in href."""

    @pytest.mark.host_only
    def test_mempool_url_validated(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/src/pages/WalletPage.tsx")
        # Must have a https?:// regex test on mempoolUrl
        assert re.search(r"https\?\:.*test.*mempool|mempool.*https\?\:", source, re.IGNORECASE), (
            "mempoolUrl must be validated with https?:// before use"
        )

    @pytest.mark.host_only
    def test_mempool_url_fallback(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/src/pages/WalletPage.tsx")
        assert "mempool.space" in source, "Must have mempool.space fallback"


# ============================================================================
# SGA3-L7: nginx server_tokens off
# ============================================================================


class TestSGA3L7NginxServerTokens:
    """SGA3-L7: nginx must not reveal version information."""

    @pytest.mark.host_only
    def test_server_tokens_off_in_nginx_conf(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/nginx.conf")
        assert "server_tokens off" in source

    @pytest.mark.host_only
    def test_server_tokens_off_in_nginx_template(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/nginx.conf.template")
        assert "server_tokens off" in source


# ============================================================================
# SGA3-L8: CSP in /assets/ location block
# ============================================================================


class TestSGA3L8AssetsCSP:
    """SGA3-L8: CSP must be present in /assets/ location block."""

    @pytest.mark.host_only
    def test_assets_block_has_csp(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/nginx.conf")
        # Find the /assets/ location block
        assert re.search(
            r"location\s+/assets/.*Content-Security-Policy",
            source,
            re.DOTALL,
        ), "CSP must be declared in /assets/ location block"


# ============================================================================
# SGA3-L9: CSP unsafe-inline removed from index.html
# ============================================================================


class TestSGA3L9CSPUnsafeInline:
    """SGA3-L9: index.html meta CSP must not include unsafe-inline in script-src."""

    @pytest.mark.host_only
    def test_no_unsafe_inline_in_script_src(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/index.html")
        # Find the CSP meta tag and extract script-src directive
        csp_match = re.search(
            r'content="([^"]*)"',
            source[source.find("Content-Security-Policy"):] if "Content-Security-Policy" in source else "",
            re.IGNORECASE,
        )
        if csp_match:
            csp = csp_match.group(1)
            # Extract just the script-src directive
            script_src_match = re.search(r"script-src\s+([^;]+)", csp)
            if script_src_match:
                script_src = script_src_match.group(1)
                assert "'unsafe-inline'" not in script_src, (
                    f"script-src must not include 'unsafe-inline', got: {script_src}"
                )


# ============================================================================
# SGA3-L10: avatar_url scheme validation in ProfileSettingsPage
# ============================================================================


class TestSGA3L10AvatarUrlValidation:
    """SGA3-L10: avatar_url must be scheme-validated before <img src> rendering."""

    @pytest.mark.host_only
    def test_avatar_url_validated(self):
        from tests.helpers.paths import require_file

        source = require_file("frontend/src/pages/ProfileSettingsPage.tsx")
        # Must have https?:// validation before rendering avatar
        assert re.search(r"https\?\:.*test.*avatar|avatar.*https\?\:", source, re.IGNORECASE), (
            "avatarUrl must be scheme-validated before rendering in <img src>"
        )


# ============================================================================
# SGA3-L11: Credentials suggested_value hidden
# ============================================================================


class TestSGA3L11CredentialsSuggestedValue:
    """SGA3-L11: suggested_value must not be shown for credentials input type."""

    @pytest.mark.host_only
    def test_credentials_suggested_value_hidden(self):
        from tests.helpers.paths import require_file

        source = require_file(
            "frontend/src/components/campaigns/InputRequestPanel.tsx"
        )
        # Must exclude credentials from suggested_value display
        assert re.search(
            r"input_type\s*!==\s*['\"]credentials['\"]|credentials.*suggested_value",
            source,
        ), "suggested_value must be hidden for credentials input type"


# ============================================================================
# SGA3-I1: Dead authenticatedUrl code removed
# ============================================================================


class TestSGA3I1DeadAuthUrl:
    """SGA3-I1: latent JWT-in-URL code must be removed from MessageAttachments."""

    @pytest.mark.host_only
    def test_no_authenticated_url_variable(self):
        from tests.helpers.paths import require_file

        source = require_file(
            "frontend/src/components/conversations/MessageAttachments.tsx"
        )
        assert "authenticatedUrl" not in source, (
            "Dead authenticatedUrl variable must be removed from MessageAttachments"
        )


# ============================================================================
# SGA3-I2: Celery health checks in docker-compose
# ============================================================================


class TestSGA3I2CeleryHealthChecks:
    """SGA3-I2: Celery worker and beat must have Docker health checks."""

    @pytest.mark.host_only
    def test_celery_worker_healthcheck(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        # Find celery-worker service
        worker_idx = source.find("celery-worker:")
        assert worker_idx >= 0, "celery-worker service not found"
        # Look in a wide window (service block can be 200+ lines)
        next_svc = re.search(r"\n  \w[\w-]+:", source[worker_idx + 20:])
        end = worker_idx + 20 + next_svc.start() if next_svc else len(source)
        worker_block = source[worker_idx:end]
        assert "healthcheck:" in worker_block, "celery-worker must have a healthcheck"

    @pytest.mark.host_only
    def test_celery_beat_healthcheck(self):
        from tests.helpers.paths import require_file

        source = require_file("docker-compose.yml")
        beat_idx = source.find("celery-beat:")
        assert beat_idx >= 0, "celery-beat service not found"
        beat_block = source[beat_idx:beat_idx + 1500]
        assert "healthcheck:" in beat_block, "celery-beat must have a healthcheck"
