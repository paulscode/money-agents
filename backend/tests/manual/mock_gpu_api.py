#!/usr/bin/env python3
"""
Mock GPU API Server - Simulates a heavy GPU compute service.

This simulates an API like a local image generation or model inference service
that requires GPU resources and takes time to process.

Run on host: python mock_gpu_api.py
Access from container: http://host.docker.internal:9999

Endpoints:
- GET /health - Health check
- GET /gpu/status - GPU availability status
- POST /gpu/process - Submit a "GPU job" (simulates processing delay)
- GET /gpu/models - List available "models"
"""

import argparse
import json
import random
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Simulated GPU state
gpu_state = {
    "in_use": False,
    "current_job": None,
    "jobs_completed": 0,
    "total_compute_time": 0,
}
gpu_lock = threading.Lock()


class MockGPUHandler(BaseHTTPRequestHandler):
    """Handler for mock GPU API requests."""
    
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
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/health":
            self._send_json({
                "status": "healthy",
                "service": "mock-gpu-api",
                "version": "1.0.0",
                "timestamp": datetime.utcnow().isoformat(),
            })
        
        elif path == "/gpu/status":
            with gpu_lock:
                self._send_json({
                    "gpu_available": not gpu_state["in_use"],
                    "current_job": gpu_state["current_job"],
                    "jobs_completed": gpu_state["jobs_completed"],
                    "total_compute_time_seconds": gpu_state["total_compute_time"],
                    "gpu_info": {
                        "name": "Mock RTX 3090",
                        "memory_total_gb": 24,
                        "memory_used_gb": 8 if gpu_state["in_use"] else 2,
                        "utilization_percent": 95 if gpu_state["in_use"] else 5,
                    }
                })
        
        elif path == "/gpu/models":
            self._send_json({
                "models": [
                    {"id": "fast-diffusion", "name": "Fast Diffusion", "type": "image", "vram_gb": 4},
                    {"id": "quality-diffusion", "name": "Quality Diffusion XL", "type": "image", "vram_gb": 12},
                    {"id": "video-gen", "name": "Video Generator", "type": "video", "vram_gb": 20},
                    {"id": "audio-gen", "name": "Audio Generator", "type": "audio", "vram_gb": 6},
                ]
            })
        
        else:
            self._send_json({"error": "Not found", "path": path}, 404)
    
    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/gpu/process":
            self._handle_gpu_process()
        else:
            self._send_json({"error": "Not found", "path": path}, 404)
    
    def _handle_gpu_process(self):
        """Handle GPU processing request."""
        data = self._read_json()
        
        model = data.get("model", "fast-diffusion")
        prompt = data.get("prompt", "")
        
        if not prompt:
            self._send_json({"error": "Missing required field: prompt"}, 400)
            return
        
        # Check GPU availability
        with gpu_lock:
            if gpu_state["in_use"]:
                self._send_json({
                    "error": "GPU is busy",
                    "current_job": gpu_state["current_job"],
                    "retry_after_seconds": 5,
                }, 503)
                return
            
            # Mark GPU as in use
            job_id = f"job-{int(time.time())}-{random.randint(1000, 9999)}"
            gpu_state["in_use"] = True
            gpu_state["current_job"] = job_id
        
        # Simulate processing time based on model
        process_times = {
            "fast-diffusion": (1, 3),
            "quality-diffusion": (3, 6),
            "video-gen": (5, 10),
            "audio-gen": (2, 4),
        }
        min_time, max_time = process_times.get(model, (1, 3))
        process_time = random.uniform(min_time, max_time)
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Processing job {job_id} with model {model} ({process_time:.1f}s)...")
        time.sleep(process_time)
        
        # Release GPU and record stats
        with gpu_lock:
            gpu_state["in_use"] = False
            gpu_state["current_job"] = None
            gpu_state["jobs_completed"] += 1
            gpu_state["total_compute_time"] += process_time
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Job {job_id} completed")
        
        # Return fake results
        self._send_json({
            "success": True,
            "job_id": job_id,
            "model": model,
            "prompt": prompt,
            "processing_time_seconds": round(process_time, 2),
            "result": {
                "type": "image" if "diffusion" in model else model.split("-")[0],
                "output_url": f"file:///tmp/output_{job_id}.png",
                "dimensions": "1024x1024" if "diffusion" in model else None,
                "metadata": {
                    "seed": random.randint(1, 999999),
                    "steps": 30 if "quality" in model else 20,
                    "guidance_scale": 7.5,
                }
            }
        })
    
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
    parser = argparse.ArgumentParser(description="Mock GPU API Server")
    parser.add_argument("--port", type=int, default=9999, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()
    
    server = HTTPServer((args.host, args.port), MockGPUHandler)
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              Mock GPU API Server Started                      ║
╠══════════════════════════════════════════════════════════════╣
║  Local:     http://localhost:{args.port}                          ║
║  Container: http://host.docker.internal:{args.port}               ║
╠══════════════════════════════════════════════════════════════╣
║  Endpoints:                                                   ║
║    GET  /health        - Health check                         ║
║    GET  /gpu/status    - GPU availability                     ║
║    GET  /gpu/models    - Available models                     ║
║    POST /gpu/process   - Submit GPU job                       ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
