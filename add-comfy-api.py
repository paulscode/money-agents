#!/usr/bin/env python3
"""
ComfyUI API Generator Wizard

Interactive CLI tool that generates FastAPI-based REST APIs from ComfyUI workflow JSON exports.

Usage:
    python add-comfy-api.py [workflow.json]
"""

import json
import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Cross-platform utilities
from platform_utils import (
    IS_WINDOWS, get_venv_python, get_venv_pip, get_venv_activate_cmd,
)

# Rich for pretty output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.syntax import Syntax
    from rich import box
except ImportError:
    print("Error: 'rich' package is required. Install with: pip install rich")
    sys.exit(1)

# Questionary for arrow-key navigation
try:
    import questionary
    from questionary import Style
except ImportError:
    print("Error: 'questionary' package is required. Install with: pip install questionary")
    sys.exit(1)

# YAML for config files
try:
    import yaml
except ImportError:
    print("Error: 'pyyaml' package is required. Install with: pip install pyyaml")
    sys.exit(1)

console = Console()

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
COMFY_WORKFLOWS_DIR = PROJECT_ROOT / "comfy-workflows"

# Questionary style matching start.py
WIZARD_STYLE = Style([
    ('qmark', 'fg:cyan bold'),
    ('question', 'bold'),
    ('answer', 'fg:cyan'),
    ('pointer', 'fg:cyan bold'),
    ('highlighted', 'fg:cyan'),
    ('selected', 'fg:green'),
])


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class NodeInput:
    """Represents an input on a ComfyUI node."""
    key: str
    value: Any
    is_link: bool = False
    link_node_id: Optional[str] = None


@dataclass 
class WorkflowNode:
    """Represents a node in a ComfyUI workflow."""
    node_id: str
    class_type: str
    title: str
    inputs: List[NodeInput] = field(default_factory=list)
    
    def display_name(self, max_length: int = 80) -> str:
        """Format node for display in the wizard."""
        inputs_summary = self._format_inputs_summary()
        base = f'[{self.node_id}] "{self.title}" ({self.class_type})'
        
        if inputs_summary:
            full = f"{base} <{inputs_summary}>"
            if len(full) > max_length:
                return full[:max_length-3] + "...>"
            return full
        return base
    
    def _format_inputs_summary(self) -> str:
        """Format inputs as a summary string."""
        parts = []
        for inp in self.inputs:
            if inp.is_link:
                parts.append(f"{inp.key}: [{inp.link_node_id}]")
            else:
                val = inp.value
                if isinstance(val, str):
                    if len(val) > 20:
                        val = val[:17] + "..."
                    val = f'"{val}"'
                parts.append(f"{inp.key}: {val}")
        return ", ".join(parts)


@dataclass
class APIInput:
    """Represents an input parameter for the generated API."""
    name: str
    param_type: str  # str, int, float, bool
    required: bool
    default: Any
    node_id: str
    input_key: str
    description: str = ""


@dataclass
class ConditionalDisable:
    """Represents a condition for disabling a node."""
    node_id: str
    condition: str
    description: str = ""


@dataclass
class OutputConfig:
    """Configuration for output file handling."""
    comfy_dir: str
    pattern: str
    prefix: str
    extension: str
    digits: int = 5


@dataclass
class APIConfig:
    """Complete configuration for a generated API."""
    name: str
    display_name: str
    description: str
    port: int
    comfyui_url: str
    comfyui_path: str
    workflow_file: str
    inputs: List[APIInput] = field(default_factory=list)
    conditional_disables: List[ConditionalDisable] = field(default_factory=list)
    output: Optional[OutputConfig] = None
    endpoints: Dict[str, bool] = field(default_factory=lambda: {
        "generate": True,
        "output": True,
        "upload": False,
        "health": False,
        "info": False,
    })


# =============================================================================
# Workflow Parser
# =============================================================================

# Node categories for filtering
NODE_CATEGORIES = {
    "Input": [
        "PrimitiveString", "PrimitiveStringMultiline", "PrimitiveInt", "PrimitiveFloat",
        "INTConstant", "FloatConstant", "StringConstant",
        "LoadAudio", "LoadImage", "LoadVideo", "VHS_LoadVideo",
    ],
    "Sampler": [
        "KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced",
        "BasicScheduler", "KarrasScheduler", "RandomNoise",
    ],
    "Model": [
        "CheckpointLoader", "CheckpointLoaderSimple", "UNETLoader",
        "LoraLoader", "LoraLoaderModelOnly", "CLIPLoader", "VAELoader",
    ],
    "Output": [
        "SaveImage", "SaveAudio", "SaveVideo", "PreviewImage",
        "VHS_VideoCombine", "VHS_SaveVideo",
    ],
    "Conditioning": [
        "CLIPTextEncode", "CLIPTextEncodeSDXL", "ConditioningCombine",
        "ConditioningConcat", "ConditioningSetArea",
    ],
}


def categorize_node(class_type: str) -> str:
    """Determine the category of a node based on its class_type."""
    for category, prefixes in NODE_CATEGORIES.items():
        for prefix in prefixes:
            if class_type.startswith(prefix):
                return category
    return "Utility"


def parse_workflow(workflow_path: Path) -> Tuple[Dict[str, WorkflowNode], Optional[str]]:
    """
    Parse a ComfyUI workflow JSON file.
    
    Returns:
        (nodes_dict, error_message)
    """
    try:
        with open(workflow_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {}, f"Invalid JSON: {e}"
    except Exception as e:
        return {}, f"Failed to read file: {e}"
    
    if not isinstance(data, dict):
        return {}, "Workflow must be a JSON object with node IDs as keys"
    
    nodes: Dict[str, WorkflowNode] = {}
    
    for node_id, node_data in data.items():
        if not isinstance(node_data, dict):
            continue
        
        class_type = node_data.get("class_type", "Unknown")
        meta = node_data.get("_meta", {})
        title = meta.get("title", class_type)
        
        inputs_data = node_data.get("inputs", {})
        inputs: List[NodeInput] = []
        
        for key, value in inputs_data.items():
            if isinstance(value, list) and len(value) == 2:
                # This is a link to another node: [node_id, output_index]
                inputs.append(NodeInput(
                    key=key,
                    value=value,
                    is_link=True,
                    link_node_id=str(value[0])
                ))
            else:
                inputs.append(NodeInput(
                    key=key,
                    value=value,
                    is_link=False
                ))
        
        nodes[node_id] = WorkflowNode(
            node_id=node_id,
            class_type=class_type,
            title=title,
            inputs=inputs
        )
    
    return nodes, None


# =============================================================================
# Port Allocation
# =============================================================================

def get_used_ports() -> List[int]:
    """Get list of ports already used by existing APIs."""
    used = []
    
    if not COMFY_WORKFLOWS_DIR.exists():
        return used
    
    for api_dir in COMFY_WORKFLOWS_DIR.iterdir():
        if not api_dir.is_dir():
            continue
        
        config_file = api_dir / "config.yaml"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = yaml.safe_load(f)
                port = config.get("api", {}).get("port")
                if port:
                    used.append(int(port))
            except Exception:
                pass
        
        # Also check app.py for legacy APIs without config.yaml
        app_file = api_dir / "app.py"
        if app_file.exists() and not config_file.exists():
            try:
                content = app_file.read_text()
                # Look for port= in app.run() or uvicorn.run()
                match = re.search(r'port[=:]?\s*(\d+)', content)
                if match:
                    used.append(int(match.group(1)))
            except Exception:
                pass
    
    return used


def allocate_port() -> int:
    """Allocate the next available port starting from 9901."""
    used = get_used_ports()
    
    for port in range(9901, 10000):
        if port not in used:
            return port
    
    # Fallback
    return 9901


# =============================================================================
# Condition Expression Parser
# =============================================================================

class ConditionEvaluator:
    """
    Evaluates conditional expressions against API input values.
    
    Supported expressions:
        EMPTY(field)           - True if field is empty/missing
        NOT(expr)              - Negates expression
        AND(expr, expr, ...)   - All must be true
        OR(expr, expr, ...)    - Any must be true
        field == "value"       - Equality check
        field != "value"       - Inequality check
        field > N              - Greater than (numeric)
        field >= N             - Greater or equal
        field < N              - Less than
        field <= N             - Less or equal
    """
    
    def __init__(self, inputs: Dict[str, Any]):
        self.inputs = inputs
    
    def evaluate(self, expression: str) -> bool:
        """Parse and evaluate the condition expression."""
        expression = expression.strip()
        
        # Handle function calls
        if expression.startswith('EMPTY(') and expression.endswith(')'):
            field = expression[6:-1].strip()
            return self._is_empty(field)
        
        if expression.startswith('NOT(') and expression.endswith(')'):
            inner = expression[4:-1].strip()
            return not self.evaluate(inner)
        
        if expression.startswith('AND(') and expression.endswith(')'):
            inner = expression[4:-1].strip()
            parts = self._split_expressions(inner)
            return all(self.evaluate(p) for p in parts)
        
        if expression.startswith('OR(') and expression.endswith(')'):
            inner = expression[3:-1].strip()
            parts = self._split_expressions(inner)
            return any(self.evaluate(p) for p in parts)
        
        # Handle comparisons
        for op in ['==', '!=', '>=', '<=', '>', '<']:
            if op in expression:
                return self._evaluate_comparison(expression, op)
        
        raise ValueError(f"Invalid expression: {expression}")
    
    def _is_empty(self, field: str) -> bool:
        """Check if a field is empty, null, or not provided."""
        value = self.inputs.get(field)
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == '':
            return True
        return False
    
    def _split_expressions(self, expr: str) -> List[str]:
        """Split comma-separated expressions, respecting nested parentheses."""
        parts = []
        current = ""
        depth = 0
        
        for char in expr:
            if char == '(':
                depth += 1
                current += char
            elif char == ')':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        
        if current.strip():
            parts.append(current.strip())
        
        return parts
    
    def _evaluate_comparison(self, expr: str, op: str) -> bool:
        """Evaluate a comparison expression."""
        parts = expr.split(op, 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid comparison: {expr}")
        
        field = parts[0].strip()
        value_str = parts[1].strip().strip('"\'')
        
        field_value = self.inputs.get(field)
        
        # Try numeric comparison first
        try:
            target = float(value_str)
            field_val = float(field_value) if field_value is not None else 0
            
            if op == '==': return field_val == target
            if op == '!=': return field_val != target
            if op == '>':  return field_val > target
            if op == '>=': return field_val >= target
            if op == '<':  return field_val < target
            if op == '<=': return field_val <= target
        except (ValueError, TypeError):
            pass
        
        # String comparison
        str_field = str(field_value) if field_value is not None else ""
        if op == '==': return str_field == value_str
        if op == '!=': return str_field != value_str
        
        raise ValueError(f"Cannot compare strings with {op}")


def validate_condition(condition: str, available_fields: List[str]) -> Optional[str]:
    """
    Validate a condition expression syntax.
    
    Returns:
        None if valid, error message if invalid
    """
    try:
        # Test with dummy values
        test_inputs = {field: "test" for field in available_fields}
        evaluator = ConditionEvaluator(test_inputs)
        evaluator.evaluate(condition)
        return None
    except Exception as e:
        return str(e)


# =============================================================================
# Code Generator
# =============================================================================

def generate_requirements_txt() -> str:
    """Generate requirements.txt content."""
    return """fastapi>=0.109.0
uvicorn[standard]>=0.27.0
httpx>=0.26.0
pyyaml>=6.0
python-multipart>=0.0.6
aiofiles>=23.2.1
"""


def generate_pydantic_models(config: APIConfig) -> str:
    """Generate Pydantic model definitions from config."""
    lines = []
    
    # Generate request model
    lines.append("class GenerateRequest(BaseModel):")
    lines.append('    """Request model for /generate endpoint."""')
    
    if not config.inputs:
        lines.append("    pass")
    else:
        for inp in config.inputs:
            type_map = {
                "str": "str",
                "int": "int", 
                "float": "float",
                "bool": "bool",
            }
            py_type = type_map.get(inp.param_type, "str")
            
            if inp.required:
                lines.append(f"    {inp.name}: {py_type}")
            elif inp.default == "random":
                lines.append(f"    {inp.name}: Optional[{py_type}] = None")
            elif inp.default is None:
                lines.append(f"    {inp.name}: Optional[{py_type}] = None")
            else:
                default_val = repr(inp.default) if isinstance(inp.default, str) else inp.default
                lines.append(f"    {inp.name}: {py_type} = {default_val}")
    
    lines.append("")
    lines.append("")
    lines.append("class GenerateResponse(BaseModel):")
    lines.append('    """Response model for /generate endpoint."""')
    
    ext = config.output.extension if config.output else "png"
    if ext in ["mp3", "wav", "flac", "ogg"]:
        lines.append("    audio_url: str")
    elif ext in ["mp4", "webm", "mov", "avi"]:
        lines.append("    video_url: str")
    else:
        lines.append("    image_url: str")
    
    return "\n".join(lines)


def generate_input_application(config: APIConfig) -> str:
    """Generate code to apply inputs to workflow."""
    lines = []
    
    for inp in config.inputs:
        if inp.default == "random" and inp.param_type == "int":
            lines.append(f'    # {inp.name}: random if not provided')
            lines.append(f'    {inp.name}_val = request.{inp.name}')
            lines.append(f'    if {inp.name}_val is None:')
            lines.append(f'        {inp.name}_val = secrets.randbelow(2**31)')
            lines.append(f'    workflow["{inp.node_id}"]["inputs"]["{inp.input_key}"] = {inp.name}_val')
        else:
            lines.append(f'    workflow["{inp.node_id}"]["inputs"]["{inp.input_key}"] = request.{inp.name}')
    
    return "\n".join(lines) if lines else "    pass"


def generate_conditional_disables(config: APIConfig) -> str:
    """Generate code for conditional node removal."""
    if not config.conditional_disables:
        return "    pass"
    
    lines = []
    lines.append("    # Build inputs dict for condition evaluation")
    lines.append("    inputs_dict = request.model_dump()")
    lines.append("")
    lines.append("    evaluator = ConditionEvaluator(inputs_dict)")
    lines.append("")
    
    for cd in config.conditional_disables:
        lines.append(f'    # {cd.description or f"Conditionally disable node {cd.node_id}"}')
        lines.append(f'    if evaluator.evaluate({repr(cd.condition)}):')
        lines.append(f'        workflow.pop("{cd.node_id}", None)')
        lines.append("")
    
    return "\n".join(lines)


def generate_app_py(config: APIConfig) -> str:
    """Generate the complete app.py file."""
    
    # Determine response field name and output strategy
    ext = config.output.extension if config.output else "png"
    if ext in ["mp3", "wav", "flac", "ogg"]:
        url_field = "audio_url"
        output_strategy = "filesystem"  # Audio uses filesystem polling
    elif ext in ["mp4", "webm", "mov", "avi"]:
        url_field = "video_url"
        output_strategy = "filesystem"  # Video uses filesystem polling
    else:
        url_field = "image_url"
        output_strategy = "view"  # Images use ComfyUI /view endpoint (more robust)
    
    pydantic_models = generate_pydantic_models(config)
    input_application = generate_input_application(config)
    conditional_disables = generate_conditional_disables(config)
    
    # Build output config
    output = config.output
    comfy_output_dir = f'Path("{output.comfy_dir}").expanduser()' if output else 'Path.home() / "ComfyUI" / "output"'
    output_pattern = output.pattern if output else "ComfyUI_*.png"
    output_prefix = output.prefix if output else "OUTPUT"
    output_ext = output.extension if output else "png"
    output_digits = output.digits if output else 5
    
    # Generate output handling code based on strategy
    if output_strategy == "view":
        # Use ComfyUI /view endpoint for images (more robust, avoids filesystem races)
        output_handling_code = '''
        # Find SaveImage nodes and poll history for image info
        save_node_ids = _find_saveimage_node_ids(workflow)
        
        # Poll history until image is available
        history_entry, image_info, poll_err = await _poll_history_until_image(
            client, prompt_id, save_node_ids
        )
        
        if poll_err == "comfyui_execution_error":
            raise HTTPException(
                status_code=502,
                detail="ComfyUI reported an execution error"
            )
        if poll_err == "timeout_waiting_for_outputs":
            raise HTTPException(
                status_code=504,
                detail="Timed out waiting for ComfyUI to generate output"
            )
        if not image_info:
            raise HTTPException(
                status_code=502,
                detail="No output found in ComfyUI history"
            )
        
        # Download via /view endpoint and save locally
        try:
            dst_name = _next_unique_name()
            dst_path = LOCAL_OUTPUT_DIR / dst_name
            await _download_image_from_comfy(client, image_info, dst_path)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download output: {e}"
            )'''
    else:
        # Use filesystem polling for video/audio (required for non-standard outputs)
        output_handling_code = '''
    # Poll filesystem for output file
    output_path = await _poll_for_output(start_ts)
    
    if not output_path:
        raise HTTPException(
            status_code=504,
            detail="Timed out waiting for output file"
        )
    
    # Move and rename
    try:
        dst_name = _next_unique_name()
        dst_path = LOCAL_OUTPUT_DIR / dst_name
        shutil.move(str(output_path), str(dst_path))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to move output file: {e}"
        )'''
    
    # Determine if we need upload endpoint
    has_upload = config.endpoints.get("upload", False)
    comfy_input_dir = f'Path("{config.comfyui_path}").expanduser() / "input"'
    
    template = f'''#!/usr/bin/env python3
"""
{config.display_name}

{config.description}

Generated by add-comfy-api.py
"""

import asyncio
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

import aiofiles
import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


def secure_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other issues."""
    # Remove path separators
    filename = filename.replace("/", "_").replace("\\\\", "_")
    # Keep only safe characters
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    # Remove leading/trailing dots and underscores
    filename = filename.strip("._")
    return filename or "unnamed"


# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.yaml"

# Load config
with open(CONFIG_FILE) as f:
    CONFIG = yaml.safe_load(f)

API_NAME = CONFIG["api"]["name"]
API_PORT = CONFIG["api"]["port"]
COMFYUI_URL = CONFIG["comfyui"]["url"]
BASE_URL = f"http://127.0.0.1:{{API_PORT}}"

# Directories
LOCAL_OUTPUT_DIR = SCRIPT_DIR / "output"
LOCAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
{"LOCAL_UPLOAD_DIR = SCRIPT_DIR / 'upload'" if has_upload else ""}
{"LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)" if has_upload else ""}

COMFY_OUTPUT_DIR = {comfy_output_dir}
{"COMFY_INPUT_DIR = " + comfy_input_dir if has_upload else ""}

# Output naming
NAME_PREFIX = "{output_prefix}"
NAME_DIGITS = {output_digits}
NAME_EXT = "{output_ext}"
OUTPUT_PATTERN = "{output_pattern}"

# Timeouts
POLL_INTERVAL_S = CONFIG.get("timeouts", {{}}).get("poll_interval", 0.5)
TOTAL_TIMEOUT_S = CONFIG.get("timeouts", {{}}).get("total_timeout", 240)
REQUEST_TIMEOUT_S = CONFIG.get("timeouts", {{}}).get("request_timeout", [5, 60])

# Load workflow template
with open(SCRIPT_DIR / "workflow.json") as f:
    WORKFLOW_TEMPLATE = json.load(f)


# =============================================================================
# Condition Evaluator
# =============================================================================

class ConditionEvaluator:
    """Evaluates conditional expressions against API input values."""
    
    def __init__(self, inputs: Dict[str, Any]):
        self.inputs = inputs
    
    def evaluate(self, expression: str) -> bool:
        expression = expression.strip()
        
        if expression.startswith('EMPTY(') and expression.endswith(')'):
            field = expression[6:-1].strip()
            return self._is_empty(field)
        
        if expression.startswith('NOT(') and expression.endswith(')'):
            inner = expression[4:-1].strip()
            return not self.evaluate(inner)
        
        if expression.startswith('AND(') and expression.endswith(')'):
            inner = expression[4:-1].strip()
            parts = self._split_expressions(inner)
            return all(self.evaluate(p) for p in parts)
        
        if expression.startswith('OR(') and expression.endswith(')'):
            inner = expression[3:-1].strip()
            parts = self._split_expressions(inner)
            return any(self.evaluate(p) for p in parts)
        
        for op in ['==', '!=', '>=', '<=', '>', '<']:
            if op in expression:
                return self._evaluate_comparison(expression, op)
        
        raise ValueError(f"Invalid expression: {{expression}}")
    
    def _is_empty(self, field: str) -> bool:
        value = self.inputs.get(field)
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == '':
            return True
        return False
    
    def _split_expressions(self, expr: str) -> List[str]:
        parts = []
        current = ""
        depth = 0
        for char in expr:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            if char == ',' and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            parts.append(current.strip())
        return parts
    
    def _evaluate_comparison(self, expr: str, op: str) -> bool:
        parts = expr.split(op, 1)
        field = parts[0].strip()
        value_str = parts[1].strip().strip('"\\\'')
        field_value = self.inputs.get(field)
        
        try:
            target = float(value_str)
            field_val = float(field_value) if field_value is not None else 0
            if op == '==': return field_val == target
            if op == '!=': return field_val != target
            if op == '>':  return field_val > target
            if op == '>=': return field_val >= target
            if op == '<':  return field_val < target
            if op == '<=': return field_val <= target
        except (ValueError, TypeError):
            pass
        
        str_field = str(field_value) if field_value is not None else ""
        if op == '==': return str_field == value_str
        if op == '!=': return str_field != value_str
        return False


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="{config.display_name}",
    description="{config.description}",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://backend:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# =============================================================================
# Pydantic Models
# =============================================================================

{pydantic_models}


# =============================================================================
# Helper Functions
# =============================================================================

def _next_unique_name() -> str:
    """Generate next unique output filename."""
    max_n = 0
    pattern = f"{{NAME_PREFIX}}_{{\'[0-9]\' * NAME_DIGITS}}.{{NAME_EXT}}"
    
    for p in LOCAL_OUTPUT_DIR.glob(pattern):
        stem = p.stem
        if not stem.startswith(NAME_PREFIX + "_"):
            continue
        num_part = stem[len(NAME_PREFIX) + 1:]
        if num_part.isdigit():
            max_n = max(max_n, int(num_part))
    
    n = max_n + 1
    return f"{{NAME_PREFIX}}_{{n:0{{NAME_DIGITS}}d}}.{{NAME_EXT}}"


def _find_saveimage_node_ids(workflow_obj: dict) -> list:
    """Find all SaveImage node IDs in workflow."""
    save_ids = []
    for node_id, node in (workflow_obj or {{}}).items():
        if isinstance(node, dict) and node.get("class_type") == "SaveImage":
            save_ids.append(str(node_id))
    return save_ids


def _extract_status(history_entry: dict) -> dict:
    """Extract status from history entry."""
    status = history_entry.get("status")
    if isinstance(status, dict):
        return status
    return {{}}


def _status_is_error(status: dict) -> bool:
    """Check if status indicates an error."""
    return str(status.get("status_str", "")).lower() == "error"


def _pick_best_image_info(history_entry: dict, save_node_ids: list) -> tuple:
    """
    Prefer images from SaveImage nodes; within a node prefer type == 'output'.
    Fallback to any images in outputs if SaveImage nodes aren't found.
    Returns (image_info, error_code)
    """
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        return None, "outputs_missing_or_empty"

    def pick_from_node(node_out):
        if not isinstance(node_out, dict):
            return None
        images = node_out.get("images")
        if not isinstance(images, list) or not images:
            return None
        for info in images:
            if isinstance(info, dict) and info.get("type") == "output":
                return info
        for info in images:
            if isinstance(info, dict):
                return info
        return None

    for sid in save_node_ids:
        info = pick_from_node(outputs.get(sid))
        if info:
            return info, None

    for _, node_out in outputs.items():
        info = pick_from_node(node_out)
        if info:
            return info, None

    return None, "no_images_found_in_outputs"


async def _poll_history_until_image(
    client: httpx.AsyncClient, prompt_id: str, save_node_ids: list
) -> tuple:
    """
    Poll /history/{{prompt_id}} until we can extract an image_info,
    or we see an error status, or timeout.
    Returns (history_entry, image_info, error_code)
    """
    deadline = time.time() + TOTAL_TIMEOUT_S

    while time.time() < deadline:
        try:
            r = await client.get(f"{{COMFYUI_URL}}/history/{{prompt_id}}")
            r.raise_for_status()
            data = r.json()

            if not (isinstance(data, dict) and prompt_id in data and isinstance(data[prompt_id], dict)):
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            entry = data[prompt_id]
            status = _extract_status(entry)

            if status and _status_is_error(status):
                return entry, None, "comfyui_execution_error"

            image_info, _ = _pick_best_image_info(entry, save_node_ids)
            if image_info:
                return entry, image_info, None

        except Exception:
            pass

        await asyncio.sleep(POLL_INTERVAL_S)

    return None, None, "timeout_waiting_for_outputs"


async def _download_image_from_comfy(
    client: httpx.AsyncClient, image_info: dict, dst_path: Path
):
    """Download via ComfyUI /view to avoid filesystem races."""
    filename = image_info.get("filename")
    if not filename:
        raise ValueError("image_info missing filename")

    params = {{"filename": filename}}
    if image_info.get("subfolder"):
        params["subfolder"] = image_info["subfolder"]
    if image_info.get("type"):
        params["type"] = image_info["type"]
    else:
        params["type"] = "output"

    r = await client.get(f"{{COMFYUI_URL}}/view", params=params)
    r.raise_for_status()

    tmp_path = dst_path.with_suffix(dst_path.suffix + ".part")
    with open(tmp_path, "wb") as f:
        f.write(r.content)
    os.replace(tmp_path, dst_path)


async def _wait_for_file_stable(path: Path, checks: int = 3, interval: float = 0.35) -> bool:
    """Wait until file size stops changing."""
    last_size = -1
    stable = 0
    
    for _ in range(checks * 4):
        if not path.exists():
            await asyncio.sleep(interval)
            continue
        try:
            size = path.stat().st_size
        except Exception:
            await asyncio.sleep(interval)
            continue
        
        if size > 0 and size == last_size:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
            last_size = size
        
        await asyncio.sleep(interval)
    
    return path.exists()


async def _poll_for_output(start_ts: float) -> Optional[Path]:
    """Poll for output file to appear (filesystem strategy)."""
    deadline = time.time() + TOTAL_TIMEOUT_S
    
    while time.time() < deadline:
        candidates = []
        try:
            for p in COMFY_OUTPUT_DIR.glob(OUTPUT_PATTERN):
                try:
                    if p.stat().st_mtime >= start_ts:
                        candidates.append((p.stat().st_mtime, p))
                except FileNotFoundError:
                    continue
        except Exception:
            pass
        
        if candidates:
            candidates.sort(reverse=True)
            path = candidates[0][1]
            if await _wait_for_file_stable(path):
                return path
        
        await asyncio.sleep(POLL_INTERVAL_S)
    
    return None


def _apply_inputs(workflow: dict, request: GenerateRequest):
    """Apply request inputs to workflow."""
{input_application}


def _apply_conditional_disables(workflow: dict, request: GenerateRequest):
    """Remove nodes based on conditions."""
{conditional_disables}


# =============================================================================
# Endpoints
# =============================================================================

@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate content using ComfyUI workflow."""
    
    # Deep copy workflow template
    workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
    
    # Apply inputs
    _apply_inputs(workflow, request)
    
    # Apply conditional disables
    _apply_conditional_disables(workflow, request)
    
    # Submit to ComfyUI
    start_ts = time.time()
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{{COMFYUI_URL}}/prompt",
                json={{"prompt": workflow}},
                timeout=REQUEST_TIMEOUT_S[1]
            )
            resp.raise_for_status()
            body = resp.json()
            prompt_id = body.get("prompt_id")
            
            if not prompt_id:
                raise HTTPException(
                    status_code=502,
                    detail="ComfyUI did not return prompt_id"
                )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=502,
                detail=f"Cannot connect to ComfyUI at {{COMFYUI_URL}}"
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="ComfyUI request timed out"
            )
{output_handling_code}

    return GenerateResponse({url_field}=f"{{BASE_URL}}/output/{{dst_name}}")


@app.get("/output/{{filename}}")
async def get_output(filename: str):
    """Retrieve a generated file."""
    safe = secure_filename(filename)
    if not safe or safe != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    path = LOCAL_OUTPUT_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(path)

'''
    
    # Add upload endpoint if enabled
    if has_upload:
        template += '''

class UploadResponse(BaseModel):
    """Response model for /upload endpoint."""
    original_filename: str
    filename: str
    path: str


def _unique_dest_path(directory: Path, filename: str) -> Path:
    """Generate unique path if file exists."""
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = directory / filename
    i = 1
    while candidate.exists():
        candidate = directory / f"{base}_{i}{ext}"
        i += 1
    return candidate


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)):
    """Upload a file to ComfyUI input directory."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    original = secure_filename(file.filename)
    if not original:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    final_path = _unique_dest_path(COMFY_INPUT_DIR, original)
    tmp_path = final_path.with_suffix(final_path.suffix + ".part")
    
    try:
        content = await file.read()
        async with aiofiles.open(tmp_path, "wb") as f:
            await f.write(content)
        os.replace(tmp_path, final_path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    
    return UploadResponse(
        original_filename=original,
        filename=final_path.name,
        path=f"input/{final_path.name}"
    )
'''
    
    # Add health endpoint if enabled
    if config.endpoints.get("health", False):
        template += '''

@app.get("/health")
async def health():
    """Health check endpoint."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{COMFYUI_URL}/system_stats", timeout=5.0)
            comfy_ok = resp.status_code == 200
        except Exception:
            comfy_ok = False
    
    return {
        "status": "healthy" if comfy_ok else "degraded",
        "comfyui": "connected" if comfy_ok else "disconnected",
        "api": API_NAME
    }
'''
    
    # Add info endpoint if enabled
    if config.endpoints.get("info", False):
        template += f'''

@app.get("/info")
async def info():
    """API information endpoint."""
    return {{
        "name": API_NAME,
        "display_name": "{config.display_name}",
        "description": "{config.description}",
        "version": "1.0.0",
        "comfyui_url": COMFYUI_URL,
        "endpoints": {{
            "generate": "/generate",
            "output": "/output/{{filename}}",
            {"'upload': '/upload'," if has_upload else ""}
            {"'health': '/health'," if config.endpoints.get('health') else ""}
            "'docs': '/docs'"
        }}
    }}
'''
    
    # Add main block
    template += f'''

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=API_PORT)
'''
    
    return template


def generate_config_yaml(config: APIConfig) -> str:
    """Generate config.yaml content."""
    
    config_dict = {
        "api": {
            "name": config.name,
            "display_name": config.display_name,
            "description": config.description,
            "version": "1.0.0",
            "port": config.port,
            "enabled": True,
        },
        "comfyui": {
            "url": config.comfyui_url,
            "path": config.comfyui_path,
        },
        # GPU affinity — comma-separated GPU indices this workflow should use
        # e.g. "0" for first GPU, "0,1" for both, "1" for second only
        "gpu_indices": "0",
        "workflow": {
            "file": "workflow.json",
        },
        "inputs": [],
        "conditional_disables": [],
        "output": {},
        "endpoints": config.endpoints,
        "timeouts": {
            "poll_interval": 0.5,
            "total_timeout": 240,
            "request_timeout": [5, 60],
        },
    }
    
    # Add inputs
    for inp in config.inputs:
        config_dict["inputs"].append({
            "name": inp.name,
            "type": inp.param_type,
            "required": inp.required,
            "default": inp.default,
            "node_id": inp.node_id,
            "input_key": inp.input_key,
            "description": inp.description,
        })
    
    # Add conditional disables
    for cd in config.conditional_disables:
        config_dict["conditional_disables"].append({
            "node_id": cd.node_id,
            "condition": cd.condition,
            "description": cd.description,
        })
    
    # Add output config
    if config.output:
        config_dict["output"] = {
            "comfy_dir": config.output.comfy_dir,
            "pattern": config.output.pattern,
            "prefix": config.output.prefix,
            "extension": config.output.extension,
            "digits": config.output.digits,
        }
    
    return yaml.dump(config_dict, default_flow_style=False, sort_keys=False)


# =============================================================================
# Interactive Wizard
# =============================================================================

def print_header():
    """Print the wizard header."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]ComfyUI API Generator Wizard[/bold cyan]\n\n"
        "This wizard will help you create a REST API from your ComfyUI workflow.\n\n"
        "[dim]Before starting, ensure you have:[/dim]\n"
        "• Exported your workflow as API JSON from ComfyUI\n"
        "• ComfyUI running and accessible",
        title="🔧 add-comfy-api",
        border_style="cyan"
    ))
    console.print()


def step_comfyui_config() -> Tuple[str, str]:
    """Step 1: Configure ComfyUI connection."""
    console.print("[bold]Step 1 of 6: ComfyUI Configuration[/bold]")
    console.print("─" * 40)
    console.print()
    
    comfyui_url = Prompt.ask(
        "ComfyUI URL",
        default="http://localhost:8188"
    )
    
    default_path = str(Path.home() / "workspace" / "ComfyUI")
    comfyui_path = Prompt.ask(
        "ComfyUI installation path",
        default=default_path
    )
    
    console.print()
    return comfyui_url, comfyui_path


def step_workflow_and_api(workflow_arg: Optional[str]) -> Tuple[Path, str, str, str, int, str]:
    """Step 2: Load workflow and get API details."""
    console.print("[bold]Step 2 of 6: Workflow & API Details[/bold]")
    console.print("─" * 40)
    console.print()
    
    # Get workflow path
    if workflow_arg:
        workflow_path = Path(workflow_arg).expanduser().resolve()
        console.print(f"Workflow file: [cyan]{workflow_path}[/cyan]")
    else:
        workflow_str = Prompt.ask("Workflow JSON file path")
        workflow_path = Path(workflow_str).expanduser().resolve()
    
    if not workflow_path.exists():
        console.print(f"[red]Error: File not found: {workflow_path}[/red]")
        sys.exit(1)
    
    # Parse workflow
    nodes, error = parse_workflow(workflow_path)
    if error:
        console.print(f"[red]Error parsing workflow: {error}[/red]")
        sys.exit(1)
    
    # Count node types
    input_count = sum(1 for n in nodes.values() if categorize_node(n.class_type) == "Input")
    output_count = sum(1 for n in nodes.values() if categorize_node(n.class_type) == "Output")
    
    console.print(f"  [green]✓[/green] Loaded workflow with [cyan]{len(nodes)}[/cyan] nodes")
    console.print(f"  [green]✓[/green] Detected: [cyan]{input_count}[/cyan] input nodes, [cyan]{output_count}[/cyan] output nodes")
    console.print()
    
    # Get API details
    api_name = Prompt.ask(
        "API name (folder name, lowercase-dashes)",
        default=workflow_path.stem.lower().replace("_", "-").replace(" ", "-")
    )
    api_name = re.sub(r'[^a-z0-9-]', '', api_name.lower())
    
    display_name = Prompt.ask(
        "API display name",
        default=api_name.replace("-", " ").title()
    )
    
    description = Prompt.ask(
        "Description",
        default=f"Generate content using {display_name} workflow"
    )
    
    # Auto-allocate port
    default_port = allocate_port()
    port = IntPrompt.ask(
        f"Port number",
        default=default_port
    )
    
    # Output prefix
    default_prefix = api_name.upper().replace("-", "")[:6]
    prefix = Prompt.ask(
        "Output file prefix",
        default=default_prefix
    )
    
    console.print()
    return workflow_path, api_name, display_name, description, port, prefix


def step_define_inputs(nodes: Dict[str, WorkflowNode]) -> List[APIInput]:
    """Step 3: Define API inputs by selecting nodes."""
    console.print("[bold]Step 3 of 6: Define API Inputs[/bold]")
    console.print("─" * 40)
    console.print()
    console.print("[dim]Select nodes to expose as API inputs. Press 'q' when done.[/dim]")
    console.print()
    
    inputs: List[APIInput] = []
    
    while True:
        # Build choices
        choices = []
        for node_id, node in sorted(nodes.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            category = categorize_node(node.class_type)
            display = f"[{category[:3].upper()}] {node.display_name()}"
            choices.append(questionary.Choice(title=display, value=node_id))
        
        choices.append(questionary.Choice(title="[Done - finish adding inputs]", value="__done__"))
        
        selected = questionary.select(
            "Select a node (↑↓ to navigate, Enter to select):",
            choices=choices,
            style=WIZARD_STYLE
        ).ask()
        
        if selected == "__done__" or selected is None:
            break
        
        node = nodes[selected]
        console.print(f"\n[cyan]Node [{node.node_id}] \"{node.title}\" selected[/cyan]")
        
        # Show node inputs
        if not node.inputs:
            console.print("[dim]This node has no configurable inputs.[/dim]")
            continue
        
        # Let user choose which input to expose
        input_choices = []
        for inp in node.inputs:
            if inp.is_link:
                continue  # Skip linked inputs
            val_display = repr(inp.value)
            if len(val_display) > 40:
                val_display = val_display[:37] + "..."
            input_choices.append(questionary.Choice(
                title=f"{inp.key} (current: {val_display})",
                value=inp.key
            ))
        
        if not input_choices:
            console.print("[dim]This node only has linked inputs (no configurable values).[/dim]")
            continue
        
        input_choices.append(questionary.Choice(title="[Back - don't add from this node]", value="__back__"))
        
        selected_input = questionary.select(
            "Which input to expose?",
            choices=input_choices,
            style=WIZARD_STYLE
        ).ask()
        
        if selected_input == "__back__" or selected_input is None:
            continue
        
        # Get the input details
        node_input = next(i for i in node.inputs if i.key == selected_input)
        
        # Determine type from current value
        if isinstance(node_input.value, bool):
            default_type = "bool"
        elif isinstance(node_input.value, int):
            default_type = "int"
        elif isinstance(node_input.value, float):
            default_type = "float"
        else:
            default_type = "str"
        
        # Get parameter name
        param_name = Prompt.ask(
            "API parameter name",
            default=selected_input.lower().replace(" ", "_")
        )
        
        # Get type
        param_type = questionary.select(
            "Parameter type:",
            choices=["str", "int", "float", "bool"],
            default=default_type,
            style=WIZARD_STYLE
        ).ask()
        
        # Required?
        required = Confirm.ask("Required?", default=True)
        
        # Default value
        default_val = None
        if not required:
            default_str = Prompt.ask(
                "Default value (leave blank for none, 'random' for random int)",
                default=""
            )
            if default_str == "random":
                default_val = "random"
            elif default_str:
                if param_type == "int":
                    default_val = int(default_str)
                elif param_type == "float":
                    default_val = float(default_str)
                elif param_type == "bool":
                    default_val = default_str.lower() in ("true", "1", "yes")
                else:
                    default_val = default_str
        
        inputs.append(APIInput(
            name=param_name,
            param_type=param_type,
            required=required,
            default=default_val,
            node_id=node.node_id,
            input_key=selected_input,
            description=f"Maps to node [{node.node_id}].inputs.{selected_input}"
        ))
        
        console.print(f"  [green]✓[/green] Added: [cyan]{param_name}[/cyan] ({param_type}, {'required' if required else 'optional'})")
        console.print()
    
    console.print(f"\n[green]✓[/green] Defined [cyan]{len(inputs)}[/cyan] API inputs")
    console.print()
    return inputs


def step_conditional_disables(nodes: Dict[str, WorkflowNode], inputs: List[APIInput]) -> List[ConditionalDisable]:
    """Step 3b: Define conditional node disabling (optional)."""
    console.print("[bold]Step 3b: Conditional Node Disabling (Optional)[/bold]")
    console.print("─" * 40)
    console.print()
    
    if not inputs:
        console.print("[dim]No API inputs defined, skipping conditional disables.[/dim]")
        console.print()
        return []
    
    add_conditions = Confirm.ask(
        "Do you want to conditionally disable nodes based on inputs?",
        default=False
    )
    
    if not add_conditions:
        console.print()
        return []
    
    console.print()
    console.print("[dim]Available expressions:[/dim]")
    console.print("  EMPTY(field)  - True when field is empty/missing")
    console.print("  NOT(expr)     - Negates expression")
    console.print("  AND(a, b)     - Both must be true")
    console.print("  OR(a, b)      - Either must be true")
    console.print("  field == \"value\"  - Equality check")
    console.print()
    
    available_fields = [inp.name for inp in inputs]
    console.print(f"[dim]Available fields: {', '.join(available_fields)}[/dim]")
    console.print()
    
    conditionals: List[ConditionalDisable] = []
    
    while True:
        # Build node choices
        choices = []
        for node_id, node in sorted(nodes.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            choices.append(questionary.Choice(
                title=node.display_name(),
                value=node_id
            ))
        choices.append(questionary.Choice(title="[Done - finish adding conditions]", value="__done__"))
        
        selected = questionary.select(
            "Select node to conditionally disable:",
            choices=choices,
            style=WIZARD_STYLE
        ).ask()
        
        if selected == "__done__" or selected is None:
            break
        
        node = nodes[selected]
        console.print(f"\n[cyan]Node [{node.node_id}] will be disabled when condition is TRUE[/cyan]")
        
        condition = Prompt.ask("Condition expression")
        
        # Validate
        error = validate_condition(condition, available_fields)
        if error:
            console.print(f"[red]Invalid condition: {error}[/red]")
            continue
        
        description = Prompt.ask(
            "Description (optional)",
            default=f"Disable {node.title} when {condition}"
        )
        
        conditionals.append(ConditionalDisable(
            node_id=node.node_id,
            condition=condition,
            description=description
        ))
        
        console.print(f"  [green]✓[/green] Node [{node.node_id}] will be disabled when: [cyan]{condition}[/cyan]")
        console.print()
    
    console.print()
    return conditionals


def step_output_config(comfyui_path: str, prefix: str) -> OutputConfig:
    """Step 4: Configure output handling."""
    console.print("[bold]Step 4 of 6: Output Configuration[/bold]")
    console.print("─" * 40)
    console.print()
    
    # Ask for output location
    default_output = str(Path(comfyui_path).expanduser() / "output")
    comfy_output = Prompt.ask(
        "ComfyUI output directory",
        default=default_output
    )
    
    # Ask for pattern
    pattern = Prompt.ask(
        "Output filename pattern (glob)",
        default="ComfyUI_*.png"
    )
    
    # Determine extension from pattern
    ext_match = re.search(r'\.(\w+)$', pattern)
    extension = ext_match.group(1) if ext_match else "png"
    
    console.print()
    console.print(f"[dim]Files will be renamed: {pattern} → {prefix}_00001.{extension}[/dim]")
    console.print()
    
    return OutputConfig(
        comfy_dir=comfy_output,
        pattern=pattern,
        prefix=prefix,
        extension=extension,
        digits=5
    )


def step_endpoints() -> Dict[str, bool]:
    """Step 5: Select additional endpoints."""
    console.print("[bold]Step 5 of 6: Additional Endpoints[/bold]")
    console.print("─" * 40)
    console.print()
    
    endpoints = {
        "generate": True,
        "output": True,
    }
    
    choices = questionary.checkbox(
        "Select additional endpoints to include:",
        choices=[
            questionary.Choice("POST /upload - Upload files to ComfyUI input/", value="upload"),
            questionary.Choice("GET /health - Health check endpoint", value="health"),
            questionary.Choice("GET /info - API info and parameters", value="info"),
        ],
        style=WIZARD_STYLE
    ).ask()
    
    if choices:
        for choice in choices:
            endpoints[choice] = True
    
    console.print()
    return endpoints


def step_review_and_generate(config: APIConfig, workflow_path: Path) -> bool:
    """Step 6: Review configuration and generate."""
    console.print("[bold]Step 6 of 6: Review & Generate[/bold]")
    console.print("═" * 60)
    console.print()
    
    # Summary table
    table = Table(title="API Configuration Summary", box=box.ROUNDED)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Name", config.name)
    table.add_row("Display Name", config.display_name)
    table.add_row("Port", str(config.port))
    table.add_row("ComfyUI URL", config.comfyui_url)
    table.add_row("Workflow Nodes", "from " + str(workflow_path))
    
    console.print(table)
    console.print()
    
    # Inputs
    if config.inputs:
        console.print("[bold]API Inputs:[/bold]")
        for inp in config.inputs:
            req = "required" if inp.required else f"optional, default={inp.default}"
            console.print(f"  • [cyan]{inp.name}[/cyan] ({inp.param_type}, {req}) → node[{inp.node_id}].{inp.input_key}")
    else:
        console.print("[dim]No API inputs defined[/dim]")
    console.print()
    
    # Conditional disables
    if config.conditional_disables:
        console.print("[bold]Conditional Disables:[/bold]")
        for cd in config.conditional_disables:
            console.print(f"  • Node [{cd.node_id}] when: [cyan]{cd.condition}[/cyan]")
    console.print()
    
    # Output
    if config.output:
        console.print("[bold]Output:[/bold]")
        console.print(f"  Pattern: [cyan]{config.output.pattern}[/cyan]")
        console.print(f"  Renamed to: [cyan]{config.output.prefix}_#####.{config.output.extension}[/cyan]")
    console.print()
    
    # Endpoints
    console.print("[bold]Endpoints:[/bold]")
    for ep, enabled in config.endpoints.items():
        if enabled:
            console.print(f"  [green]✓[/green] /{ep}")
    console.print("  [green]✓[/green] /docs (Swagger UI)")
    console.print("  [green]✓[/green] /redoc (ReDoc)")
    console.print()
    
    console.print("─" * 60)
    
    choice = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Generate API", value="generate"),
            questionary.Choice("Cancel", value="cancel"),
        ],
        style=WIZARD_STYLE
    ).ask()
    
    return choice == "generate"


def generate_api(config: APIConfig, workflow_path: Path):
    """Generate the API files."""
    console.print()
    console.print("[bold]Generating API...[/bold]")
    console.print("─" * 40)
    
    # Create directory
    api_dir = COMFY_WORKFLOWS_DIR / config.name
    api_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"  [green]✓[/green] Created {api_dir.relative_to(PROJECT_ROOT)}/")
    
    # Copy workflow.json
    workflow_dest = api_dir / "workflow.json"
    shutil.copy(workflow_path, workflow_dest)
    console.print(f"  [green]✓[/green] Copied workflow.json")
    
    # Generate config.yaml
    config_content = generate_config_yaml(config)
    (api_dir / "config.yaml").write_text(config_content)
    console.print(f"  [green]✓[/green] Generated config.yaml")
    
    # Generate app.py
    app_content = generate_app_py(config)
    (api_dir / "app.py").write_text(app_content)
    line_count = len(app_content.splitlines())
    console.print(f"  [green]✓[/green] Generated app.py ({line_count} lines)")
    
    # Generate requirements.txt
    (api_dir / "requirements.txt").write_text(generate_requirements_txt())
    console.print(f"  [green]✓[/green] Generated requirements.txt")
    
    # Create directories
    (api_dir / "output").mkdir(exist_ok=True)
    console.print(f"  [green]✓[/green] Created output/ directory")
    
    if config.endpoints.get("upload", False):
        (api_dir / "upload").mkdir(exist_ok=True)
        console.print(f"  [green]✓[/green] Created upload/ directory")
    
    # Success message
    console.print()
    console.print(Panel.fit(
        f"[bold green]✅ API CREATED[/bold green]\n\n"
        f"Your API is ready at: [cyan]comfy-workflows/{config.name}/[/cyan]\n\n"
        f"[bold]To start manually:[/bold]\n"
        f"  cd comfy-workflows/{config.name}\n"
        f"  python -m venv .venv\n"
        f"  {get_venv_activate_cmd(Path('.venv'))}\n"
        f"  pip install -r requirements.txt\n"
        f"  python app.py\n\n"
        f"[bold]API Endpoints:[/bold]\n"
        f"  POST http://127.0.0.1:{config.port}/generate\n"
        f"  GET  http://127.0.0.1:{config.port}/output/<filename>\n"
        + (f"  POST http://127.0.0.1:{config.port}/upload\n" if config.endpoints.get("upload") else "")
        + f"  GET  http://127.0.0.1:{config.port}/docs (Swagger UI)",
        title="Success",
        border_style="green"
    ))
    
    # Ask to start
    console.print()
    start_now = Confirm.ask("Would you like to start the API now?", default=True)
    
    if start_now:
        start_api(api_dir, config.port)


def start_api(api_dir: Path, port: int):
    """Start the API in the foreground."""
    console.print()
    console.print(f"[cyan]Starting API on port {port}...[/cyan]")
    console.print(f"[dim]Press Ctrl+C to stop[/dim]")
    console.print()
    
    venv_dir = api_dir / ".venv"
    
    # Create venv if needed
    if not venv_dir.exists():
        console.print("[dim]Creating virtual environment...[/dim]")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        
        pip = get_venv_pip(venv_dir)
        console.print("[dim]Installing dependencies...[/dim]")
        subprocess.run(
            [str(pip), "install", "-r", str(api_dir / "requirements.txt")],
            check=True,
            capture_output=True
        )
    
    # Run the app
    python = get_venv_python(venv_dir)
    try:
        subprocess.run(
            [str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(api_dir)
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]API stopped[/yellow]")


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point for the wizard."""
    # Check for workflow argument
    workflow_arg = sys.argv[1] if len(sys.argv) > 1 else None
    
    print_header()
    
    try:
        # Step 1: ComfyUI configuration
        comfyui_url, comfyui_path = step_comfyui_config()
        
        # Step 2: Workflow and API details
        workflow_path, api_name, display_name, description, port, prefix = step_workflow_and_api(workflow_arg)
        
        # Parse workflow for node selection
        nodes, error = parse_workflow(workflow_path)
        if error:
            console.print(f"[red]Error: {error}[/red]")
            sys.exit(1)
        
        # Step 3: Define inputs
        inputs = step_define_inputs(nodes)
        
        # Step 3b: Conditional disables
        conditionals = step_conditional_disables(nodes, inputs)
        
        # Step 4: Output configuration
        output_config = step_output_config(comfyui_path, prefix)
        
        # Step 5: Endpoints
        endpoints = step_endpoints()
        
        # Build config
        config = APIConfig(
            name=api_name,
            display_name=display_name,
            description=description,
            port=port,
            comfyui_url=comfyui_url,
            comfyui_path=comfyui_path,
            workflow_file="workflow.json",
            inputs=inputs,
            conditional_disables=conditionals,
            output=output_config,
            endpoints=endpoints,
        )
        
        # Step 6: Review and generate
        if step_review_and_generate(config, workflow_path):
            generate_api(config, workflow_path)
        else:
            console.print("[yellow]Cancelled[/yellow]")
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
