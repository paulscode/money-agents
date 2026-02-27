"""
Dev Sandbox Service — Manages ephemeral Docker containers for agent code execution.

Gives agents a safe, isolated Linux environment where they can:
- Write and execute code (Python, Node.js, shell scripts, etc.)
- Install packages (pip install, npm install, apt-get install)
- Build and test applications
- Create file artifacts that persist back to the host

Architecture: Sibling containers via Docker socket mount.
The backend container talks to the host Docker daemon to create/manage
sandbox containers that run alongside (not inside) the backend.

Security: Each sandbox is isolated with resource limits, no host filesystem
access, optional network access, and auto-cleanup on expiry.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shlex
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import docker
from docker.errors import APIError, ContainerError, ImageNotFound, NotFound
from docker.models.containers import Container

from app.core.config import settings

logger = logging.getLogger(__name__)

# Container label used to identify sandbox containers
SANDBOX_LABEL = "money-agents.sandbox"
SANDBOX_LABEL_VALUE = "true"

# Network names
SANDBOX_NETWORK_ISOLATED = "sandbox-isolated"
SANDBOX_NETWORK_INTERNET = "sandbox-internet"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SandboxInfo:
    """Metadata about a sandbox container."""
    sandbox_id: str
    container_id: str
    image: str
    status: str  # "running", "stopped", "destroyed"
    created_at: datetime
    expires_at: datetime
    memory_limit: str
    cpu_count: float
    network_access: bool
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecResult:
    """Result of executing a command inside a sandbox."""
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


@dataclass
class FileInfo:
    """Metadata about a file inside a sandbox."""
    path: str
    name: str
    is_dir: bool
    size: int  # bytes, 0 for directories


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DevSandboxService:
    """Manages ephemeral Docker containers for agent code execution.

    Uses the Docker SDK to create sibling containers via the mounted
    Docker socket (/var/run/docker.sock).
    """

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy-init Docker client."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as e:
                logger.error("Failed to connect to Docker daemon: %s", e)
                raise RuntimeError(
                    "Cannot connect to Docker daemon. "
                    "Ensure /var/run/docker.sock is mounted and Docker is running."
                ) from e
        return self._client

    # ------------------------------------------------------------------
    # Network setup
    # ------------------------------------------------------------------

    def _ensure_networks(self) -> None:
        """Create sandbox Docker networks if they don't exist.

        Networks are normally pre-created by docker-compose (SGA3-M8).
        This method acts as a fallback in case the service is started
        outside of compose (e.g. during tests or standalone dev).
        """
        try:
            existing = {n.name for n in self.client.networks.list()}
        except Exception as exc:
            logger.warning(
                "SGA3-M8: Could not list Docker networks (%s); "
                "sandbox network creation skipped — relying on compose-managed networks.",
                exc,
            )
            return

        # Isolated network — no internet, no host access
        if SANDBOX_NETWORK_ISOLATED not in existing:
            try:
                self.client.networks.create(
                    SANDBOX_NETWORK_ISOLATED,
                    driver="bridge",
                    internal=True,  # No outbound internet
                    labels={SANDBOX_LABEL: SANDBOX_LABEL_VALUE},
                )
                logger.info("Created isolated sandbox network: %s", SANDBOX_NETWORK_ISOLATED)
            except Exception as exc:
                logger.warning("Could not create %s: %s", SANDBOX_NETWORK_ISOLATED, exc)

        # Internet-enabled network — outbound internet, no host services
        if SANDBOX_NETWORK_INTERNET not in existing:
            try:
                self.client.networks.create(
                    SANDBOX_NETWORK_INTERNET,
                    driver="bridge",
                    internal=False,  # Outbound internet allowed
                    labels={SANDBOX_LABEL: SANDBOX_LABEL_VALUE},
                )
                logger.info("Created internet sandbox network: %s", SANDBOX_NETWORK_INTERNET)
            except Exception as exc:
                logger.warning("Could not create %s: %s", SANDBOX_NETWORK_INTERNET, exc)

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    async def create_sandbox(
        self,
        image: str | None = None,
        memory_limit: str | None = None,
        cpu_count: float | None = None,
        network_access: bool | None = None,
        timeout_seconds: int | None = None,
        labels: dict[str, str] | None = None,
    ) -> SandboxInfo:
        """Create a new sandbox container.

        Returns SandboxInfo with the sandbox_id and metadata.
        """
        # Apply defaults from config, capping at maximums
        image = image or settings.dev_sandbox_default_image

        # Validate image against allowlist
        allowed = settings.dev_sandbox_allowed_images
        if allowed and image not in allowed:
            raise ValueError(
                f"Image '{image}' is not in the allowed sandbox images. "
                f"Allowed: {allowed}"
            )

        memory_limit = memory_limit or settings.dev_sandbox_default_memory
        timeout_seconds = min(
            timeout_seconds or settings.dev_sandbox_default_timeout,
            settings.dev_sandbox_max_timeout,
        )
        cpu_count = min(
            cpu_count or settings.dev_sandbox_default_cpus,
            settings.dev_sandbox_max_cpus,
        )
        if network_access is None:
            network_access = settings.dev_sandbox_network_access

        # Enforce max memory
        if not self._memory_within_limit(memory_limit, settings.dev_sandbox_max_memory):
            memory_limit = settings.dev_sandbox_max_memory

        # Check concurrent limit
        active = await self.list_sandboxes()
        if len(active) >= settings.dev_sandbox_max_concurrent:
            raise RuntimeError(
                f"Maximum concurrent sandboxes ({settings.dev_sandbox_max_concurrent}) reached. "
                "Destroy an existing sandbox first."
            )

        sandbox_id = str(uuid4())[:12]
        container_name = f"sandbox-{sandbox_id}"
        volume_name = f"sandbox-{sandbox_id}-vol"
        now = utc_now()
        expires_at = now + timedelta(seconds=timeout_seconds)

        # Ensure networks exist
        self._ensure_networks()

        # Select network
        network = SANDBOX_NETWORK_INTERNET if network_access else SANDBOX_NETWORK_ISOLATED

        # Build container labels
        container_labels = {
            SANDBOX_LABEL: SANDBOX_LABEL_VALUE,
            "money-agents.sandbox.id": sandbox_id,
            "money-agents.sandbox.created": now.isoformat(),
            "money-agents.sandbox.expires": expires_at.isoformat(),
            "money-agents.sandbox.image": image,
            "money-agents.sandbox.memory": memory_limit,
            "money-agents.sandbox.cpus": str(cpu_count),
            "money-agents.sandbox.network": str(network_access).lower(),
        }
        if labels:
            for k, v in labels.items():
                container_labels[f"money-agents.sandbox.user.{k}"] = str(v)

        def _create():
            # Pull image if not available locally
            try:
                self.client.images.get(image)
            except ImageNotFound:
                logger.info("Pulling image %s (first use)...", image)
                self.client.images.pull(image)

            # Create named volume
            self.client.volumes.create(name=volume_name, labels={
                SANDBOX_LABEL: SANDBOX_LABEL_VALUE,
                "money-agents.sandbox.id": sandbox_id,
            })

            # Create and start container
            container = self.client.containers.run(
                image=image,
                name=container_name,
                command="sleep infinity",  # Keep alive for exec
                detach=True,
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                mem_limit=memory_limit,
                nano_cpus=int(cpu_count * 1e9),
                network=network,
                labels=container_labels,
                security_opt=["no-new-privileges"],
                user="1000:1000",  # Run as unprivileged user
                read_only=True,  # Root FS read-only; /workspace volume + tmpfs provide writes
                tmpfs={
                    "/tmp": "size=64m",
                    "/var/tmp": "size=64m",
                    "/run": "size=16m",
                },
                pids_limit=256,  # Prevent fork bombs
                cap_drop=["ALL"],  # Drop all Linux capabilities
                # Block access to internal services — prevent sandbox containers
                # from reaching the backend API, database, or other Docker services
                # by pointing common service hostnames to an unreachable address.
                extra_hosts={
                    "backend": "127.0.0.254",
                    "postgres": "127.0.0.254",
                    "redis": "127.0.0.254",
                    "host.docker.internal": "127.0.0.254",
                },
                dns=["8.8.8.8", "8.8.4.4"],  # Public DNS only — no Docker DNS
            )
            return container

        container = await asyncio.to_thread(_create)

        info = SandboxInfo(
            sandbox_id=sandbox_id,
            container_id=container.id[:12],
            image=image,
            status="running",
            created_at=now,
            expires_at=expires_at,
            memory_limit=memory_limit,
            cpu_count=cpu_count,
            network_access=network_access,
            labels=labels or {},
        )

        logger.info(
            "Created sandbox %s (image=%s, memory=%s, cpus=%.1f, net=%s, expires=%s)",
            sandbox_id, image, memory_limit, cpu_count, network_access,
            expires_at.isoformat(),
        )
        return info

    async def exec_command(
        self,
        sandbox_id: str,
        command: str | list[str],
        workdir: str = "/workspace",
        timeout: int = 60,
        user: str | None = None,
    ) -> ExecResult:
        """Execute a command inside a sandbox container.

        Args:
            sandbox_id: The sandbox UUID prefix.
            command: Shell command string or argv list.
            workdir: Working directory inside the container.
            timeout: Max seconds for the command to run.
            user: Ignored — always runs as 1000:1000 (GAP: MEDIUM-2).

        Returns:
            ExecResult with stdout, stderr, exit_code, and timing.
        """
        container = self._get_container(sandbox_id)

        # SA2-27: Restrict workdir to /workspace or /tmp to prevent
        # accessing sensitive paths inside the sandbox 
        if not workdir.startswith("/workspace") and not workdir.startswith("/tmp"):
            raise ValueError(
                f"Workdir must start with /workspace or /tmp, got: {workdir}"
            )

        # Wrap string commands in sh -c
        if isinstance(command, str):
            exec_cmd = ["sh", "-c", command]
        else:
            exec_cmd = command

        start_ms = time.monotonic_ns() // 1_000_000

        def _exec():
            exec_kwargs = {
                "cmd": exec_cmd,
                "workdir": workdir,
                "demux": True,  # Separate stdout/stderr
                # Always run as non-root UID:GID to prevent privilege
                # escalation inside the sandbox.  GAP: MEDIUM-2
                "user": "1000:1000",
            }

            # Create exec instance
            exec_id = self.client.api.exec_create(
                container.id,
                **exec_kwargs,
            )
            # Start and get output
            output = self.client.api.exec_start(exec_id["Id"], demux=True)
            inspect = self.client.api.exec_inspect(exec_id["Id"])
            return output, inspect

        timed_out = False
        try:
            output, inspect = await asyncio.wait_for(
                asyncio.to_thread(_exec),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            output = (b"", b"Command timed out")
            inspect = {"ExitCode": -1}

        end_ms = time.monotonic_ns() // 1_000_000
        duration = end_ms - start_ms

        stdout_raw, stderr_raw = output if output else (b"", b"")
        stdout = (stdout_raw or b"").decode("utf-8", errors="replace")
        stderr = (stderr_raw or b"").decode("utf-8", errors="replace")

        # Truncate very long output to avoid memory issues
        max_output = 100_000  # 100KB
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + f"\n... (truncated, {len(stdout)} bytes total)"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + f"\n... (truncated, {len(stderr)} bytes total)"

        return ExecResult(
            exit_code=inspect.get("ExitCode", -1),
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration,
            timed_out=timed_out,
        )

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write a file into the sandbox filesystem.

        Creates parent directories automatically.

        Raises:
            ValueError: If the path targets a directory outside /workspace or /tmp.
        """
        # SA3-H7: Validate path targets allowed directories only (parity with write_files)
        resolved = os.path.normpath(path)
        if not resolved.startswith(("/workspace", "/tmp")):
            raise ValueError(
                f"write_file path must start with /workspace or /tmp, "
                f"got: {path!r} (resolved: {resolved!r})"
            )

        container = self._get_container(sandbox_id)

        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        # Ensure parent directory exists
        parent = str(Path(path).parent)
        if parent and parent != "/":
            await self.exec_command(sandbox_id, f"mkdir -p {shlex.quote(parent)}", timeout=10)

        # Create tar archive with the file
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            file_info = tarfile.TarInfo(name=Path(path).name)
            file_info.size = len(content_bytes)
            file_info.mtime = int(time.time())
            tar.addfile(file_info, io.BytesIO(content_bytes))
        tar_stream.seek(0)

        def _put():
            container.put_archive(parent, tar_stream)

        await asyncio.to_thread(_put)

    async def write_files(
        self,
        sandbox_id: str,
        files: dict[str, str | bytes],
    ) -> list[str]:
        """Write multiple files into the sandbox in one call.

        Args:
            sandbox_id: The sandbox to write to.
            files: Dict mapping file paths to their content.
                   e.g. {"/workspace/app.py": "print('hi')", "/workspace/data.csv": "a,b\n1,2"}

        Returns:
            List of paths written.

        Raises:
            ValueError: If any file path targets a directory outside /workspace or /tmp.
        """
        # GAP-11: Validate all paths target allowed directories only.
        # The container has read_only=True with writable tmpfs at /tmp, /var/tmp,
        # /run — we restrict writes to /workspace and /tmp to prevent abuse.
        _ALLOWED_PREFIXES = ("/workspace/", "/workspace", "/tmp/", "/tmp")
        for path in files:
            resolved = os.path.normpath(path)
            if not resolved.startswith(("/workspace", "/tmp")):
                raise ValueError(
                    f"write_files path must start with /workspace or /tmp, "
                    f"got: {path!r} (resolved: {resolved!r})"
                )

        container = self._get_container(sandbox_id)

        # Collect all unique parent directories
        parents = set()
        for path in files:
            parent = str(Path(path).parent)
            if parent and parent != "/":
                parents.add(parent)

        # Create all parent directories in one exec
        if parents:
            mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(p) for p in sorted(parents))
            await self.exec_command(sandbox_id, mkdir_cmd, timeout=10)

        # Build a single tar archive containing all files
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for path, content in files.items():
                if isinstance(content, str):
                    content_bytes = content.encode("utf-8")
                else:
                    content_bytes = content
                # Use full path (strip leading /) as tar member name
                member_name = path.lstrip("/")
                file_info = tarfile.TarInfo(name=member_name)
                file_info.size = len(content_bytes)
                file_info.mtime = int(time.time())
                tar.addfile(file_info, io.BytesIO(content_bytes))
        tar_stream.seek(0)

        def _put():
            container.put_archive("/", tar_stream)

        await asyncio.to_thread(_put)
        return list(files.keys())

    async def run_script(
        self,
        sandbox_id: str,
        script: str,
        interpreter: str = "sh",
        workdir: str = "/workspace",
        timeout: int = 120,
    ) -> ExecResult:
        """Write a script to a temp file and execute it in one operation.

        Eliminates the write-then-exec round-trip. The script is written to
        /tmp/_sandbox_script.sh (or .py etc.) and executed.

        Args:
            sandbox_id: The sandbox to run in.
            script: Multi-line script content.
            interpreter: Interpreter to use (sh, bash, python3, node, etc.).
            workdir: Working directory for execution.
            timeout: Command timeout in seconds.

        Returns:
            ExecResult with stdout, stderr, exit_code.
        """
        # Determine extension from interpreter
        ext_map = {"sh": ".sh", "bash": ".sh", "python3": ".py", "python": ".py", "node": ".js"}
        ext = ext_map.get(interpreter, "")
        script_path = f"/tmp/_sandbox_script{ext}"

        # Write the script file
        await self.write_file(sandbox_id, script_path, script)

        # Make it executable and run
        result = await self.exec_command(
            sandbox_id,
            f"chmod +x {script_path} && {interpreter} {script_path}",
            workdir=workdir,
            timeout=timeout,
        )
        return result

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str:
        """Read a file from the sandbox filesystem.

        SGA3-M3: Restricts reads to /workspace and /tmp to prevent reading
        sensitive container files (/etc/shadow, /proc, etc.).

        Returns file content as a string.
        """
        # SGA3-M3: Restrict file reads to safe directories
        import posixpath
        normalized = posixpath.normpath(path)
        if not (normalized.startswith("/workspace") or normalized.startswith("/tmp")):
            raise ValueError(
                f"File reads are restricted to /workspace and /tmp. "
                f"Requested path: {path}"
            )

        container = self._get_container(sandbox_id)

        def _get():
            bits, stat = container.get_archive(path)
            # bits is a generator of chunks forming a tar archive
            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)
            with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                member = tar.getmembers()[0]
                f = tar.extractfile(member)
                if f is None:
                    raise FileNotFoundError(f"Cannot read {path} (is it a directory?)")
                return f.read().decode("utf-8", errors="replace")

        return await asyncio.to_thread(_get)

    async def list_files(
        self,
        sandbox_id: str,
        path: str = "/workspace",
    ) -> list[FileInfo]:
        """List files in a sandbox directory."""
        result = await self.exec_command(
            sandbox_id,
            f"ls -la --time-style=+%s {path}",
            timeout=10,
        )

        files = []
        for line in result.stdout.strip().split("\n"):
            # Skip header line ("total N") and empty lines
            if not line or line.startswith("total "):
                continue
            parts = line.split(None, 7)
            if len(parts) < 7:
                continue

            perms = parts[0]
            size = int(parts[3]) if parts[3].isdigit() else 0
            name = parts[-1]

            # Skip . and ..
            if name in (".", ".."):
                continue

            is_dir = perms.startswith("d")
            full_path = f"{path.rstrip('/')}/{name}"

            files.append(FileInfo(
                path=full_path,
                name=name,
                is_dir=is_dir,
                size=size,
            ))

        return files

    async def extract_artifacts(
        self,
        sandbox_id: str,
        paths: list[str] | None = None,
    ) -> str:
        """Copy files from sandbox to host.

        Args:
            sandbox_id: The sandbox to extract from.
            paths: Specific paths to extract (default: entire /workspace).

        Returns:
            Host directory path where artifacts were saved.
        """
        container = self._get_container(sandbox_id)

        # Create host-side artifact directory
        artifact_dir = Path(settings.dev_sandbox_artifact_dir) / sandbox_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        extract_paths = paths or ["/workspace"]

        for src_path in extract_paths:
            def _get(p=src_path):
                bits, stat = container.get_archive(p)
                tar_stream = io.BytesIO()
                for chunk in bits:
                    tar_stream.write(chunk)
                tar_stream.seek(0)
                return tar_stream

            tar_stream = await asyncio.to_thread(_get)

            with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                # Security: prevent path traversal
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        continue
                    tar.extract(member, artifact_dir, filter="data")

        logger.info("Extracted artifacts from sandbox %s to %s", sandbox_id, artifact_dir)
        return str(artifact_dir)

    async def destroy_sandbox(
        self,
        sandbox_id: str,
        extract_first: bool = False,
    ) -> dict[str, Any]:
        """Stop and remove a sandbox container and its volume.

        Args:
            sandbox_id: The sandbox to destroy.
            extract_first: If True, extract /workspace artifacts before destroying.

        Returns:
            Dict with sandbox_id and artifact_dir (if extracted).
        """
        result = {"sandbox_id": sandbox_id, "artifact_dir": None}

        if extract_first:
            try:
                result["artifact_dir"] = await self.extract_artifacts(sandbox_id)
            except Exception as e:
                logger.warning("Failed to extract artifacts from %s: %s", sandbox_id, e)

        container_name = f"sandbox-{sandbox_id}"
        volume_name = f"sandbox-{sandbox_id}-vol"

        def _destroy():
            # Stop and remove container
            try:
                container = self.client.containers.get(container_name)
                container.remove(force=True)
            except NotFound:
                pass
            except Exception as e:
                logger.warning("Error removing container %s: %s", container_name, e)

            # Remove volume
            try:
                volume = self.client.volumes.get(volume_name)
                volume.remove(force=True)
            except NotFound:
                pass
            except Exception as e:
                logger.warning("Error removing volume %s: %s", volume_name, e)

        await asyncio.to_thread(_destroy)
        logger.info("Destroyed sandbox %s", sandbox_id)
        return result

    async def get_sandbox_info(self, sandbox_id: str) -> SandboxInfo | None:
        """Get status and metadata of a sandbox."""
        try:
            container = self._get_container(sandbox_id)
        except RuntimeError:
            return None

        labels = container.labels
        created_str = labels.get("money-agents.sandbox.created", "")
        expires_str = labels.get("money-agents.sandbox.expires", "")

        try:
            created_at = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            created_at = utc_now()
        try:
            expires_at = datetime.fromisoformat(expires_str)
        except (ValueError, TypeError):
            expires_at = utc_now()

        # Get user labels
        user_labels = {}
        for k, v in labels.items():
            if k.startswith("money-agents.sandbox.user."):
                user_labels[k.replace("money-agents.sandbox.user.", "")] = v

        return SandboxInfo(
            sandbox_id=sandbox_id,
            container_id=container.id[:12],
            image=labels.get("money-agents.sandbox.image", "unknown"),
            status=container.status,
            created_at=created_at,
            expires_at=expires_at,
            memory_limit=labels.get("money-agents.sandbox.memory", "512m"),
            cpu_count=float(labels.get("money-agents.sandbox.cpus", "1.0")),
            network_access=labels.get("money-agents.sandbox.network", "false") == "true",
            labels=user_labels,
        )

    async def list_sandboxes(self) -> list[SandboxInfo]:
        """List all active sandbox containers."""
        def _list():
            return self.client.containers.list(
                filters={"label": f"{SANDBOX_LABEL}={SANDBOX_LABEL_VALUE}"},
                all=True,
            )

        containers = await asyncio.to_thread(_list)
        sandboxes = []

        for c in containers:
            sid = c.labels.get("money-agents.sandbox.id", "")
            if not sid:
                continue
            info = SandboxInfo(
                sandbox_id=sid,
                container_id=c.id[:12],
                image=c.labels.get("money-agents.sandbox.image", "unknown"),
                status=c.status,
                created_at=datetime.fromisoformat(
                    c.labels.get("money-agents.sandbox.created", utc_now().isoformat())
                ),
                expires_at=datetime.fromisoformat(
                    c.labels.get("money-agents.sandbox.expires", utc_now().isoformat())
                ),
                memory_limit=c.labels.get("money-agents.sandbox.memory", "512m"),
                cpu_count=float(c.labels.get("money-agents.sandbox.cpus", "1.0")),
                network_access=c.labels.get("money-agents.sandbox.network", "false") == "true",
            )
            sandboxes.append(info)

        return sandboxes

    async def cleanup_expired(self) -> int:
        """Destroy sandboxes that have exceeded their timeout.

        Returns count of sandboxes cleaned up.
        """
        sandboxes = await self.list_sandboxes()
        now = utc_now()
        cleaned = 0

        for sb in sandboxes:
            if now > sb.expires_at:
                logger.info(
                    "Cleaning up expired sandbox %s (expired %s)",
                    sb.sandbox_id, sb.expires_at.isoformat(),
                )
                await self.destroy_sandbox(sb.sandbox_id)
                cleaned += 1

        if cleaned:
            logger.info("Cleaned up %d expired sandbox(es)", cleaned)
        return cleaned

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            def _ping():
                self.client.ping()
            await asyncio.to_thread(_ping)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_container(self, sandbox_id: str) -> Container:
        """Get a sandbox container by its sandbox ID.

        SA3-C2: Validates that the target container name matches the
        sandbox naming convention (``sandbox-<id>``) to prevent Docker
        socket proxy abuse — ensures exec_command can never target
        non-sandbox containers (postgres, redis, backend, etc.).
        """
        # Validate sandbox_id format: must be a 12-char hex UUID prefix
        import re
        if not sandbox_id or not re.fullmatch(r'[a-f0-9]{1,12}', sandbox_id):
            raise ValueError(f"Invalid sandbox ID format: {sandbox_id!r}")

        container_name = f"sandbox-{sandbox_id}"

        try:
            container = self.client.containers.get(container_name)
        except NotFound:
            raise RuntimeError(f"Sandbox {sandbox_id} not found. It may have been destroyed or expired.")
        except Exception as e:
            raise RuntimeError(f"Error accessing sandbox {sandbox_id}: {e}")

        # Double-check: verify the container has the sandbox label
        labels = container.labels or {}
        if labels.get(SANDBOX_LABEL) != SANDBOX_LABEL_VALUE:
            logger.warning(
                "SA3-C2: Container %s exists but lacks sandbox label — refusing access",
                container_name,
            )
            raise RuntimeError(f"Sandbox {sandbox_id} not found. It may have been destroyed or expired.")

        return container

    @staticmethod
    def _memory_within_limit(requested: str, maximum: str) -> bool:
        """Check if requested memory is within the maximum limit."""
        def _parse_mem(s: str) -> int:
            s = s.strip().lower()
            if s.endswith("g"):
                return int(float(s[:-1]) * 1024 * 1024 * 1024)
            elif s.endswith("m"):
                return int(float(s[:-1]) * 1024 * 1024)
            elif s.endswith("k"):
                return int(float(s[:-1]) * 1024)
            return int(s)

        try:
            return _parse_mem(requested) <= _parse_mem(maximum)
        except (ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional[DevSandboxService] = None


def get_dev_sandbox_service() -> DevSandboxService:
    """Get or create the global DevSandboxService instance."""
    global _service
    if _service is None:
        _service = DevSandboxService()
    return _service
