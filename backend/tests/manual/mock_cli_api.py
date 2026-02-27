#!/usr/bin/env python3
"""
Mock CLI Tool HTTP Wrapper - Exposes CLI tool as REST API

This wraps the mock_cli_tool.sh script as an HTTP API so that
containers can interact with host CLI tools via HTTP.

Real-world analogy: This is how you'd expose ffmpeg, imagemagick,
yt-dlp, or any other CLI tool to containerized agents.

Run on host: python mock_cli_api.py
Access from container: http://host.docker.internal:9998

Endpoints:
- GET  /health              - Health check
- GET  /info                - Tool info (runs --operation info)
- POST /process             - Process text (runs --operation process)
- POST /analyze             - Analyze text (runs --operation analyze)
- POST /convert             - Convert data (runs --operation convert)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Path to the CLI tool script
SCRIPT_DIR = Path(__file__).parent
CLI_TOOL_PATH = SCRIPT_DIR / "mock_cli_tool.sh"


class CLIWrapperHandler(BaseHTTPRequestHandler):
    """Handler for CLI wrapper API requests."""
    
    def _send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def _read_json(self) -> dict:
        """Read JSON from request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            body = self.rfile.read(content_length)
            return json.loads(body.decode())
        return {}
    
    def _run_cli(self, operation: str, **kwargs) -> dict:
        """Run the CLI tool and return results."""
        cmd = [str(CLI_TOOL_PATH), "--operation", operation, "--format", "json"]
        
        if "input" in kwargs and kwargs["input"]:
            cmd.extend(["--input", kwargs["input"]])
        if "output" in kwargs and kwargs["output"]:
            cmd.extend(["--output", kwargs["output"]])
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(SCRIPT_DIR),
            )
            
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {
                        "success": True,
                        "output": result.stdout,
                        "raw": True,
                    }
            else:
                return {
                    "success": False,
                    "error": result.stderr or f"Exit code: {result.returncode}",
                    "exit_code": result.returncode,
                }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out after 30 seconds",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._send_json({
                "status": "healthy",
                "service": "mock-cli-api",
                "version": "1.0.0",
                "cli_tool": str(CLI_TOOL_PATH),
                "cli_exists": CLI_TOOL_PATH.exists(),
                "timestamp": datetime.utcnow().isoformat(),
            })
        
        elif self.path == "/info":
            result = self._run_cli("info")
            self._send_json(result)
        
        else:
            self._send_json({"error": "Not found", "path": self.path}, 404)
    
    def do_POST(self):
        """Handle POST requests."""
        data = self._read_json()
        
        if self.path == "/process":
            input_text = data.get("input", data.get("text", ""))
            if not input_text:
                self._send_json({"error": "Missing required field: input"}, 400)
                return
            
            result = self._run_cli("process", input=input_text)
            self._send_json(result)
        
        elif self.path == "/analyze":
            input_text = data.get("input", data.get("text", ""))
            if not input_text:
                self._send_json({"error": "Missing required field: input"}, 400)
                return
            
            result = self._run_cli("analyze", input=input_text)
            self._send_json(result)
        
        elif self.path == "/convert":
            input_text = data.get("input", data.get("data", ""))
            if not input_text:
                self._send_json({"error": "Missing required field: input"}, 400)
                return
            
            result = self._run_cli("convert", input=input_text)
            self._send_json(result)
        
        else:
            self._send_json({"error": "Not found", "path": self.path}, 404)
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def log_message(self, format, *args):
        """Custom log format."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="Mock CLI Tool HTTP Wrapper")
    parser.add_argument("--port", type=int, default=9998, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()
    
    # Check CLI tool exists
    if not CLI_TOOL_PATH.exists():
        print(f"ERROR: CLI tool not found at {CLI_TOOL_PATH}")
        sys.exit(1)
    
    # Make sure it's executable
    os.chmod(CLI_TOOL_PATH, 0o755)
    
    server = HTTPServer((args.host, args.port), CLIWrapperHandler)
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Mock CLI Tool HTTP Wrapper Started                  ║
╠══════════════════════════════════════════════════════════════╣
║  Local:     http://localhost:{args.port}                          ║
║  Container: http://host.docker.internal:{args.port}               ║
╠══════════════════════════════════════════════════════════════╣
║  CLI Tool:  {str(CLI_TOOL_PATH)[:45]:<45} ║
╠══════════════════════════════════════════════════════════════╣
║  Endpoints:                                                   ║
║    GET  /health    - Health check                             ║
║    GET  /info      - Get system/tool info                     ║
║    POST /process   - Process input text                       ║
║    POST /analyze   - Analyze input text                       ║
║    POST /convert   - Convert data format                      ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
