"""
Tool Health Check Service - Validates and monitors tool health.

Provides:
- Interface-specific health checks (REST API, CLI, Python SDK, MCP)
- Configuration validation
- Health status tracking
- Historical health data
"""
import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import httpx

from app.core.datetime_utils import utc_now, ensure_utc
from sqlalchemy import select, update, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Tool, ToolHealthCheck, HealthStatus

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    status: HealthStatus
    message: str
    response_time_ms: int
    details: Optional[Dict[str, Any]] = None


class ToolHealthService:
    """
    Service for checking and monitoring tool health.
    
    Supports interface-specific health checks:
    - REST API: HTTP connectivity check
    - CLI: Command availability check
    - Python SDK: Module import check
    - MCP: Server connectivity check
    """
    
    # Timeout for health checks (seconds)
    DEFAULT_TIMEOUT = 10
    
    # Response time thresholds (ms)
    DEGRADED_THRESHOLD_MS = 5000  # > 5s = degraded
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # Main Health Check Methods
    # =========================================================================
    
    async def check_tool_health(
        self,
        tool_id: UUID,
        user_id: Optional[UUID] = None,
        check_type: str = "full",
    ) -> ToolHealthCheck:
        """
        Perform a health check on a tool.
        
        Args:
            tool_id: Tool to check
            user_id: User who triggered (None if automatic)
            check_type: Type of check (connectivity, validation, full)
            
        Returns:
            ToolHealthCheck record with results
        """
        # Get the tool
        result = await self.db.execute(
            select(Tool).where(Tool.id == tool_id)
        )
        tool = result.scalar_one_or_none()
        
        if not tool:
            raise ValueError(f"Tool not found: {tool_id}")
        
        # Perform the health check based on interface type
        start_time = time.time()
        try:
            if check_type == "connectivity":
                check_result = await self._check_connectivity(tool)
            elif check_type == "validation":
                check_result = await self._check_validation(tool)
            else:  # full
                check_result = await self._check_full(tool)
        except Exception as e:
            logger.exception(f"Health check failed for tool {tool.slug}")
            check_result = HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {str(e)}",
                response_time_ms=int((time.time() - start_time) * 1000),
            )
        
        # Create health check record
        health_check = ToolHealthCheck(
            tool_id=tool_id,
            status=check_result.status.value,
            message=check_result.message,
            response_time_ms=check_result.response_time_ms,
            check_type=check_type,
            details=check_result.details,
            is_automatic=user_id is None,
            triggered_by_id=user_id,
        )
        self.db.add(health_check)
        
        # Update tool's health status
        await self.db.execute(
            update(Tool)
            .where(Tool.id == tool_id)
            .values(
                health_status=check_result.status.value,
                last_health_check=utc_now(),
                health_message=check_result.message,
                health_response_ms=check_result.response_time_ms,
            )
        )
        
        await self.db.commit()
        await self.db.refresh(health_check)
        
        return health_check
    
    async def check_all_tools(
        self,
        only_enabled: bool = True,
    ) -> List[ToolHealthCheck]:
        """
        Check health of all tools (or only those with health checks enabled).
        
        Args:
            only_enabled: If True, only check tools with health_check_enabled=True
            
        Returns:
            List of health check results
        """
        conditions = [Tool.status == "implemented"]
        if only_enabled:
            conditions.append(Tool.health_check_enabled == True)
        
        result = await self.db.execute(
            select(Tool).where(and_(*conditions))
        )
        tools = list(result.scalars().all())
        
        results = []
        for tool in tools:
            try:
                check = await self.check_tool_health(tool.id)
                results.append(check)
            except Exception as e:
                logger.error(f"Failed to check health of {tool.slug}: {e}")
        
        return results
    
    async def get_tools_needing_check(self) -> List[Tool]:
        """
        Get tools that need a health check based on their interval.
        
        Returns tools where:
        - health_check_enabled = True
        - last_health_check is None OR older than health_check_interval_minutes
        """
        now = utc_now()
        
        result = await self.db.execute(
            select(Tool).where(
                and_(
                    Tool.status == "implemented",
                    Tool.health_check_enabled == True,
                )
            )
        )
        tools = list(result.scalars().all())
        
        needs_check = []
        for tool in tools:
            if tool.last_health_check is None:
                needs_check.append(tool)
            else:
                interval_minutes = tool.health_check_interval_minutes or 60
                last_check = ensure_utc(tool.last_health_check)
                age_minutes = (now - last_check).total_seconds() / 60
                if age_minutes >= interval_minutes:
                    needs_check.append(tool)
        
        return needs_check
    
    # =========================================================================
    # Interface-Specific Checks
    # =========================================================================
    
    async def _check_connectivity(self, tool: Tool) -> HealthCheckResult:
        """Check basic connectivity for a tool."""
        interface_type = tool.interface_type or "rest_api"
        
        if interface_type == "rest_api":
            return await self._check_rest_api_connectivity(tool)
        elif interface_type == "cli":
            return await self._check_cli_connectivity(tool)
        elif interface_type == "python_sdk":
            return await self._check_python_sdk_connectivity(tool)
        elif interface_type == "mcp":
            return await self._check_mcp_connectivity(tool)
        else:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Unknown interface type: {interface_type}",
                response_time_ms=0,
            )
    
    async def _check_validation(self, tool: Tool) -> HealthCheckResult:
        """Validate tool configuration."""
        interface_type = tool.interface_type
        config = tool.interface_config or {}
        errors = []
        warnings = []
        
        if not interface_type:
            errors.append("No interface_type configured")
        
        if interface_type == "rest_api":
            if not config.get("base_url"):
                errors.append("Missing base_url in interface_config")
            if not config.get("endpoints"):
                warnings.append("No endpoints configured")
        
        elif interface_type == "cli":
            if not config.get("command"):
                errors.append("Missing command in interface_config")
        
        elif interface_type == "python_sdk":
            if not config.get("module"):
                errors.append("Missing module in interface_config")
        
        elif interface_type == "mcp":
            if not config.get("server_url") and not config.get("command"):
                errors.append("Missing server_url or command in interface_config")
        
        # Check input/output schemas
        if tool.input_schema:
            if not isinstance(tool.input_schema, dict):
                errors.append("input_schema must be a JSON object")
        
        if errors:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Validation failed: {'; '.join(errors)}",
                response_time_ms=0,
                details={"errors": errors, "warnings": warnings},
            )
        elif warnings:
            return HealthCheckResult(
                status=HealthStatus.DEGRADED,
                message=f"Validation passed with warnings: {'; '.join(warnings)}",
                response_time_ms=0,
                details={"warnings": warnings},
            )
        else:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Configuration valid",
                response_time_ms=0,
            )
    
    async def _check_full(self, tool: Tool) -> HealthCheckResult:
        """Perform full health check (validation + connectivity)."""
        # First validate configuration
        validation = await self._check_validation(tool)
        if validation.status == HealthStatus.UNHEALTHY:
            return validation
        
        # Then check connectivity
        connectivity = await self._check_connectivity(tool)
        
        # Combine results
        if connectivity.status == HealthStatus.UNHEALTHY:
            return connectivity
        elif connectivity.status == HealthStatus.DEGRADED or validation.status == HealthStatus.DEGRADED:
            return HealthCheckResult(
                status=HealthStatus.DEGRADED,
                message=f"{validation.message}; {connectivity.message}",
                response_time_ms=connectivity.response_time_ms,
                details={
                    "validation": validation.details,
                    "connectivity": connectivity.details,
                },
            )
        else:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="All checks passed",
                response_time_ms=connectivity.response_time_ms,
                details=connectivity.details,
            )
    
    async def _check_rest_api_connectivity(self, tool: Tool) -> HealthCheckResult:
        """Check REST API connectivity."""
        config = tool.interface_config or {}
        base_url = config.get("base_url")
        health_endpoint = config.get("health_endpoint", "/")
        
        if not base_url:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No base_url configured",
                response_time_ms=0,
            )
        
        # Build health check URL
        url = base_url.rstrip("/") + health_endpoint
        
        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Add auth if configured
                headers = {}
                auth = config.get("auth", {})
                if auth.get("type") == "bearer":
                    # Note: In production, token would come from secure storage
                    headers["Authorization"] = f"Bearer {auth.get('token', 'PLACEHOLDER')}"
                elif auth.get("type") == "api_key":
                    header_name = auth.get("header_name", "X-API-Key")
                    headers[header_name] = auth.get("api_key", "PLACEHOLDER")
                
                response = await client.get(url, headers=headers)
                response_time_ms = int((time.time() - start_time) * 1000)
                
                if response.status_code < 400:
                    status = HealthStatus.DEGRADED if response_time_ms > self.DEGRADED_THRESHOLD_MS else HealthStatus.HEALTHY
                    return HealthCheckResult(
                        status=status,
                        message=f"HTTP {response.status_code} in {response_time_ms}ms",
                        response_time_ms=response_time_ms,
                        details={"status_code": response.status_code, "url": url},
                    )
                else:
                    return HealthCheckResult(
                        status=HealthStatus.UNHEALTHY,
                        message=f"HTTP {response.status_code}: {response.text[:200]}",
                        response_time_ms=response_time_ms,
                        details={"status_code": response.status_code, "url": url},
                    )
        except httpx.TimeoutException:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout after {self.DEFAULT_TIMEOUT}s",
                response_time_ms=self.DEFAULT_TIMEOUT * 1000,
                details={"url": url},
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {str(e)}",
                response_time_ms=int((time.time() - start_time) * 1000),
                details={"url": url, "error": str(e)},
            )
    
    async def _check_cli_connectivity(self, tool: Tool) -> HealthCheckResult:
        """Check CLI tool availability."""
        config = tool.interface_config or {}
        command = config.get("command")
        
        if not command:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No command configured",
                response_time_ms=0,
            )
        
        # Extract the base command (first word)
        base_command = command.split()[0]
        
        start_time = time.time()
        
        # Check if command exists
        if shutil.which(base_command):
            # Try to get version
            try:
                version_cmd = config.get("version_command", f"{base_command} --version")
                result = subprocess.run(
                    version_cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                response_time_ms = int((time.time() - start_time) * 1000)
                
                version_output = result.stdout.strip() or result.stderr.strip()
                version_line = version_output.split('\n')[0][:100] if version_output else "unknown"
                
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Command available: {version_line}",
                    response_time_ms=response_time_ms,
                    details={"command": base_command, "version": version_line},
                )
            except subprocess.TimeoutExpired:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Command exists but version check timed out",
                    response_time_ms=5000,
                    details={"command": base_command},
                )
            except Exception as e:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Command exists but version check failed: {e}",
                    response_time_ms=int((time.time() - start_time) * 1000),
                    details={"command": base_command},
                )
        else:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Command not found: {base_command}",
                response_time_ms=int((time.time() - start_time) * 1000),
                details={"command": base_command},
            )
    
    async def _check_python_sdk_connectivity(self, tool: Tool) -> HealthCheckResult:
        """Check Python SDK module availability."""
        config = tool.interface_config or {}
        module_name = config.get("module")
        
        if not module_name:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No module configured",
                response_time_ms=0,
            )
        
        start_time = time.time()
        
        try:
            # Try to import the module
            import importlib
            module = importlib.import_module(module_name)
            response_time_ms = int((time.time() - start_time) * 1000)
            
            # Try to get version
            version = getattr(module, '__version__', 'unknown')
            
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message=f"Module available: {module_name} v{version}",
                response_time_ms=response_time_ms,
                details={"module": module_name, "version": version},
            )
        except ImportError as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Module not found: {module_name} ({e})",
                response_time_ms=int((time.time() - start_time) * 1000),
                details={"module": module_name, "error": str(e)},
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Module import failed: {str(e)}",
                response_time_ms=int((time.time() - start_time) * 1000),
                details={"module": module_name, "error": str(e)},
            )
    
    async def _check_mcp_connectivity(self, tool: Tool) -> HealthCheckResult:
        """Check MCP server connectivity."""
        config = tool.interface_config or {}
        server_url = config.get("server_url")
        command = config.get("command")
        
        if server_url:
            # HTTP-based MCP server
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                    # MCP servers typically respond to POST with JSON-RPC
                    response = await client.post(
                        server_url,
                        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
                        headers={"Content-Type": "application/json"},
                    )
                    response_time_ms = int((time.time() - start_time) * 1000)
                    
                    if response.status_code < 400:
                        return HealthCheckResult(
                            status=HealthStatus.HEALTHY,
                            message=f"MCP server responding ({response_time_ms}ms)",
                            response_time_ms=response_time_ms,
                            details={"url": server_url},
                        )
                    else:
                        return HealthCheckResult(
                            status=HealthStatus.UNHEALTHY,
                            message=f"MCP server error: HTTP {response.status_code}",
                            response_time_ms=response_time_ms,
                            details={"url": server_url, "status_code": response.status_code},
                        )
            except Exception as e:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"MCP connection failed: {str(e)}",
                    response_time_ms=int((time.time() - start_time) * 1000),
                    details={"url": server_url, "error": str(e)},
                )
        
        elif command:
            # Stdio-based MCP server - check if command exists
            base_command = command.split()[0]
            if shutil.which(base_command):
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"MCP command available: {base_command}",
                    response_time_ms=0,
                    details={"command": base_command},
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"MCP command not found: {base_command}",
                    response_time_ms=0,
                    details={"command": base_command},
                )
        
        else:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No server_url or command configured",
                response_time_ms=0,
            )
    
    # =========================================================================
    # Query Methods
    # =========================================================================
    
    async def get_health_history(
        self,
        tool_id: UUID,
        limit: int = 50,
    ) -> List[ToolHealthCheck]:
        """Get health check history for a tool."""
        result = await self.db.execute(
            select(ToolHealthCheck)
            .where(ToolHealthCheck.tool_id == tool_id)
            .order_by(ToolHealthCheck.checked_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def get_unhealthy_tools(self) -> List[Tool]:
        """Get all tools with unhealthy status."""
        result = await self.db.execute(
            select(Tool).where(
                and_(
                    Tool.status == "implemented",
                    Tool.health_status.in_(["unhealthy", "degraded"]),
                )
            )
        )
        return list(result.scalars().all())
    
    async def get_health_summary(self) -> Dict[str, int]:
        """Get summary of tool health statuses."""
        result = await self.db.execute(
            select(
                Tool.health_status,
                func.count(Tool.id).label("count"),
            )
            .where(Tool.status == "implemented")
            .group_by(Tool.health_status)
        )
        
        summary = {"healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}
        for row in result:
            status = row[0] or "unknown"
            summary[status] = row[1]
        
        return summary
    
    async def enable_health_checks(
        self,
        tool_id: UUID,
        interval_minutes: int = 60,
    ) -> None:
        """Enable automatic health checks for a tool."""
        await self.db.execute(
            update(Tool)
            .where(Tool.id == tool_id)
            .values(
                health_check_enabled=True,
                health_check_interval_minutes=interval_minutes,
            )
        )
        await self.db.commit()
    
    async def disable_health_checks(self, tool_id: UUID) -> None:
        """Disable automatic health checks for a tool."""
        await self.db.execute(
            update(Tool)
            .where(Tool.id == tool_id)
            .values(health_check_enabled=False)
        )
        await self.db.commit()
