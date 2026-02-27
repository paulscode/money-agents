"""
Job executor for resource agent.

Executes different types of jobs:
- REST API calls
- CLI commands
- Python scripts
"""
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

import httpx


logger = logging.getLogger(__name__)


class JobExecutor:
    """
    Executes jobs received from the broker.
    
    Supports multiple execution types:
    - rest_api: HTTP calls to APIs
    - cli: Command-line tool execution
    - python: Python script execution
    """
    
    def __init__(self, work_dir: Path):
        """
        Initialize executor.
        
        Args:
            work_dir: Directory for temp files and job working directory
        """
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._running_jobs: dict[str, asyncio.subprocess.Process] = {}
    
    async def execute(self, job_data: dict) -> dict:
        """
        Execute a job based on its type.
        
        Args:
            job_data: Job specification from broker containing:
                - job_id: Unique job identifier
                - tool_id: Tool being executed
                - tool_slug: Tool slug for identification
                - execution_type: "rest_api", "cli", or "python"
                - config: Type-specific configuration
                - parameters: Input parameters
                - timeout: Execution timeout in seconds
        
        Returns:
            Execution result dictionary
        """
        job_id = job_data.get("job_id")
        execution_type = job_data.get("execution_type", "rest_api")
        config = job_data.get("config", {})
        parameters = job_data.get("parameters", {})
        timeout = job_data.get("timeout", 300)
        
        logger.info(f"Executing job {job_id}, type: {execution_type}")
        
        try:
            if execution_type == "rest_api":
                result = await self._execute_rest_api(config, parameters, timeout)
            elif execution_type == "cli":
                result = await self._execute_cli(job_id, config, parameters, timeout)
            elif execution_type == "python":
                result = await self._execute_python(job_id, config, parameters, timeout)
            else:
                raise ValueError(f"Unknown execution type: {execution_type}")
            
            return {
                "success": True,
                "result": result,
                "execution_type": execution_type,
                "executed_at": datetime.utcnow().isoformat()
            }
            
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Job timed out after {timeout}s",
                "execution_type": execution_type
            }
        except Exception as e:
            logger.error(f"Job {job_id} execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "execution_type": execution_type
            }
    
    async def _execute_rest_api(
        self,
        config: dict,
        parameters: dict,
        timeout: int
    ) -> dict:
        """
        Execute a REST API call.
        
        Config:
            - base_url: Base URL for the API
            - endpoint: {"method": "POST", "path": "/api/endpoint"}
            - auth: {"type": "bearer|api_key|basic", ...}
            - request_mapping: Template for request body
            - response_mapping: Extract fields from response
        """
        base_url = config.get("base_url", "").rstrip("/")
        endpoint = config.get("endpoint", {})
        auth = config.get("auth", {})
        request_mapping = config.get("request_mapping", {})
        response_mapping = config.get("response_mapping", {})
        
        method = endpoint.get("method", "POST").upper()
        path = endpoint.get("path", "/")
        url = f"{base_url}{path}"
        
        # Build request body from mapping and parameters
        body = self._apply_mapping(request_mapping, parameters)
        
        # Build headers with auth
        headers = {"Content-Type": "application/json"}
        
        auth_type = auth.get("type", "none")
        if auth_type == "bearer":
            token = auth.get("token") or os.environ.get(auth.get("token_env", ""))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key":
            key = auth.get("key") or os.environ.get(auth.get("key_env", ""))
            header_name = auth.get("header", "X-API-Key")
            if key:
                headers[header_name] = key
        elif auth_type == "basic":
            import base64
            username = auth.get("username", "")
            password = auth.get("password") or os.environ.get(auth.get("password_env", ""))
            if username and password:
                creds = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {creds}"
        
        logger.debug(f"REST API call: {method} {url}")
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, headers=headers, params=body)
            elif method == "POST":
                response = await client.post(url, headers=headers, json=body)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=body)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            
            # Try to parse JSON response
            try:
                result = response.json()
            except json.JSONDecodeError:
                result = {"raw_response": response.text}
            
            # Apply response mapping if configured
            if response_mapping:
                result = self._extract_mapping(response_mapping, result)
            
            return result
    
    async def _execute_cli(
        self,
        job_id: str,
        config: dict,
        parameters: dict,
        timeout: int
    ) -> dict:
        """
        Execute a CLI command.
        
        Config:
            - command: Base command (e.g., "ffmpeg")
            - args_template: List of argument templates with {param} placeholders
            - working_dir: Optional working directory
            - env: Optional environment variables
        """
        command = config.get("command", "")
        args_template = config.get("args_template", [])
        working_dir = config.get("working_dir") or str(self.work_dir)
        env_vars = config.get("env", {})
        
        # Build command with arguments
        args = [command]
        for arg in args_template:
            if isinstance(arg, str):
                # Replace {param} placeholders
                for key, value in parameters.items():
                    arg = arg.replace(f"{{{key}}}", str(value))
                args.append(arg)
        
        # Set up environment
        env = os.environ.copy()
        env.update(env_vars)
        
        logger.debug(f"CLI execution: {' '.join(args)}")
        
        # Run subprocess
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=env
        )
        
        # Track running process for cancellation
        self._running_jobs[job_id] = process
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            
            return {
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "command": " ".join(args)
            }
        finally:
            self._running_jobs.pop(job_id, None)
    
    async def _execute_python(
        self,
        job_id: str,
        config: dict,
        parameters: dict,
        timeout: int
    ) -> dict:
        """
        Execute a Python script.
        
        Config:
            - script: Python code to execute
            - script_file: Path to Python script (alternative to inline script)
            - python_path: Python interpreter path (default: sys.executable)
            - requirements: List of pip packages to ensure installed
        """
        script = config.get("script")
        script_file = config.get("script_file")
        python_path = config.get("python_path") or "python"
        
        if not script and not script_file:
            raise ValueError("Either 'script' or 'script_file' must be provided")
        
        # Create temp script file if inline script provided
        if script:
            script_path = self.work_dir / f"job_{job_id}.py"
            with open(script_path, "w") as f:
                f.write(script)
        else:
            script_path = Path(script_file)
        
        # Pass parameters as JSON via stdin
        params_json = json.dumps(parameters)
        
        # Build command
        args = [python_path, str(script_path)]
        
        logger.debug(f"Python execution: {' '.join(args)}")
        
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.work_dir)
        )
        
        self._running_jobs[job_id] = process
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=params_json.encode()),
                timeout=timeout
            )
            
            # Try to parse stdout as JSON (for structured output)
            stdout_text = stdout.decode("utf-8", errors="replace")
            try:
                output = json.loads(stdout_text)
            except json.JSONDecodeError:
                output = stdout_text
            
            return {
                "exit_code": process.returncode,
                "output": output,
                "stderr": stderr.decode("utf-8", errors="replace")
            }
        finally:
            self._running_jobs.pop(job_id, None)
            # Clean up temp script
            if script and script_path.exists():
                script_path.unlink()
    
    def cancel_job(self, job_id: str):
        """Cancel a running job."""
        process = self._running_jobs.get(job_id)
        if process:
            logger.info(f"Cancelling job {job_id}")
            process.terminate()
    
    def _apply_mapping(self, mapping: dict, parameters: dict) -> dict:
        """
        Apply parameter mapping to create request body.
        
        Supports:
        - Direct values: {"model": "gpt-4"}
        - Parameter references: {"prompt": "{input_text}"}
        - Nested objects
        """
        result = {}
        
        for key, value in mapping.items():
            if isinstance(value, str):
                # Check for {param} references
                for param_key, param_value in parameters.items():
                    value = value.replace(f"{{{param_key}}}", str(param_value))
                result[key] = value
            elif isinstance(value, dict):
                result[key] = self._apply_mapping(value, parameters)
            else:
                result[key] = value
        
        return result
    
    def _extract_mapping(self, mapping: dict, response: dict) -> dict:
        """
        Extract fields from response based on mapping.
        
        Mapping example:
        {"output": "response.text", "tokens": "usage.total_tokens"}
        """
        result = {}
        
        for output_key, path in mapping.items():
            if isinstance(path, str):
                # Navigate dot-separated path
                value = response
                for part in path.split("."):
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        value = None
                        break
                result[output_key] = value
            else:
                result[output_key] = path
        
        return result
