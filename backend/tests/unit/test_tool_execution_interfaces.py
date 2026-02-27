"""Tests for Tool Execution Service interface types (CLI, Python SDK, MCP)."""
import pytest
import json
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from uuid import uuid4
from datetime import datetime

from app.services.tool_execution_service import ToolExecutor, ToolExecutionService, ToolExecutionResult
from app.models import Tool, ToolExecution, ToolExecutionStatus


class TestToolExecutionServiceInterfaces:
    """Tests for the various interface types in ToolExecutionService."""
    
    @pytest.fixture
    def executor(self):
        """Create an executor instance."""
        return ToolExecutor()
    
    @pytest.fixture
    def mock_tool(self):
        """Create a mock tool object."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.name = "Test Tool"
        tool.slug = "test-tool"
        tool.timeout_seconds = 30
        return tool
    

class TestCLIInterfaceExecutor:
    """Tests for CLI interface type execution."""
    
    @pytest.fixture
    def executor(self):
        """Create an executor instance."""
        return ToolExecutor()
    
    @pytest.fixture
    def cli_tool(self):
        """Create a mock CLI tool."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.name = "FFmpeg Tool"
        tool.slug = "ffmpeg"
        tool.timeout_seconds = 60
        tool.interface_type = "cli"
        tool.interface_config = {
            "command": "ffmpeg",
            "working_dir": "/tmp/workspace",
            "templates": {
                "convert": {
                    "args": ["-i", "{{input}}", "-c:v", "libx264", "{{output}}"],
                    "env": {"CUDA_VISIBLE_DEVICES": "0"}
                },
                "info": {
                    "args": ["-i", "{{input}}", "-f", "null", "-"],
                    "env": {}
                }
            }
        }
        return tool
    
    @pytest.mark.asyncio
    async def test_cli_template_substitution(self, executor, cli_tool):
        """Test CLI argument template substitution."""
        params = {
            "template": "convert",
            "input": "input.mp4",
            "output": "output.mp4"
        }
        
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"Success", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process
            
            result = await executor._execute_cli(cli_tool, params, cli_tool.interface_config)
            
            assert result.success
            # Verify correct args were passed
            call_args = mock_exec.call_args
            assert "ffmpeg" in call_args[0]
            assert "-i" in call_args[0]
            assert "input.mp4" in call_args[0]
            assert "output.mp4" in call_args[0]
    
    @pytest.mark.asyncio
    async def test_cli_missing_template(self, executor, cli_tool):
        """Test CLI execution with missing template."""
        params = {
            "template": "nonexistent",
            "input": "test.mp4"
        }
        
        result = await executor._execute_cli(cli_tool, params, cli_tool.interface_config)
        
        assert not result.success
        assert "Template 'nonexistent' not found" in result.error
    
    @pytest.mark.asyncio
    async def test_cli_env_variables_from_template(self, executor, cli_tool):
        """Test environment variables from CLI template are applied."""
        # The template already has CUDA_VISIBLE_DEVICES=0
        params = {
            "template": "convert",
            "input": "in.mp4",
            "output": "out.mp4"
        }
        
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"OK", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process
            
            result = await executor._execute_cli(cli_tool, params, cli_tool.interface_config)
            
            # Check env from template was applied
            call_kwargs = mock_exec.call_args[1]
            # The implementation copies os.environ and updates with template env
            assert call_kwargs.get('env', {}).get('CUDA_VISIBLE_DEVICES') == '0'
    
    @pytest.mark.asyncio
    async def test_cli_failure_handling(self, executor, cli_tool):
        """Test CLI execution failure handling."""
        params = {
            "template": "convert",
            "input": "bad.mp4",
            "output": "out.mp4"
        }
        
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"Error: File not found"))
            mock_process.returncode = 1
            mock_exec.return_value = mock_process
            
            result = await executor._execute_cli(cli_tool, params, cli_tool.interface_config)
            
            assert not result.success
            assert "File not found" in result.error


class TestPythonSDKInterfaceExecutor:
    """Tests for Python SDK interface type execution."""
    
    @pytest.fixture
    def executor(self):
        """Create a service instance."""
        return ToolExecutor()
    
    @pytest.fixture
    def sdk_tool(self):
        """Create a mock Python SDK tool."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.name = "OpenAI Tool"
        tool.slug = "openai-chat"
        tool.timeout_seconds = 120
        tool.interface_type = "python_sdk"
        tool.interface_config = {
            "module": "openai",
            "class": "OpenAI",
            "init_args": {
                "api_key": "$OPENAI_API_KEY"
            },
            "method": "chat.completions.create",
            "method_kwargs_mapping": {
                "model": "$.model",
                "messages": "$.messages"
            }
        }
        return tool
    
    @pytest.mark.asyncio
    async def test_sdk_module_import(self, executor, sdk_tool):
        """Test Python SDK module import and execution."""
        params = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        
        mock_client = MagicMock()
        mock_completions = MagicMock()
        mock_create = MagicMock()
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"id": "test-id", "choices": []}
        mock_create.return_value = mock_result
        mock_completions.create = mock_create
        mock_client.chat.completions = mock_completions
        
        mock_module = MagicMock()
        mock_module.OpenAI.return_value = mock_client
        
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            with patch('importlib.import_module', return_value=mock_module):
                result = await executor._execute_python_sdk(sdk_tool, params, sdk_tool.interface_config)
                
                # Should have attempted to create client with resolved API key
                mock_module.OpenAI.assert_called_once()
                init_call_kwargs = mock_module.OpenAI.call_args[1]
                assert init_call_kwargs.get('api_key') == 'test-key'
    
    @pytest.mark.asyncio
    async def test_sdk_function_only(self, executor, sdk_tool):
        """Test Python SDK with function (no class)."""
        sdk_tool.interface_config = {
            "module": "httpx",
            "function": "get",  # Use 'function' not 'method' for function-only
        }
        params = {"url": "https://example.com"}
        
        mock_response = MagicMock()
        mock_response.json.side_effect = Exception("not json")
        mock_response.text = '{"ok": true}'
        mock_response.__str__ = lambda self: '{"ok": true}'
        
        mock_module = MagicMock()
        mock_module.get.return_value = mock_response
        
        with patch('importlib.import_module', return_value=mock_module):
            result = await executor._execute_python_sdk(sdk_tool, params, sdk_tool.interface_config)
        
        assert result.success
        mock_module.get.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_sdk_missing_module(self, executor, sdk_tool):
        """Test Python SDK with non-existent module."""
        sdk_tool.interface_config = {
            "module": "nonexistent_module_xyz",
            "method": "run"
        }
        params = {}
        
        result = await executor._execute_python_sdk(sdk_tool, params, sdk_tool.interface_config)
        
        assert not result.success
        assert "module" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_sdk_nested_method_call(self, executor, sdk_tool):
        """Test Python SDK with nested method path."""
        mock_client = MagicMock()
        mock_level1 = MagicMock()
        mock_level2 = MagicMock()
        mock_method = MagicMock(return_value={"result": "success"})
        mock_level2.execute = mock_method
        mock_level1.deep = mock_level2
        mock_client.nested = mock_level1
        
        mock_module = MagicMock()
        mock_module.Client.return_value = mock_client
        
        sdk_tool.interface_config = {
            "module": "httpx",
            "class": "Client",
            "method": "nested.deep.execute",
        }
        params = {"data": "test"}
        
        with patch('importlib.import_module', return_value=mock_module):
            result = await executor._execute_python_sdk(sdk_tool, params, sdk_tool.interface_config)
            
            assert result.success
            mock_method.assert_called_once()


class TestMCPInterfaceExecutor:
    """Tests for MCP (Model Context Protocol) interface type execution."""
    
    @pytest.fixture
    def executor(self):
        """Create a service instance."""
        return ToolExecutor()
    
    @pytest.fixture
    def mcp_stdio_tool(self):
        """Create a mock MCP stdio tool."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.name = "Filesystem MCP"
        tool.slug = "fs-mcp"
        tool.timeout_seconds = 30
        tool.interface_type = "mcp"
        tool.interface_config = {
            "transport": "stdio",
            "server_command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "tool_name": "read_file"
        }
        return tool
    
    @pytest.fixture
    def mcp_http_tool(self):
        """Create a mock MCP HTTP tool."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.name = "Remote MCP Server"
        tool.slug = "remote-mcp"
        tool.timeout_seconds = 30
        tool.interface_type = "mcp"
        tool.interface_config = {
            "transport": "http",
            "server_url": "http://localhost:8080/mcp",
            "tool_name": "execute_code"
        }
        return tool
    
    @pytest.mark.asyncio
    async def test_mcp_stdio_execution(self, executor, mcp_stdio_tool):
        """Test MCP stdio transport execution."""
        params = {"path": "/tmp/test.txt"}
        
        # Mock the subprocess and JSON-RPC communication
        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "File contents"}]
            }
        }
        
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            # communicate() returns (stdout, stderr)
            mock_process.communicate = AsyncMock(
                return_value=(json.dumps(mock_response).encode() + b'\n', b"")
            )
            mock_exec.return_value = mock_process
            
            result = await executor._execute_mcp(mcp_stdio_tool, params, mcp_stdio_tool.interface_config)
            
            # Should have spawned the MCP server
            mock_exec.assert_called()
            call_args = mock_exec.call_args[0]
            assert "npx" in call_args
            assert result.success
    
    @pytest.mark.asyncio
    async def test_mcp_http_execution(self, executor, mcp_http_tool):
        """Test MCP HTTP transport execution."""
        params = {"code": "print('hello')"}
        
        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "hello\n"}]
            }
        }
        
        with patch('httpx.AsyncClient') as mock_client_class, \
             patch('app.core.security.validate_target_url', return_value="http://localhost:8080/mcp"):
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.json.return_value = mock_response
            mock_client.post = AsyncMock(return_value=mock_response_obj)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            result = await executor._execute_mcp(mcp_http_tool, params, mcp_http_tool.interface_config)
            
            # Should have made HTTP request to MCP server
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "http://localhost:8080/mcp" in str(call_args)
    
    @pytest.mark.asyncio
    async def test_mcp_error_response(self, executor, mcp_http_tool):
        """Test MCP error response handling."""
        params = {"code": "invalid"}
        
        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32000,
                "message": "Execution failed"
            }
        }
        
        with patch('httpx.AsyncClient') as mock_client_class, \
             patch('app.core.security.validate_target_url', return_value="http://localhost:8080/mcp"):
            mock_client = AsyncMock()
            mock_response_obj = MagicMock()
            mock_response_obj.json.return_value = mock_response
            mock_client.post = AsyncMock(return_value=mock_response_obj)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            result = await executor._execute_mcp(mcp_http_tool, params, mcp_http_tool.interface_config)
            
            assert not result.success
            assert "Execution failed" in result.error
    
    @pytest.mark.asyncio
    async def test_mcp_missing_transport(self, executor, mcp_stdio_tool):
        """Test MCP with missing transport config."""
        mcp_stdio_tool.interface_config = {
            "tool_name": "read_file"
            # Missing transport
        }
        params = {"path": "/test"}
        
        result = await executor._execute_mcp(mcp_stdio_tool, params, mcp_stdio_tool.interface_config)
        
        # Should default to stdio but fail due to missing server_command
        assert not result.success


class TestDynamicInterfaceRouting:
    """Tests for the _execute_dynamic method's interface routing."""
    
    @pytest.fixture
    def executor(self):
        """Create an executor instance."""
        return ToolExecutor()
    
    @pytest.mark.asyncio
    async def test_routes_to_rest_api(self, executor):
        """Test routing to REST API executor."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.interface_type = "rest_api"
        tool.interface_config = {"base_url": "http://api.test"}
        
        with patch.object(executor, '_execute_rest_api', new_callable=AsyncMock) as mock_rest:
            mock_rest.return_value = ToolExecutionResult(success=True, output="ok")
            
            result = await executor._execute_dynamic(tool, {"param": "value"})
            
            mock_rest.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_routes_to_cli(self, executor):
        """Test routing to CLI executor."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.interface_type = "cli"
        tool.interface_config = {"command": "echo"}
        
        with patch.object(executor, '_execute_cli', new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = ToolExecutionResult(success=True, output="hello")
            
            result = await executor._execute_dynamic(tool, {"text": "hello"})
            
            mock_cli.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_routes_to_python_sdk(self, executor):
        """Test routing to Python SDK executor."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.interface_type = "python_sdk"
        tool.interface_config = {"module": "test"}
        
        with patch.object(executor, '_execute_python_sdk', new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = ToolExecutionResult(success=True, output={"result": 1})
            
            result = await executor._execute_dynamic(tool, {})
            
            mock_sdk.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_routes_to_mcp(self, executor):
        """Test routing to MCP executor."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.interface_type = "mcp"
        tool.interface_config = {"transport": "stdio"}
        
        with patch.object(executor, '_execute_mcp', new_callable=AsyncMock) as mock_mcp:
            mock_mcp.return_value = ToolExecutionResult(success=True, output="mcp result")
            
            result = await executor._execute_dynamic(tool, {})
            
            mock_mcp.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_unsupported_interface_type(self, executor):
        """Test unsupported interface type returns error."""
        tool = MagicMock(spec=Tool)
        tool.id = uuid4()
        tool.interface_type = "unknown_type"
        tool.interface_config = {}
        
        result = await executor._execute_dynamic(tool, {})
        
        assert not result.success
        assert "Unsupported interface type" in result.error
        assert "unknown_type" in result.error


class TestEnvVarResolution:
    """Tests for environment variable resolution helper."""
    
    @pytest.fixture
    def executor(self):
        """Create an executor instance."""
        return ToolExecutor()
    
    def test_resolves_single_env_var(self, executor):
        """Test resolving a single environment variable."""
        data = {"key": "$OPENAI_API_KEY"}
        
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'resolved_value'}):
            result = executor._resolve_env_vars(data)
            
            assert result["key"] == "resolved_value"
    
    def test_resolves_nested_env_vars(self, executor):
        """Test resolving nested environment variables."""
        data = {
            "level1": {
                "level2": "$ANTHROPIC_API_KEY",
                "other": "static"
            }
        }
        
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'nested_value'}):
            result = executor._resolve_env_vars(data)
            
            assert result["level1"]["level2"] == "nested_value"
            assert result["level1"]["other"] == "static"
    
    def test_missing_env_var_returns_empty(self, executor):
        """Test missing environment variable returns empty string."""
        data = {"key": "$OPENAI_API_KEY"}
        
        # Ensure var doesn't exist
        with patch.dict('os.environ', {}, clear=True):
            result = executor._resolve_env_vars(data)
            
            # Implementation returns empty string for missing vars
            assert result["key"] == ""
    
    def test_non_string_values_unchanged(self, executor):
        """Test non-string values pass through unchanged."""
        data = {
            "number": 42,
            "boolean": True,
            "null": None,
            "float": 3.14
        }
        
        result = executor._resolve_env_vars(data)
        
        assert result["number"] == 42
        assert result["boolean"] is True
        assert result["null"] is None
        assert result["float"] == 3.14
