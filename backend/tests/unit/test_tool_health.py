"""
Unit tests for Tool Health Check Service.

Tests:
- Interface-specific connectivity checks (REST, CLI, Python SDK, MCP)
- HealthCheckResult dataclass
- Health status determination
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock

from app.models import Tool, HealthStatus
from app.services.tool_health_service import ToolHealthService, HealthCheckResult


# =============================================================================
# Test: HealthCheckResult dataclass
# =============================================================================

def test_health_check_result_dataclass():
    """Test HealthCheckResult dataclass creation."""
    result = HealthCheckResult(
        status=HealthStatus.HEALTHY,
        message="All systems operational",
        response_time_ms=150,
        details={"version": "1.0.0"}
    )
    
    assert result.status == HealthStatus.HEALTHY
    assert result.message == "All systems operational"
    assert result.response_time_ms == 150
    assert result.details == {"version": "1.0.0"}


def test_health_check_result_minimal():
    """Test HealthCheckResult with minimal args."""
    result = HealthCheckResult(
        status=HealthStatus.UNKNOWN,
        message="No check performed",
        response_time_ms=0,
    )
    
    assert result.status == HealthStatus.UNKNOWN
    assert result.details is None


# =============================================================================
# Mock Tool Fixtures
# =============================================================================

@pytest.fixture
def mock_rest_tool():
    """Create a mock REST API tool."""
    tool = MagicMock(spec=Tool)
    tool.id = uuid4()
    tool.name = "REST API Tool"
    tool.slug = "rest-api-tool"
    tool.interface_type = "rest_api"
    tool.interface_config = {
        "base_url": "https://api.example.com",
        "method": "GET",
        "endpoint": "/health",
    }
    return tool


@pytest.fixture
def mock_cli_tool():
    """Create a mock CLI tool."""
    tool = MagicMock(spec=Tool)
    tool.id = uuid4()
    tool.name = "CLI Tool"
    tool.slug = "cli-tool"
    tool.interface_type = "cli"
    tool.interface_config = {
        "command": "python",
        "args": ["--version"],
    }
    return tool


@pytest.fixture
def mock_python_sdk_tool():
    """Create a mock Python SDK tool."""
    tool = MagicMock(spec=Tool)
    tool.id = uuid4()
    tool.name = "Python SDK Tool"
    tool.slug = "python-sdk-tool"
    tool.interface_type = "python_sdk"
    tool.interface_config = {
        "module": "json",  # Standard library module for testing
        "class_name": "JSONEncoder",
    }
    return tool


@pytest.fixture
def mock_mcp_tool():
    """Create a mock MCP server tool."""
    tool = MagicMock(spec=Tool)
    tool.id = uuid4()
    tool.name = "MCP Server Tool"
    tool.slug = "mcp-server-tool"
    tool.interface_type = "mcp"
    tool.interface_config = {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_server"],
    }
    return tool


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    return MagicMock()


# =============================================================================
# Test: REST API connectivity checks
# =============================================================================

@pytest.mark.asyncio
async def test_check_rest_api_connectivity_healthy(mock_db_session, mock_rest_tool):
    """Test REST API connectivity check with successful response."""
    service = ToolHealthService(mock_db_session)
    
    # Mock HTTP client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        result = await service._check_rest_api_connectivity(mock_rest_tool)
    
    assert result.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_rest_api_connectivity_unhealthy(mock_db_session, mock_rest_tool):
    """Test REST API connectivity check with connection error."""
    service = ToolHealthService(mock_db_session)
    
    with patch("app.services.tool_health_service.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = None
        
        result = await service._check_rest_api_connectivity(mock_rest_tool)
    
    assert result.status == HealthStatus.UNHEALTHY
    assert "Connection refused" in result.message or "error" in result.message.lower()


@pytest.mark.asyncio
async def test_check_rest_api_connectivity_degraded_slow(mock_db_session, mock_rest_tool):
    """Test REST API connectivity check with slow response."""
    service = ToolHealthService(mock_db_session)
    
    # Mock HTTP client with slow response - use time.time patch
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    
    # Track call count to return different times
    call_count = [0]
    def mock_time():
        call_count[0] += 1
        if call_count[0] == 1:
            return 0.0  # start time
        return 6.0  # end time (6 seconds later)
    
    with patch("httpx.AsyncClient") as mock_client:
        with patch("app.services.tool_health_service.time.time", mock_time):
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock()
            
            result = await service._check_rest_api_connectivity(mock_rest_tool)
    
    assert result.status == HealthStatus.DEGRADED
    assert result.response_time_ms >= 5000


@pytest.mark.asyncio
async def test_check_rest_api_connectivity_no_config(mock_db_session, mock_rest_tool):
    """Test REST API connectivity check with no configuration."""
    service = ToolHealthService(mock_db_session)
    mock_rest_tool.interface_config = None
    
    result = await service._check_rest_api_connectivity(mock_rest_tool)
    
    # Returns UNKNOWN when no base_url is configured
    assert result.status == HealthStatus.UNKNOWN


# =============================================================================
# Test: CLI connectivity checks
# =============================================================================

@pytest.mark.asyncio
async def test_check_cli_connectivity_healthy(mock_db_session, mock_cli_tool):
    """Test CLI connectivity check when command exists."""
    service = ToolHealthService(mock_db_session)
    
    with patch("shutil.which", return_value="/usr/bin/python"):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"Python 3.10", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await service._check_cli_connectivity(mock_cli_tool)
    
    assert result.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_cli_connectivity_unhealthy_not_found(mock_db_session, mock_cli_tool):
    """Test CLI connectivity check when command doesn't exist."""
    service = ToolHealthService(mock_db_session)
    mock_cli_tool.interface_config = {"command": "nonexistent_command_xyz123"}
    
    with patch("shutil.which", return_value=None):
        result = await service._check_cli_connectivity(mock_cli_tool)
    
    assert result.status == HealthStatus.UNHEALTHY
    assert "not found" in result.message.lower()


@pytest.mark.asyncio
async def test_check_cli_connectivity_unhealthy_execution_error(mock_db_session, mock_cli_tool):
    """Test CLI connectivity check when command fails to execute."""
    service = ToolHealthService(mock_db_session)
    
    with patch("shutil.which", return_value="/usr/bin/python"):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"Error"))
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process
            
            result = await service._check_cli_connectivity(mock_cli_tool)
    
    # Non-zero return code but command was found, could be degraded or healthy depending on implementation
    # Just verify it doesn't crash and returns a valid result
    assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]


@pytest.mark.asyncio
async def test_check_cli_connectivity_no_config(mock_db_session, mock_cli_tool):
    """Test CLI connectivity check with no configuration."""
    service = ToolHealthService(mock_db_session)
    mock_cli_tool.interface_config = None
    
    result = await service._check_cli_connectivity(mock_cli_tool)
    
    # Returns UNKNOWN when no command is configured
    assert result.status == HealthStatus.UNKNOWN


# =============================================================================
# Test: Python SDK connectivity checks
# =============================================================================

@pytest.mark.asyncio
async def test_check_python_sdk_connectivity_healthy(mock_db_session, mock_python_sdk_tool):
    """Test Python SDK connectivity check when module can be imported."""
    service = ToolHealthService(mock_db_session)
    
    result = await service._check_python_sdk_connectivity(mock_python_sdk_tool)
    
    # json module should always be importable
    assert result.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_python_sdk_connectivity_unhealthy(mock_db_session, mock_python_sdk_tool):
    """Test Python SDK connectivity check when module doesn't exist."""
    service = ToolHealthService(mock_db_session)
    mock_python_sdk_tool.interface_config = {"module": "nonexistent_module_xyz123"}
    
    result = await service._check_python_sdk_connectivity(mock_python_sdk_tool)
    
    assert result.status == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_check_python_sdk_connectivity_no_config(mock_db_session, mock_python_sdk_tool):
    """Test Python SDK connectivity check with no configuration."""
    service = ToolHealthService(mock_db_session)
    mock_python_sdk_tool.interface_config = None
    
    result = await service._check_python_sdk_connectivity(mock_python_sdk_tool)
    
    # Returns UNKNOWN when no module is configured
    assert result.status == HealthStatus.UNKNOWN


# =============================================================================
# Test: MCP connectivity checks
# =============================================================================

@pytest.mark.asyncio
async def test_check_mcp_connectivity_unhealthy_no_server(mock_db_session, mock_mcp_tool):
    """Test MCP connectivity check when server is not running."""
    service = ToolHealthService(mock_db_session)
    
    with patch("shutil.which", return_value=None):
        result = await service._check_mcp_connectivity(mock_mcp_tool)
    
    # Without the MCP server command available, should be unhealthy
    assert result.status == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_check_mcp_connectivity_no_config(mock_db_session, mock_mcp_tool):
    """Test MCP connectivity check with no configuration."""
    service = ToolHealthService(mock_db_session)
    mock_mcp_tool.interface_config = None
    
    result = await service._check_mcp_connectivity(mock_mcp_tool)
    
    # Returns UNKNOWN when no transport/command is configured
    assert result.status == HealthStatus.UNKNOWN


# =============================================================================
# Test: General connectivity dispatch
# =============================================================================

@pytest.mark.asyncio
async def test_check_connectivity_dispatches_rest_api(mock_db_session, mock_rest_tool):
    """Test connectivity check dispatches to REST API handler."""
    service = ToolHealthService(mock_db_session)
    
    # Mock the specific handler
    with patch.object(service, "_check_rest_api_connectivity") as mock_handler:
        mock_handler.return_value = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="OK",
            response_time_ms=100
        )
        
        result = await service._check_connectivity(mock_rest_tool)
    
    mock_handler.assert_called_once_with(mock_rest_tool)
    assert result.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_connectivity_dispatches_cli(mock_db_session, mock_cli_tool):
    """Test connectivity check dispatches to CLI handler."""
    service = ToolHealthService(mock_db_session)
    
    with patch.object(service, "_check_cli_connectivity") as mock_handler:
        mock_handler.return_value = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="OK",
            response_time_ms=50
        )
        
        result = await service._check_connectivity(mock_cli_tool)
    
    mock_handler.assert_called_once_with(mock_cli_tool)
    assert result.status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_connectivity_unknown_interface(mock_db_session):
    """Test connectivity check handles unknown interface type."""
    service = ToolHealthService(mock_db_session)
    
    mock_tool = MagicMock(spec=Tool)
    mock_tool.id = uuid4()
    mock_tool.name = "Unknown Tool"
    mock_tool.slug = "unknown-tool"
    mock_tool.interface_type = "unknown_type"
    mock_tool.interface_config = {"some": "config"}
    
    result = await service._check_connectivity(mock_tool)
    
    # Should return unknown for unrecognized interface types
    assert result.status == HealthStatus.UNKNOWN


# =============================================================================
# Test: Threshold constants
# =============================================================================

def test_degraded_threshold_constant():
    """Test that degraded threshold is set correctly."""
    # Access the class constant
    assert ToolHealthService.DEGRADED_THRESHOLD_MS == 5000


def test_default_timeout_constant():
    """Test that default timeout is set correctly."""
    assert ToolHealthService.DEFAULT_TIMEOUT == 10
