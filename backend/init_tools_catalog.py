#!/usr/bin/env python3
"""
Initialize the Tools Catalog with available API-based tools.

This script:
1. Reads .env to determine which API keys are available
2. Creates tool entries for each available service
3. Can be re-run safely (idempotent):
   - Creates missing tools
   - Does not create duplicates
   - Disables tools when API keys are removed
4. Creates comprehensive, professional tool descriptions for agents

Usage:
    python init_tools_catalog.py

The script will create tools with status=IMPLEMENTED for services with valid API keys.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from uuid import UUID

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from app.core.database import get_db, get_session_maker
from app.core.config import settings
from app.models import Tool, User, ToolStatus, ToolCategory


# Load environment variables
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)


# Placeholder values that should not be treated as real API keys
_PLACEHOLDER_KEYS = {
    "your_zai_api_key_here",
    "your_openai_api_key_here",
    "your_anthropic_api_key_here",
    "your_elevenlabs_api_key_here",
    "your_serper_api_key_here",
}


def _is_real_key(key: Optional[str]) -> bool:
    """Check if an API key is real (not empty or placeholder)."""
    return bool(key) and key not in _PLACEHOLDER_KEYS


# ── Input Schemas (JSON Schema) for tool workbench / validation ──────────

INPUT_SCHEMA_LLM = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The text prompt to send to the LLM"
        },
        "system_prompt": {
            "type": "string",
            "description": "Optional system prompt to set the LLM's behavior"
        },
        "temperature": {
            "type": "number",
            "description": "Sampling temperature (0.0–2.0)",
            "default": 0.7,
            "minimum": 0.0,
            "maximum": 2.0
        },
        "max_tokens": {
            "type": "integer",
            "description": "Maximum tokens to generate",
            "default": 4096,
            "minimum": 1
        }
    },
    "required": ["prompt"]
}

INPUT_SCHEMA_DALLE = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Text description of the image to generate"
        },
        "n": {
            "type": "integer",
            "description": "Number of images to generate",
            "default": 1,
            "minimum": 1,
            "maximum": 10
        },
        "size": {
            "type": "string",
            "description": "Image size",
            "default": "1024x1024",
            "enum": ["1024x1024", "1024x1792", "1792x1024"]
        },
        "quality": {
            "type": "string",
            "description": "Image quality",
            "default": "standard",
            "enum": ["standard", "hd"]
        },
        "style": {
            "type": "string",
            "description": "Image style",
            "default": "vivid",
            "enum": ["vivid", "natural"]
        }
    },
    "required": ["prompt"]
}

INPUT_SCHEMA_SERPER = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query text"
        },
        "num": {
            "type": "integer",
            "description": "Number of results to return",
            "default": 10,
            "minimum": 1,
            "maximum": 100
        },
        "type": {
            "type": "string",
            "description": "Type of search",
            "default": "search",
            "enum": ["search", "news", "images"]
        },
        "gl": {
            "type": "string",
            "description": "Country code for search results",
            "default": "us"
        }
    },
    "required": ["query"]
}

INPUT_SCHEMA_ELEVENLABS = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Text to convert to speech"
        },
        "voice_id": {
            "type": "string",
            "description": "ElevenLabs voice ID (default: Rachel)",
            "default": "21m00Tcm4TlvDq8ikWAM"
        },
        "model_id": {
            "type": "string",
            "description": "ElevenLabs model ID",
            "default": "eleven_monolingual_v1"
        },
        "stability": {
            "type": "number",
            "description": "Voice stability (0.0–1.0)",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 1.0
        },
        "similarity_boost": {
            "type": "number",
            "description": "Voice similarity boost (0.0–1.0)",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 1.0
        }
    },
    "required": ["text"]
}

INPUT_SCHEMA_SUNO = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Description of the music to generate"
        }
    },
    "required": ["prompt"]
}

INPUT_SCHEMA_ACESTEP = {
    "type": "object",
    "properties": {
        "lyrics": {
            "type": "string",
            "description": "Song lyrics to set to music (leave empty for instrumental)"
        },
        "style": {
            "type": "string",
            "description": "Music style/genre (e.g. pop, rock, jazz, classical)"
        },
        "model": {
            "type": "string",
            "description": "DiT model variant. Turbo: fast (8 steps, ~15s/min of audio). Base: slower but more diverse (27-60 steps). XL variants use the 4B DiT decoder for higher quality but need more VRAM (>=16GB with offloading, >=20GB without). xl-turbo: fast XL (8 steps). xl-base: slower XL (50 steps). xl-sft: highest quality XL.",
            "enum": ["turbo", "base", "xl-turbo", "xl-base", "xl-sft"],
            "default": "turbo"
        },
        "duration": {
            "type": "number",
            "description": "Duration in seconds",
            "default": 60.0,
            "minimum": 1.0,
            "maximum": 300.0
        },
        "steps": {
            "type": "integer",
            "description": "Inference steps — controls quality vs speed. Turbo/xl-turbo: fixed at 8. Base: 27-60 recommended (default 45). XL-base/xl-sft: 30-60 recommended (default 50). Leave blank to use the model's default.",
            "minimum": 1
        },
        "instrumental": {
            "type": "boolean",
            "description": "Generate instrumental only (no vocals)",
            "default": False
        },
        "temperature": {
            "type": "number",
            "description": "Generation temperature",
            "default": 0.95,
            "minimum": 0.0,
            "maximum": 2.0
        },
        "guidance_scale": {
            "type": "number",
            "description": "Classifier-free guidance scale",
            "default": 3.5,
            "minimum": 0.0
        },
        "batch_size": {
            "type": "integer",
            "description": "Number of variations to generate",
            "default": 1,
            "minimum": 1,
            "maximum": 4
        },
        "seed": {
            "type": "integer",
            "description": "Random seed for reproducibility"
        }
    },
    "required": []
}

INPUT_SCHEMA_QWEN3_TTS = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Text to convert to speech"
        },
        "mode": {
            "type": "string",
            "description": "TTS mode: custom_voice (built-in speaker + optional style instruction), voice_clone (clone from audio sample), voice_design (describe the voice you want in text), voice_design_clone (design a voice then use it as clone reference)",
            "default": "custom_voice",
            "enum": ["custom_voice", "voice_clone", "voice_design", "voice_design_clone"]
        },
        "voice": {
            "type": "string",
            "description": "Built-in voice name for custom_voice mode (e.g. Chelsie, Ethan, Aiden, Ryan)"
        },
        "instruct": {
            "type": "string",
            "description": "Style instruction for custom_voice mode (e.g. 'Speak happily', 'Whisper softly', 'Read like a news anchor')"
        },
        "reference_audio": {
            "type": "string",
            "description": "Filename of uploaded voice sample for voice_clone mode (upload via /upload_voice first)"
        },
        "reference_text": {
            "type": "string",
            "description": "Transcript of the reference audio (improves clone quality)"
        },
        "voice_description": {
            "type": "string",
            "description": "Natural language description of desired voice for voice_design mode (e.g. 'A warm female voice with a slight British accent')"
        }
    },
    "required": ["text"]
}

INPUT_SCHEMA_ZIMAGE = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Text description of the image to generate"
        },
        "negative_prompt": {
            "type": "string",
            "description": "Things to avoid in the image"
        },
        "width": {
            "type": "integer",
            "description": "Image width in pixels",
            "default": 1024,
            "minimum": 256,
            "maximum": 2048
        },
        "height": {
            "type": "integer",
            "description": "Image height in pixels",
            "default": 1024,
            "minimum": 256,
            "maximum": 2048
        },
        "num_inference_steps": {
            "type": "integer",
            "description": "Number of denoising steps (higher = better quality)",
            "minimum": 1,
            "maximum": 100
        },
        "guidance_scale": {
            "type": "number",
            "description": "Classifier-free guidance scale",
            "minimum": 0.0,
            "maximum": 20.0
        },
        "seed": {
            "type": "integer",
            "description": "Random seed for reproducibility"
        },
        "num_images_per_prompt": {
            "type": "integer",
            "description": "Number of images to generate",
            "default": 1,
            "minimum": 1,
            "maximum": 4
        }
    },
    "required": ["prompt"]
}

INPUT_SCHEMA_SEEDVR2 = {
    "type": "object",
    "properties": {
        "image_url": {
            "type": "string",
            "description": "URL of image to upscale (from Z-Image or other tool output)"
        },
        "video_url": {
            "type": "string",
            "description": "URL of video to upscale (from LTX-Video or other tool output)"
        },
        "resolution": {
            "type": "integer",
            "description": "Target resolution (height in pixels)",
            "default": 1080
        },
        "max_resolution": {
            "type": "integer",
            "description": "Maximum resolution limit (0 = no limit)",
            "default": 0
        },
        "color_correction": {
            "type": "string",
            "description": "Color correction method",
            "default": "lab",
            "enum": ["lab", "none"]
        },
        "seed": {
            "type": "integer",
            "description": "Random seed for reproducibility"
        },
        "batch_size": {
            "type": "integer",
            "description": "Video only: number of frames to process per batch (affects VRAM usage). Must follow 4n+1 formula: 5, 9, 13, 17... Lower values use less VRAM but are slower.",
            "default": 5,
            "minimum": 1
        },
        "temporal_overlap": {
            "type": "integer",
            "description": "Video only: number of overlapping frames between batches for smooth blending",
            "default": 2,
            "minimum": 0
        }
    },
    "required": []
}

INPUT_SCHEMA_CANARY_STT = {
    "type": "object",
    "properties": {
        "audio_url": {
            "type": "string",
            "description": "URL of audio file to transcribe"
        },
        "audio_path": {
            "type": "string",
            "description": "Local path to audio file to transcribe"
        },
        "save_transcript": {
            "type": "boolean",
            "description": "Save transcript to file",
            "default": False
        }
    },
    "required": []
}

INPUT_SCHEMA_AUDIOSR = {
    "type": "object",
    "properties": {
        "audio_url": {
            "type": "string",
            "description": "URL of audio file to enhance (upscale to 48kHz)"
        },
        "audio_path": {
            "type": "string",
            "description": "Local path to audio file to enhance"
        },
        "model_name": {
            "type": "string",
            "description": "AudioSR model variant. 'basic': general-purpose audio super-resolution (music, environmental, speech). 'speech': optimized specifically for speech clarity and intelligibility. If omitted, uses the server's configured default (usually 'basic').",
            "enum": ["basic", "speech"],
            "default": "basic"
        },
        "ddim_steps": {
            "type": "integer",
            "description": "Number of diffusion denoising steps (higher = better quality, slower)",
            "default": 50
        },
        "guidance_scale": {
            "type": "number",
            "description": "Classifier-free guidance scale",
            "default": 3.5
        },
        "seed": {
            "type": "integer",
            "description": "Random seed for reproducible results (optional)"
        }
    },
    "required": []
}

INPUT_SCHEMA_DOCLING = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "URL of document to parse (PDF, DOCX, PPTX, HTML, image, etc.). Can be a URL to a sibling service output or any web URL."
        },
        "file_path": {
            "type": "string",
            "description": "Local file path of document to parse (provide this OR url)"
        },
        "output_format": {
            "type": "string",
            "enum": ["markdown", "json", "text"],
            "description": "Output format: 'markdown' (structured with headers/tables, default), 'json' (full document structure), 'text' (plain text, no formatting)",
            "default": "markdown"
        }
    },
    "required": []
}

INPUT_SCHEMA_REALESRGAN_CPU = {
    "type": "object",
    "properties": {
        "image_url": {
            "type": "string",
            "description": "URL of image to upscale (provide this OR video_url)"
        },
        "video_url": {
            "type": "string",
            "description": "URL of video to upscale (provide this OR image_url). WARNING: CPU video upscaling is SLOW — use short clips only (< 2 minutes)"
        },
        "model_name": {
            "type": "string",
            "description": "Real-ESRGAN model to use. 'realesr-animevideov3': fastest, optimized for anime & video (default). 'realesrgan-x4plus': best quality for real-world photos. 'realesrnet-x4plus': faster alternative for photos. 'realesrgan-x4plus-anime': best for anime images. Model is swapped on-the-fly if different from current.",
            "enum": ["realesr-animevideov3", "realesrgan-x4plus", "realesrnet-x4plus", "realesrgan-x4plus-anime"],
            "default": "realesr-animevideov3"
        },
        "scale": {
            "type": "integer",
            "enum": [2, 4],
            "description": "Upscale factor: 2x (faster) or 4x (higher quality). Default: 2"
        },
        "tile": {
            "type": "integer",
            "description": "Tile size for processing (0=no tiling, 4-8 recommended for CPU). Higher values use less memory but may show seams. Default: 4"
        }
    },
    "required": []
}

INPUT_SCHEMA_MEDIA_TOOLKIT = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["probe", "extract_audio", "strip_audio", "combine",
                     "mix_audio", "adjust_volume", "create_slideshow", "concat", "trim"],
            "description": "The media operation to perform"
        },
        "url": {
            "type": "string",
            "description": "URL of any media file (for probe, trim, adjust_volume)"
        },
        "video_url": {
            "type": "string",
            "description": "URL of video file (for extract_audio, strip_audio, combine)"
        },
        "audio_url": {
            "type": "string",
            "description": "URL of audio file (for combine, create_slideshow)"
        },
        "audio_tracks": {
            "type": "array",
            "description": "Audio tracks for combine operation. Each: {url, volume (0-5, default 1), start_time, fade_in, fade_out, loop}",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL of audio file"},
                    "volume": {"type": "number", "description": "Volume multiplier (0-5, default 1.0)", "default": 1.0},
                    "start_time": {"type": "number", "description": "Start offset in seconds", "default": 0.0},
                    "fade_in": {"type": "number", "description": "Fade-in seconds", "default": 0.0},
                    "fade_out": {"type": "number", "description": "Fade-out seconds", "default": 0.0},
                    "loop": {"type": "boolean", "description": "Loop to fill duration", "default": False}
                },
                "required": ["url"]
            }
        },
        "tracks": {
            "type": "array",
            "description": "Audio tracks for mix_audio operation (same schema as audio_tracks)",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "volume": {"type": "number", "default": 1.0},
                    "start_time": {"type": "number", "default": 0.0},
                    "fade_in": {"type": "number", "default": 0.0},
                    "fade_out": {"type": "number", "default": 0.0},
                    "loop": {"type": "boolean", "default": False}
                },
                "required": ["url"]
            }
        },
        "images": {
            "type": "array",
            "description": "Images for create_slideshow. Each: {url, duration (seconds), effect}",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL of image (from Z-Image, SeedVR2, etc.)"},
                    "duration": {"type": "number", "description": "Display duration in seconds", "default": 5.0},
                    "effect": {"type": "string", "description": "Ken Burns effect", "enum": ["none", "zoom_in", "zoom_out", "pan_left", "pan_right", "ken_burns"], "default": "none"}
                },
                "required": ["url"]
            }
        },
        "files": {
            "type": "array",
            "description": "List of media file URLs to concatenate (for concat)",
            "items": {"type": "string"}
        },
        "replace_audio": {
            "type": "boolean",
            "description": "Replace existing audio in video (for combine)",
            "default": True
        },
        "format": {
            "type": "string",
            "description": "Output format",
            "enum": ["wav", "mp3", "mp4", "flac"]
        },
        "sample_rate": {
            "type": "integer",
            "description": "Output sample rate for audio operations"
        },
        "start_time": {
            "type": "number",
            "description": "Trim start time in seconds"
        },
        "end_time": {
            "type": "number",
            "description": "Trim end time in seconds"
        },
        "duration": {
            "type": "number",
            "description": "Duration in seconds (for trim or mix_audio)"
        },
        "volume": {
            "type": "number",
            "description": "Volume multiplier for adjust_volume (0.0 - 5.0)",
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 5.0
        },
        "normalize": {
            "type": "boolean",
            "description": "Normalize audio loudness (EBU R128, -16 LUFS)",
            "default": False
        },
        "fps": {
            "type": "integer",
            "description": "Frames per second for slideshow",
            "default": 24
        },
        "transition": {
            "type": "string",
            "description": "Transition effect for concat/slideshow",
            "enum": ["none", "crossfade"],
            "default": "none"
        },
        "transition_duration": {
            "type": "number",
            "description": "Transition duration in seconds",
            "default": 0.5
        },
        "output_format": {
            "type": "string",
            "description": "Output format for mix_audio",
            "enum": ["wav", "mp3", "flac", "aac"]
        }
    },
    "required": ["operation"]
}

INPUT_SCHEMA_LTX_VIDEO = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Text description of the video to generate"
        },
        "width": {
            "type": "integer",
            "description": "Video width in pixels",
            "default": 768,
            "minimum": 256,
            "maximum": 1280
        },
        "height": {
            "type": "integer",
            "description": "Video height in pixels",
            "default": 512,
            "minimum": 256,
            "maximum": 1280
        },
        "num_frames": {
            "type": "integer",
            "description": "Number of frames to generate",
            "default": 241,
            "minimum": 1
        },
        "fps": {
            "type": "integer",
            "description": "Frames per second",
            "default": 24,
            "minimum": 1,
            "maximum": 60
        },
        "seed": {
            "type": "integer",
            "description": "Random seed for reproducibility"
        },
        "enhance_prompt": {
            "type": "boolean",
            "description": "Enhance prompt via Ollama before generation",
            "default": False
        }
    },
    "required": ["prompt"]
}

INPUT_SCHEMA_DEV_SANDBOX = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "Sandbox operation to perform",
            "enum": [
                "create", "exec", "write", "read", "list",
                "extract", "destroy", "info", "run_script",
                "write_files", "setup"
            ]
        },
        "sandbox_id": {
            "type": "string",
            "description": "Sandbox ID (required for most actions except create/info/setup)"
        },
        "command": {
            "type": "string",
            "description": "Shell command to execute (for exec action)"
        },
        "script": {
            "type": "string",
            "description": "Script content to run (for run_script action)"
        },
        "interpreter": {
            "type": "string",
            "description": "Script interpreter (for run_script action)",
            "default": "sh"
        },
        "path": {
            "type": "string",
            "description": "File path in sandbox (for write/read/list actions)"
        },
        "content": {
            "type": "string",
            "description": "File content (for write action)"
        },
        "image": {
            "type": "string",
            "description": "Docker image (for create/setup actions)",
            "default": "python:3.12-slim"
        },
        "memory_limit": {
            "type": "string",
            "description": "Memory limit (for create/setup actions)",
            "default": "512m"
        },
        "cpu_count": {
            "type": "number",
            "description": "CPU count (for create/setup actions)",
            "default": 1.0
        },
        "network_access": {
            "type": "boolean",
            "description": "Allow network access (for create/setup actions)",
            "default": False
        },
        "timeout": {
            "type": "integer",
            "description": "Command timeout in seconds",
            "default": 60
        },
        "workdir": {
            "type": "string",
            "description": "Working directory in the sandbox",
            "default": "/workspace"
        }
    },
    "required": ["action"]
}

INPUT_SCHEMA_LND_LIGHTNING = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "balance", "info", "create_invoice", "decode_invoice",
                "pay_invoice", "send_onchain", "list_payments",
                "list_invoices", "list_channels", "estimate_fee"
            ],
            "description": "The Lightning Network operation to perform"
        },
        "payment_request": {
            "type": "string",
            "description": "BOLT-11 payment request (for pay_invoice and decode_invoice)"
        },
        "amount_sats": {
            "type": "integer",
            "description": "Amount in satoshis (for create_invoice, send_onchain, estimate_fee)"
        },
        "address": {
            "type": "string",
            "description": "Bitcoin on-chain address (for send_onchain, estimate_fee)"
        },
        "memo": {
            "type": "string",
            "description": "Invoice memo/description (for create_invoice)"
        },
        "expiry_seconds": {
            "type": "integer",
            "description": "Invoice expiry in seconds (default: 3600)",
            "default": 3600
        },
        "campaign_id": {
            "type": "string",
            "description": "Campaign UUID for budget enforcement (for pay_invoice, send_onchain)"
        },
        "fee_limit_sats": {
            "type": "integer",
            "description": "Maximum fee in sats for Lightning payment (default: 1% of amount, min 10)"
        },
        "fee_priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Fee priority for on-chain transactions: low (~1hr), medium (~30min, default), high (next block). Fetches current fee rates from Mempool Explorer. Use for send_onchain and estimate_fee.",
            "default": "medium"
        },
        "sat_per_vbyte": {
            "type": "integer",
            "description": "Explicit fee rate override in sat/vByte for on-chain transactions. Overrides fee_priority if both are set."
        },
        "limit": {
            "type": "integer",
            "description": "Number of results for list actions (default: 20)",
            "default": 20
        },
        "notes": {
            "type": "string",
            "description": "Agent notes explaining why this transaction is being made (for pay_invoice, send_onchain, create_invoice). Recorded in the transaction ledger for auditability."
        }
    },
    "required": ["action"]
}

INPUT_SCHEMA_NOSTR = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "create_identity", "list_identities", "get_identity", "update_profile",
                "post_note", "post_article", "react", "repost", "reply",
                "follow", "unfollow", "delete_event",
                "search", "get_feed", "get_thread", "get_profile", "get_engagement",
                "get_zap_receipts", "send_zap"
            ],
            "description": "The Nostr action to perform"
        },
        "identity_id": {
            "type": "string",
            "description": "UUID of the managed Nostr identity to act as"
        },
        "name": {
            "type": "string",
            "description": "Display name for identity profile"
        },
        "about": {
            "type": "string",
            "description": "Bio/description for identity profile"
        },
        "picture": {
            "type": "string",
            "description": "URL of profile picture"
        },
        "nip05": {
            "type": "string",
            "description": "NIP-05 DNS identifier (e.g. user@domain.com)"
        },
        "lud16": {
            "type": "string",
            "description": "Lightning address for receiving zaps (e.g. user@walletofsatoshi.com)"
        },
        "content": {
            "type": "string",
            "description": "Text content for notes, replies, or articles"
        },
        "title": {
            "type": "string",
            "description": "Title for long-form articles (kind 30023)"
        },
        "summary": {
            "type": "string",
            "description": "Summary for long-form articles"
        },
        "image": {
            "type": "string",
            "description": "Header image URL for articles"
        },
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of hashtag strings (without #)"
        },
        "event_id": {
            "type": "string",
            "description": "Hex event ID to interact with"
        },
        "event_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of hex event IDs (for deletion)"
        },
        "reply_to": {
            "type": "string",
            "description": "Hex event ID of the note being replied to"
        },
        "reaction": {
            "type": "string",
            "description": "Reaction content (default: '+')",
            "default": "+"
        },
        "pubkeys": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of hex pubkeys to follow/unfollow"
        },
        "pubkey_or_npub": {
            "type": "string",
            "description": "Hex pubkey or npub1... of a user"
        },
        "query": {
            "type": "string",
            "description": "Search query string (NIP-50)"
        },
        "kinds": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Event kinds to filter by"
        },
        "target": {
            "type": "string",
            "description": "Zap target — npub or hex pubkey"
        },
        "amount_sats": {
            "type": "integer",
            "description": "Amount in satoshis for zaps"
        },
        "comment": {
            "type": "string",
            "description": "Optional comment attached to a zap"
        },
        "since": {
            "type": "integer",
            "description": "Unix timestamp — only return events after this time"
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of items to return (max 20)",
            "default": 10
        },
        "include_posts": {
            "type": "boolean",
            "description": "Include recent posts with profile",
            "default": False
        },
        "relays": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Custom relay URLs for this identity (wss://...)"
        }
    },
    "required": ["action"]
}


# Tool definitions with comprehensive metadata
TOOL_DEFINITIONS = {
    # Z.ai / Zhipu AI LLM Provider
    "z_ai_llm": {
        "condition": lambda: _is_real_key(settings.z_ai_api_key),
        "name": "Z.ai LLM",
        "slug": "z-ai-llm",
        "category": ToolCategory.API,
        "description": "Primary LLM provider via Z.ai (Zhipu AI). Supports three tiers: fast (glm-4.7-flash, FREE), reasoning (glm-4.7), and quality (glm-5). Best value for high-volume operations.",
        "tags": ["llm", "ai", "text-generation", "primary", "cost-effective", "tiered"],
        "usage_instructions": """# Z.ai LLM Provider

## Overview
Z.ai provides access to GLM models through a tiered system. The Money Agents framework automatically selects the appropriate model based on the requested tier.

## Tiers
| Tier | Model | Use Case | Pricing |
|------|-------|----------|---------|
| **fast** | glm-4.7-flash | Quick responses, high-volume | **FREE!** |
| **reasoning** | glm-4.7 | Complex tasks requiring reasoning | $0.60/$2.20 per 1M tokens |
| **quality** | glm-5 | Highest quality output | $0.55/$2.50 per 1M tokens |

## Configuration
- `LLM_PROVIDER_PRIORITY=glm,claude,openai` - GLM first in fallback chain
- `max_tokens` default: 4096 (8192 for Proposal Writer refinement)

## Usage in Agents
```python
# In agent code - just specify the tier
response = await llm_service.generate(
    messages=messages,
    model="fast",  # Uses glm-4.7-flash (FREE)
    max_tokens=4096
)

# Force GLM even if not first in priority
response = await llm_service.generate(
    messages=messages,
    model="glm:reasoning",  # Forces glm-4.7
)
```

## Best Practices
- Use "fast" tier for routine operations (it's FREE!)
- Use "reasoning" tier for complex multi-step tasks
- Monitor token usage even on free tier (rate limits apply)
- GLM reasoning model returns `reasoning_content` - ensure max_tokens is sufficient""",
        "example_code": """# Via LLM Service (recommended)
from app.services.llm_service import LLMService, LLMMessage

llm = LLMService()

# Fast tier (FREE glm-4.7-flash)
response = await llm.generate(
    messages=[
        LLMMessage(role="system", content="You are a helpful assistant."),
        LLMMessage(role="user", content="Summarize this article...")
    ],
    model="fast",
    temperature=0.7,
    max_tokens=4096
)

# Reasoning tier (glm-4.7 with reasoning)
response = await llm.generate(
    messages=messages,
    model="reasoning",
    max_tokens=8192  # Higher for reasoning tasks
)""",
        "required_environment_variables": {
            "Z_AI_API_KEY": "API key for Z.ai/Zhipu AI service"
        },
        "integration_complexity": "low",
        "cost_model": "tiered",
        "cost_details": {
            "tiers": {
                "fast": {
                    "model": "glm-4.7-flash",
                    "input_per_1m": 0.00,
                    "output_per_1m": 0.00,
                    "notes": "FREE - best for high volume"
                },
                "reasoning": {
                    "model": "glm-4.7",
                    "input_per_1m": 0.60,
                    "output_per_1m": 2.20,
                    "notes": "Has reasoning_content capability"
                },
                "quality": {
                    "model": "glm-5",
                    "input_per_1m": 0.55,
                    "output_per_1m": 2.50,
                    "notes": "Top quality GLM model (2025)"
                }
            },
            "currency": "USD"
        },
        "strengths": """- **FREE fast tier**: glm-4.7-flash has no cost for most operations
- **Reasoning support**: glm-4.7 provides reasoning_content for complex tasks
- **Primary provider**: First in fallback chain for reliability
- **Fast inference**: Quick response times
- **Good balance**: Cost-effective yet capable""",
        "weaknesses": """- **Rate limits**: Free tier has daily request limits
- **Context window**: Smaller than Claude (~8k vs 200k)
- **Less documented**: Fewer community examples than OpenAI/Anthropic
- **Reasoning output**: May need higher max_tokens for reasoning models""",
        "best_use_cases": """- Primary LLM for all agent operations (fast tier is FREE)
- High-volume text generation and summarization
- Initial proposal drafting and content analysis
- Routine conversational tasks
- Use reasoning tier for complex multi-step planning""",
        "external_documentation_url": "https://open.bigmodel.cn/dev/api",
        "version": "tiered",
        "priority": "critical",
        "input_schema": INPUT_SCHEMA_LLM
    },
    
    # Anthropic Claude LLM Provider
    "anthropic_llm": {
        "condition": lambda: _is_real_key(settings.anthropic_api_key),
        "name": "Anthropic LLM",
        "slug": "anthropic-llm",
        "category": ToolCategory.API,
        "description": "Secondary LLM provider via Anthropic. Supports three tiers: fast (claude-haiku-4-5), reasoning (claude-sonnet-4-6), and quality (claude-opus-4-6). Best for complex reasoning and long context.",
        "tags": ["llm", "ai", "text-generation", "fallback", "high-reliability", "reasoning", "tiered"],
        "usage_instructions": """# Anthropic LLM Provider

## Overview
Anthropic provides Claude models through a tiered system. Used as fallback when Z.ai is unavailable, or when explicitly requested for high-stakes tasks.

## Tiers
| Tier | Model | Use Case | Pricing (per 1M tokens) |
|------|-------|----------|--------------------------|
| **fast** | claude-haiku-4-5 | Quick responses | $1.00/$5.00 |
| **reasoning** | claude-sonnet-4-6 | Balanced quality/speed | $3.00/$15.00 |
| **quality** | claude-opus-4-6 | Best reasoning, long context | $5.00/$25.00 |

## Configuration
- `LLM_PROVIDER_PRIORITY=glm,claude,openai` - Claude second in fallback chain
- `max_tokens` default: 4096 (8192 for Proposal Writer refinement)
- Context window: up to 200k tokens (excellent for long documents)

## Usage in Agents
```python
# Force Claude provider
response = await llm_service.generate(
    messages=messages,
    model="claude:reasoning",  # Forces claude-sonnet-4-6
    max_tokens=4096
)

# Or just request a tier (uses priority order)
response = await llm_service.generate(
    messages=messages,
    model="quality",  # Will try GLM first, then Claude
)
```

## Best Practices
- Reserve for high-value tasks (more expensive than GLM)
- Excellent for long document analysis (200k context)
- Use for final proposal refinement and critical decisions
- Opus tier best for complex multi-step reasoning""",
        "example_code": """# Via LLM Service (recommended)
from app.services.llm_service import LLMService, LLMMessage

llm = LLMService()

# Force Claude for critical analysis
response = await llm.generate(
    messages=[
        LLMMessage(role="system", content="You are an expert business analyst."),
        LLMMessage(role="user", content="Analyze risks in this proposal...")
    ],
    model="claude:quality",  # Forces claude-opus-4-6
    temperature=0.3,  # Lower for analytical tasks
    max_tokens=8192
)

# Balanced reasoning (sonnet)
response = await llm.generate(
    messages=messages,
    model="claude:reasoning",
    max_tokens=4096
)""",
        "required_environment_variables": {
            "ANTHROPIC_API_KEY": "API key for Anthropic Claude"
        },
        "integration_complexity": "low",
        "cost_model": "tiered",
        "cost_details": {
            "tiers": {
                "fast": {
                    "model": "claude-haiku-4-5",
                    "input_per_1m": 1.00,
                    "output_per_1m": 5.00,
                    "notes": "Fast and cheap"
                },
                "reasoning": {
                    "model": "claude-sonnet-4-6",
                    "input_per_1m": 3.00,
                    "output_per_1m": 15.00,
                    "notes": "Balanced quality/speed"
                },
                "quality": {
                    "model": "claude-opus-4-6",
                    "input_per_1m": 5.00,
                    "output_per_1m": 25.00,
                    "notes": "Best reasoning, 200k context"
                }
            },
            "currency": "USD"
        },
        "strengths": """- **Superior reasoning**: Opus is best-in-class for complex analysis
- **Long context**: 200k token window for document analysis
- **Reliable**: Consistent high-quality outputs
- **Safety-focused**: Built-in content moderation
- **Excellent fallback**: Second in provider priority""",
        "weaknesses": """- **Cost**: Significantly more expensive than GLM
- **Speed**: Slightly slower than GLM-flash
- **Opus pricing**: Quality tier is expensive ($15/$75 per 1M)""",
        "best_use_cases": """- Fallback when GLM unavailable
- Complex multi-step reasoning tasks
- Critical business decision analysis
- Long document analysis (200k context)
- Final proposal refinement (use quality tier)
- High-stakes content generation""",
        "external_documentation_url": "https://docs.anthropic.com/claude/docs",
        "version": "tiered",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_LLM
    },
    
    # OpenAI LLM Provider
    "openai_llm": {
        "condition": lambda: _is_real_key(settings.openai_api_key),
        "name": "OpenAI LLM",
        "slug": "openai-llm",
        "category": ToolCategory.API,
        "description": "Tertiary LLM provider via OpenAI. Supports three tiers: fast (gpt-4.1-mini), reasoning (o4-mini), and quality (gpt-4.1). Last fallback option with broad ecosystem integration.",
        "tags": ["llm", "ai", "text-generation", "tertiary", "enterprise", "tiered"],
        "usage_instructions": """# OpenAI LLM Provider

## Overview
OpenAI provides GPT-4.1 and o-series models through a tiered system. Used as tertiary fallback when both Z.ai and Anthropic are unavailable.

## Tiers
| Tier | Model | Use Case | Pricing (per 1M tokens) |
|------|-------|----------|-------------------------|
| **fast** | gpt-4.1-mini | Quick responses, cheap | $0.40/$1.60 |
| **reasoning** | o4-mini | Reasoning tasks | $1.10/$4.40 |
| **quality** | gpt-4.1 | High quality output | $2.00/$8.00 |

## Configuration
- `LLM_PROVIDER_PRIORITY=glm,claude,openai` - OpenAI last in fallback chain
- `max_tokens` default: 4096 (uses `max_completion_tokens` for o-series models)
- Note: o-series models don't support `temperature` parameter

## Usage in Agents
```python
# Force OpenAI provider
response = await llm_service.generate(
    messages=messages,
    model="openai:fast",  # Forces gpt-4.1-mini
    max_tokens=4096
)

# Reasoning with o4-mini
response = await llm_service.generate(
    messages=messages,
    model="openai:reasoning",  # Uses o4-mini
)
```

## Best Practices
- Use only as last-resort fallback
- gpt-4.1-mini is cost-effective for fast tier
- o-series models have built-in reasoning (no temperature)
- Integrates with other OpenAI services (DALL-E, Whisper)""",
        "example_code": """# Via LLM Service (recommended)
from app.services.llm_service import LLMService, LLMMessage

llm = LLMService()

# Fast tier (gpt-4.1-mini)
response = await llm.generate(
    messages=[
        LLMMessage(role="system", content="You are a helpful assistant."),
        LLMMessage(role="user", content="Quick summary please...")
    ],
    model="openai:fast",
    max_tokens=4096
)

# Reasoning tier (o4-mini)
response = await llm.generate(
    messages=messages,
    model="openai:reasoning",
    # Note: o-series models ignore temperature
)""",
        "required_environment_variables": {
            "OPENAI_API_KEY": "API key for OpenAI"
        },
        "integration_complexity": "low",
        "cost_model": "tiered",
        "cost_details": {
            "tiers": {
                "fast": {
                    "model": "gpt-4.1-mini",
                    "input_per_1m": 0.40,
                    "output_per_1m": 1.60,
                    "notes": "Newer gen mini, cost-effective"
                },
                "reasoning": {
                    "model": "o4-mini",
                    "input_per_1m": 1.10,
                    "output_per_1m": 4.40,
                    "notes": "Latest optimised reasoning, no temperature"
                },
                "quality": {
                    "model": "gpt-4.1",
                    "input_per_1m": 2.00,
                    "output_per_1m": 8.00,
                    "notes": "Current flagship, better value than gpt-4o"
                }
            },
            "currency": "USD"
        },
        "strengths": """- **Ecosystem**: Integrates with DALL-E, Whisper, etc.
- **gpt-4.1-mini**: Cost-effective fast option, newer than gpt-4o-mini
- **o-series reasoning**: Built-in chain-of-thought (o4-mini is latest)
- **Multimodal**: gpt-4.1 supports images
- **Documentation**: Extensive docs and examples""",
        "weaknesses": """- **Tertiary fallback**: Only used when others fail
- **o-series limitations**: No temperature control, specific token handling
- **Context window**: Smaller than Claude (check current limits)""",
        "best_use_cases": """- Last-resort fallback when GLM and Claude unavailable
- Multimodal tasks requiring image understanding
- Integration with other OpenAI services
- Tasks requiring latest training data
- Structured data extraction with function calling""",
        "external_documentation_url": "https://platform.openai.com/docs",
        "version": "tiered",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_LLM
    },
    
    # OpenAI DALL-E 3
    "openai_dalle": {
        "condition": lambda: _is_real_key(settings.openai_api_key),
        "name": "OpenAI DALL-E 3",
        "slug": "openai-dall-e-3",
        "category": ToolCategory.API,
        "description": "AI image generation from OpenAI. Create high-quality images from text descriptions for content, marketing, product visualization, and creative campaigns.",
        "tags": ["image-generation", "ai", "creative", "visual"],
        "usage_instructions": """# OpenAI DALL-E 3 Usage

## Authentication
Uses the same OPENAI_API_KEY as GPT models.

## Basic Usage
```python
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

response = client.images.generate(
    model="dall-e-3",
    prompt="A professional book cover for 'AI Business Automation' with lightning bolts and circuit patterns, dark theme, high quality",
    size="1024x1024",
    quality="standard",
    n=1
)

image_url = response.data[0].url
# Download and save image
```

## Image Sizes
- 1024x1024 (square)
- 1792x1024 (landscape)
- 1024x1792 (portrait)

## Quality Options
- standard: Faster, lower cost
- hd: Higher detail, higher cost

## Best Practices
- Be specific and detailed in prompts
- Specify style, mood, colors, composition
- Use for final production, not iterations
- Budget ~$0.04-0.08 per image""",
        "example_code": """import os
from openai import OpenAI
import requests
from pathlib import Path

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Generate product image for affiliate marketing
response = client.images.generate(
    model="dall-e-3",
    prompt=\"\"\"Product photography style: A sleek smart home device on a minimalist desk, 
    soft morning lighting, professional product shot, high quality, clean background, 
    modern aesthetic, 8k resolution\"\"\",
    size="1024x1024",
    quality="hd",
    n=1
)

# Download image
image_url = response.data[0].url
image_data = requests.get(image_url).content

output_path = Path("generated_product.png")
output_path.write_bytes(image_data)
print(f"Image saved to {output_path}")""",
        "required_environment_variables": {
            "OPENAI_API_KEY": "API key for OpenAI"
        },
        "integration_complexity": "low",
        "cost_model": "per_use",
        "cost_details": {
            "type": "per_image",
            "standard_1024x1024": 0.040,
            "standard_1024x1792": 0.080,
            "hd_1024x1024": 0.080,
            "hd_1024x1792": 0.120,
            "currency": "USD"
        },
        "strengths": """- **High quality**: Best-in-class image generation
- **Text integration**: Can include text in images (with limitations)
- **Style understanding**: Responds well to style descriptions
- **Consistency**: Reliable results for similar prompts
- **Commercial use**: Images can be used commercially""",
        "weaknesses": """- **Cost**: $0.04-0.12 per image adds up quickly
- **No iterations**: Each generation costs money
- **Rate limits**: Limited generations per minute
- **Cannot edit**: No image editing, must regenerate
- **Content policy**: Some subjects restricted""",
        "best_use_cases": """- Blog post featured images
- Product mockups and visualization
- Book covers and ebook graphics
- Social media visual content
- Marketing materials and ads
- Thumbnail generation for videos
- Coloring book illustrations
- NFT artwork generation
- Print-on-demand designs""",
        "external_documentation_url": "https://platform.openai.com/docs/guides/images",
        "version": "3",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_DALLE
    },
    
    # Serper Web Search
    "serper_search": {
        "condition": lambda: _is_real_key(settings.serper_api_key) or bool(settings.use_serper_clone),
        "name": "Serper Web Search",
        "slug": "serper-web-search",
        "category": ToolCategory.DATA_SOURCE,
        "description": "Real-time web search API powered by Google. Essential for agents to research opportunities, validate ideas, analyze competition, and gather current market data. Supports both official Serper API and self-hosted Serper Clone.",
        "tags": ["search", "research", "data", "required", "web"],
        "usage_instructions": """# Serper Web Search Usage

## Overview
This tool supports two backends:
1. **Serper (official)**: https://serper.dev - Paid service ($0.001/search)
2. **Serper Clone**: https://github.com/paulscode/searxng-serper-bridge - Free, self-hosted

The system automatically uses the configured backend (see USE_SERPER_CLONE in .env).

## Authentication
Set the SERPER_API_KEY environment variable (required for both backends).

## Basic Usage
The tool executor handles URL routing automatically:
```python
# Via the tool executor (recommended)
from app.services.tool_execution_service import ToolExecutor

executor = ToolExecutor()
result = await executor._execute_serper_search(
    tool=None,
    params={"query": "your search query", "num": 10}
)

# Result includes cost tracking (0 for Serper Clone, 1 credit for Serper)
print(result.output)  # Search results
print(result.cost_units)  # 0 if using Serper Clone
```

## Search Types
- Organic search results
- News search
- Images search
- Shopping results (Serper only)
- Knowledge graph data

## Best Practices
- Use specific, targeted queries
- Limit results to what you need (cost per search with official Serper)
- Cache results when possible
- Combine with LLM to synthesize findings
- Consider Serper Clone for unlimited free searches""",
        "example_code": """# Example: Research a business opportunity
from app.services.tool_execution_service import ToolExecutor

async def research_opportunity(topic: str):
    \"\"\"Research a business opportunity using web search.\"\"\"
    executor = ToolExecutor()
    try:
        result = await executor._execute_serper_search(
            tool=None,
            params={
                "query": f"{topic} business opportunity 2026 profit",
                "num": 10
            }
        )
        
        if result.success:
            findings = {
                "organic_results": result.output.get("organic_results", []),
                "knowledge_graph": result.output.get("knowledge_graph"),
                "related_searches": result.output.get("related_searches", []),
                "cost_units": result.cost_units,  # 0 for Serper Clone
                "provider": result.cost_details.get("provider")
            }
            return findings
        else:
            print(f"Search failed: {result.error}")
            return None
    finally:
        await executor.close()

# Research print-on-demand opportunity
# research = await research_opportunity("print on demand t-shirts")
# print(research)""",
        "required_environment_variables": {
            "SERPER_API_KEY": "API key for Serper or Serper Clone",
            "USE_SERPER_CLONE": "(Optional) Set to 'true' for self-hosted Serper Clone",
            "SERPER_CLONE_URL": "(Optional) URL to Serper Clone instance"
        },
        "integration_complexity": "low",
        "cost_model": "per_use",
        "cost_details": {
            "type": "per_search",
            "cost_per_search": 0.001,
            "cost_per_search_clone": 0,
            "free_tier_searches": 2500,
            "clone_unlimited": True,
            "currency": "USD",
            "note": "Serper Clone is free and unlimited"
        },
        "strengths": """- **Real-time data**: Access to latest search results
- **Cost-effective**: $0.001 per search OR free with Serper Clone
- **Fast**: Quick response times
- **Comprehensive**: Organic, news, images results
- **Self-hostable**: Use Serper Clone for free unlimited searches
- **Required**: Essential for opportunity research""",
        "weaknesses": """- **Rate limits**: 60 requests per minute on Serper free tier
- **Cost accumulation**: Can add up with heavy Serper use (use Clone for free)
- **No content scraping**: Returns links, not full content
- **Requires interpretation**: Raw results need LLM processing
- **Serper Clone SSL**: Uses self-signed certificate (handled automatically)""",
        "best_use_cases": """- **Required for Opportunity Scout Agent**
- Market research and trend analysis
- Competition analysis
- Keyword research for SEO campaigns
- Validating business ideas
- Finding affiliate products
- Research for content creation
- Price comparison and market sizing
- Finding tools and services
- News monitoring and trend detection""",
        "external_documentation_url": "https://serper.dev/docs",
        "version": "1.0",
        "priority": "critical",
        "input_schema": INPUT_SCHEMA_SERPER
    },
    
    # ElevenLabs Voice
    "elevenlabs_voice": {
        "condition": lambda: _is_real_key(settings.elevenlabs_api_key),
        "name": "ElevenLabs Voice Generation",
        "slug": "elevenlabs-voice",
        "category": ToolCategory.API,
        "description": "High-quality text-to-speech AI. Generate natural-sounding voiceovers for YouTube videos, podcasts, audiobooks, and other audio content.",
        "tags": ["voice", "tts", "audio", "content-creation"],
        "usage_instructions": """# ElevenLabs Voice Generation Usage

## Authentication
Set the ELEVENLABS_API_KEY environment variable.

## Basic Usage
```python
import os
from elevenlabs import generate, save, voices, Voice

# Generate speech
audio = generate(
    text="Welcome to our automated podcast about AI and business automation.",
    voice=Voice(
        voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel voice
        settings={
            "stability": 0.75,
            "similarity_boost": 0.75
        }
    ),
    model="eleven_multilingual_v2"
)

# Save to file
save(audio, "voiceover.mp3")
```

## Voice Selection
- Browse voices: `voices()` 
- Use pre-made voices or clone custom voice
- Adjust stability and clarity

## Best Practices
- Choose voice that matches content tone
- Adjust stability (0.0-1.0) for consistency
- Use appropriate model (multilingual v2 recommended)
- Split long text into smaller chunks
- Monitor character usage""",
        "example_code": """import os
from elevenlabs import generate, save, Voice

def create_video_voiceover(script: str, output_file: str = "voiceover.mp3"):
    \"\"\"Generate voiceover for video content.\"\"\"
    
    # Generate with professional voice
    audio = generate(
        text=script,
        voice=Voice(
            voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel - professional female
            settings={
                "stability": 0.71,
                "similarity_boost": 0.5,
                "style": 0.0,
                "use_speaker_boost": True
            }
        ),
        model="eleven_multilingual_v2"
    )
    
    # Save audio file
    save(audio, output_file)
    return output_file

# Create voiceover for YouTube Short
script = \"\"\"
Discover the top 3 AI tools that will transform your business in 2026.
Number 1: Automated content creation with GPT-5.
Number 2: Voice synthesis for video production.
Number 3: Image generation for social media.
Start your AI journey today!
\"\"\"

output = create_video_voiceover(script)
print(f"Voiceover saved to {output}")""",
        "required_environment_variables": {
            "ELEVENLABS_API_KEY": "API key for ElevenLabs"
        },
        "integration_complexity": "low",
        "cost_model": "subscription",
        "cost_details": {
            "type": "per_character",
            "free_tier_chars_per_month": 10000,
            "starter_tier": {
                "monthly_cost": 5,
                "characters_per_month": 30000,
                "currency": "USD"
            },
            "creator_tier": {
                "monthly_cost": 22,
                "characters_per_month": 100000,
                "currency": "USD"
            }
        },
        "strengths": """- **Natural quality**: Very realistic voices
- **Voice variety**: Many pre-made voices
- **Voice cloning**: Can clone custom voices
- **Multiple languages**: Multilingual support
- **Fine control**: Adjust stability, clarity, style
- **Fast generation**: Quick turnaround""",
        "weaknesses": """- **Cost**: Character-based pricing adds up
- **Free tier limited**: Only 10k chars/month free
- **No free alternatives**: Quality alternatives expensive
- **Usage tracking needed**: Easy to exceed limits""",
        "best_use_cases": """- YouTube video voiceovers
- Podcast content generation
- Audiobook creation
- Educational content narration
- Product demo videos
- Social media video content
- Explainer videos
- Advertisement voiceovers
- IVR and automated phone systems""",
        "external_documentation_url": "https://elevenlabs.io/docs",
        "version": "2.0",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_ELEVENLABS
    },
    
    # Suno Music Generation
    "suno_music": {
        "condition": lambda: bool(settings.use_suno),
        "name": "Suno AI Music Generation",
        "slug": "suno-ai-music",
        "category": ToolCategory.COMMUNICATION,  # Manual workflow
        "description": "AI music generation service (MANUAL WORKFLOW). Agent provides specifications (title, style, lyrics, settings), human generates music via Suno.ai web interface, then provides file back to agent.",
        "tags": ["music", "audio", "creative", "manual-workflow", "human-in-loop"],
        "usage_instructions": """# Suno AI Music Generation Usage (MANUAL WORKFLOW)

⚠️ **IMPORTANT**: Suno.ai has NO API. This is a manual workflow requiring human interaction.

## Workflow Process

### 1. Agent Prepares Specifications
Agent generates structured JSON with:
```json
{
  "title": "Motivational Startup Anthem",
  "style": "upbeat, electronic, inspirational, 140 BPM",
  "lyrics": "[Verse 1]\\nRise up, take the leap...",
  "advanced_options": {
    "vocal_gender": "male",
    "lyrics_mode": "manual",
    "weirdness": 20,
    "style_influence": 75
  }
}
```

### 2. Human Receives Request
- Agent posts message in Discussion with JSON specifications
- Human receives notification
- Human reviews requirements

### 3. Human Uses Suno.ai Web Interface
1. Go to https://suno.ai
2. Log in to account
3. Click "Create"
4. Paste Title into Title field
5. Paste Style into Style field
6. Paste Lyrics into Lyrics field (if using manual mode)
7. Click "Advanced Options" and set:
   - Vocal Gender: Male/Female
   - Lyrics Mode: Manual/Auto
   - Weirdness: 0%-100%
   - Style Influence: 0%-100%
8. Click "Generate"
9. Wait 1-2 minutes for generation
10. Listen to results, regenerate if needed
11. Download best version (MP3)

### 4. Human Provides File to Agent
- Upload MP3 file to Discussion (requires file attachment feature)
- Post message: "Music generated and uploaded: [filename.mp3]"
- Agent can now reference file for campaign use

## Best Practices
- Generate 2-3 variations and pick best
- Be specific in style descriptions
- Test different weirdness/influence settings
- Keep lyrics under 3000 characters
- Budget ~5-10 minutes per song

## Limitations
- No automation possible (no API)
- Requires active Suno.ai subscription
- Generation not instantaneous
- Quality varies, may need iterations""",
        "example_code": """# Agent code to request music generation

async def request_music_generation(
    title: str,
    style: str,
    lyrics: Optional[str] = None,
    vocal_gender: str = "male",
    lyrics_mode: str = "auto",
    weirdness: int = 50,
    style_influence: int = 70
):
    \"\"\"
    Request human to generate music via Suno.ai.
    
    Returns message_id for tracking response.
    \"\"\"
    
    # Prepare specifications
    specs = {
        "tool": "Suno AI Music Generation",
        "title": title,
        "style": style,
        "lyrics": lyrics or "[Auto-generated]",
        "advanced_options": {
            "vocal_gender": vocal_gender,
            "lyrics_mode": lyrics_mode,
            "weirdness": weirdness,
            "style_influence": style_influence
        },
        "instructions": "Please generate music with these specs and upload the MP3 file."
    }
    
    # Post to conversation/discussion
    message = await post_to_discussion(
        content=f\"\"\"🎵 **Music Generation Request**

Please generate music using Suno.ai with these specifications:

```json
{json.dumps(specs, indent=2)}
```

Once generated, please upload the MP3 file to this discussion.
\"\"\",
        conversation_id=conversation_id,
        sender_type="agent"
    )
    
    return message.id

# Example usage
message_id = await request_music_generation(
    title="AI Startup Theme Song",
    style="electronic, energetic, modern, 130 BPM, synthesizers",
    lyrics=\"\"\"[Verse 1]
Code and dreams unite as one
Building futures, just begun
Innovation lights the way
AI powers every day

[Chorus]
We're the makers, we're the dreamers
Tech and vision, true believers
Rise up high, reach for the sky
Startup spirit never dies\"\"\",
    vocal_gender="male",
    lyrics_mode="manual",
    weirdness=30,
    style_influence=80
)

# Monitor for human response with file attachment
await wait_for_file_upload(message_id)""",
        "required_environment_variables": {
            "USE_SUNO": "Set to 'true' to enable Suno tool in catalog"
        },
        "integration_complexity": "high",
        "cost_model": "subscription",
        "cost_details": {
            "type": "subscription",
            "free_tier": {
                "songs_per_day": 5,
                "monthly_cost": 0
            },
            "basic_tier": {
                "monthly_cost": 10,
                "songs_per_month": 500,
                "currency": "USD"
            },
            "pro_tier": {
                "monthly_cost": 30,
                "songs_per_month": 2000,
                "currency": "USD"
            }
        },
        "strengths": """- **High quality**: Professional-sounding music
- **Lyric generation**: Can auto-generate lyrics
- **Style variety**: Many genres and styles
- **Fast generation**: 1-2 minutes per song
- **Commercial use**: Can be used commercially
- **Customization**: Advanced options for control""",
        "weaknesses": """- **NO API**: Requires manual human workflow
- **Not automated**: Cannot be fully automated
- **Human time**: Requires 5-10 minutes per song
- **Quality variance**: May need multiple attempts
- **Subscription required**: Costs $10-30/month
- **File handling**: Requires file attachment feature""",
        "best_use_cases": """- Background music for YouTube videos
- Podcast intro/outro music
- Social media video soundtracks
- Advertisement background music
- Game background music
- Custom jingles for brands
- Meditation and ambient music
- Workout and motivation tracks
- Educational content background music

**REQUIRES**: File attachment support in Discussion interface""",
        "external_documentation_url": "https://suno.ai/",
        "version": "3.0",
        "priority": "low",
        "input_schema": INPUT_SCHEMA_SUNO
    },
    
    # ACE-Step Local Music Generation
    "acestep_music": {
        "condition": lambda: bool(settings.use_acestep),
        "name": "ACE-Step Music Generation",
        "slug": "acestep-music-generation",
        "category": ToolCategory.API,
        "description": "FREE local AI music generation using ACE-Step 1.5. Produces commercial-grade songs with lyrics (50+ languages), runs entirely on your GPU - UNLIMITED usage, NO API costs. Supports various styles/genres with fine-tuned control over generation parameters.",
        "tags": ["music", "audio", "ai", "local", "free", "gpu", "lyrics", "generation"],
        "usage_instructions": """# ACE-Step Music Generation

## Overview
ACE-Step 1.5 is a cutting-edge open-source music generation model that runs locally on your GPU.
Unlike cloud services, it's **completely FREE** with **unlimited generations**.

## Key Features
- 🎵 **Commercial-quality**: State-of-the-art audio quality
- 🌍 **50+ languages**: Lyrics support in most major languages  
- 🎤 **Vocals or Instrumental**: Generate with or without vocals
- ⚡ **Turbo mode**: Fast generation in just 8 steps
- 🎛️ **Fine control**: Adjust temperature, CFG, steps for desired output
- 💰 **FREE & UNLIMITED**: No API costs, no limits

## Quality Tiers (based on GPU VRAM)
| Tier | VRAM | LM Model | Quality |
|------|------|----------|---------|
| turbo | ≥16GB | 1.7B | Best quality + fastest |
| quality | 12-16GB | 1.7B | High quality |
| standard | 6-12GB | 0.6B | Good quality |
| lite | 4-6GB | None | Basic quality |
| cpu | Any | None | Very slow |

## Parameters
- `lyrics` (str): Song lyrics - use [Verse], [Chorus], [Bridge] tags
- `style` (str): Music style description (e.g., "upbeat pop with synthesizers")
- `duration` (float): Duration in seconds (10-240, default 60)
- `steps` (int): Inference steps (turbo: max 8, base: max 300)
- `instrumental` (bool): Generate without vocals
- `temperature` (float): Sampling temperature (0.7-1.5, default 0.95)
- `guidance_scale` (float): CFG scale (1.0-10.0, default 3.5)
- `batch_size` (int): Generate multiple variations (1-4)
- `seed` (int): For reproducible results

## Example Usage
```python
result = await execute_tool(
    "acestep-music-generation",
    {
        "style": "energetic pop rock, electric guitar, drums, 120 BPM",
        "lyrics": \"\"\"[Verse 1]
Breaking free from all the chains
Running wild through summer rains
Nothing's gonna hold us back
We're on a brand new track

[Chorus]  
Rise up, reach for the sky
Spread your wings and learn to fly
This is our moment, our time to shine
Everything's gonna be just fine\"\"\",
        "duration": 90,
        "instrumental": False,
        "temperature": 0.95,
        "steps": 8  # For turbo model
    }
)

# Result contains audio_urls with downloadable MP3 files
audio_url = result["audio_urls"][0]
```

## Best Practices
1. **Style prompts**: Be descriptive with genre, instruments, tempo, mood
2. **Lyrics structure**: Use [Verse], [Chorus], [Bridge] tags
3. **Duration**: Start with 60s, adjust based on song structure
4. **Temperature**: Lower (0.7-0.9) for consistency, higher (1.0-1.5) for variety
5. **Batch size**: Generate 2-3 variations to pick the best""",
        "example_code": """# Generate a pop song with lyrics
async def generate_pop_song():
    result = await tool_service.execute(
        tool_slug="acestep-music-generation",
        params={
            "style": "upbeat pop, catchy melody, synthesizers, 128 BPM",
            "lyrics": \"\"\"[Verse 1]
Monday morning, coffee in my hand
Ready to take on the world again
Got my playlist and my favorite chair
Nothing can stop me, I'm walking on air

[Chorus]
This is my day, gonna make it count
Every second, every breath, every ounce
Living life like there's no tomorrow
No more worries, no more sorrow\"\"\",
            "duration": 60,
            "temperature": 0.95,
            "guidance_scale": 3.5
        }
    )
    return result

# Generate instrumental background music
async def generate_background_music():
    result = await tool_service.execute(
        tool_slug="acestep-music-generation",
        params={
            "style": "ambient electronic, calm, atmospheric, 80 BPM",
            "instrumental": True,
            "duration": 120,
            "temperature": 0.85
        }
    )
    return result""",
        "required_environment_variables": {
            "USE_ACESTEP": "Set to 'true' to enable ACE-Step",
            "ACESTEP_API_URL": "ACE-Step server URL (default: http://host.docker.internal:8001)",
            "ACESTEP_MODEL": "Default DiT model: turbo (fast, 8 steps) or base (more steps, higher diversity). Users can also select per-request via the model field."
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_generation": 0,
            "notes": "Completely free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, generate as much as you want
- **High quality**: Commercial-grade audio output
- **Full automation**: No manual steps required, unlike Suno
- **Privacy**: All processing happens locally
- **50+ languages**: Lyrics in most major languages
- **Fine control**: Many parameters to tune output
- **Fast**: Turbo mode generates in seconds
- **Commercial use**: Open source, commercial-friendly""",
        "weaknesses": """- **GPU required**: Needs NVIDIA GPU with 4GB+ VRAM for good results
- **Initial download**: ~5GB model download on first use
- **CPU mode slow**: CPU-only mode is very slow
- **Quality varies**: May need multiple attempts for perfect results
- **Learning curve**: Many parameters to understand""",
        "best_use_cases": """- YouTube video background music
- Podcast intro/outro jingles
- Social media content soundtracks
- Advertisement music
- Game background music
- Custom brand jingles
- Meditation and ambient tracks
- Workout and motivation music
- Educational content backgrounds
- Prototype music for client demos

**IDEAL FOR**: High-volume music generation where cost is a concern""",
        "external_documentation_url": "https://github.com/ace-step/ACE-Step-1.5",
        "version": "1.5",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_ACESTEP,
        "timeout_seconds": 1800
    },
    
    # Qwen3-TTS Local Voice Generation  
    "qwen3_tts_voice": {
        "condition": lambda: bool(settings.use_qwen3_tts),
        "name": "Qwen3-TTS Voice Generation",
        "slug": "qwen3-tts-voice",
        "category": ToolCategory.API,
        "description": "FREE local AI voice generation using Qwen3-TTS. Supports voice cloning from audio samples, 9 built-in speakers with instruction control, and voice design from text descriptions. Runs entirely on your GPU - UNLIMITED usage, NO API costs. Multi-lingual: Chinese, English, Japanese, Korean.",
        "tags": ["tts", "voice", "audio", "ai", "local", "free", "gpu", "cloning", "multilingual"],
        "usage_instructions": """# Qwen3-TTS Voice Generation

## Overview
Qwen3-TTS is a state-of-the-art open-source text-to-speech model from Alibaba that runs locally.
Unlike ElevenLabs, it's **completely FREE** with **unlimited generations**.

## Key Features
- 🎤 **Voice Cloning**: Clone any voice from a short audio sample
- 🗣️ **9 Built-in Voices**: Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee
- 🎨 **Voice Design**: Create voices from text descriptions ("A warm female British accent")
- 🌍 **Multi-lingual**: Chinese, English, Japanese, Korean
- 💰 **FREE & UNLIMITED**: No API costs, no limits
- 🎛️ **Instruction Control**: "Speak happily", "Whisper", "With excitement"

## Models
| Tier | Model | VRAM | Capabilities |
|------|-------|------|-------------|
| full | 1.7B  | ~8GB | All modes: clone, custom voice, voice design |
| lite | 0.6B  | ~4GB | Voice clone only |

## Generation Modes

### 1. custom_voice (1.7B only)
Use one of 9 built-in speakers with optional style instructions.
```python
result = await execute_tool("qwen3-tts-voice", {
    "text": "Hello, welcome to our podcast!",
    "mode": "custom_voice",
    "voice": "Ryan",
    "instruct": "Speak with enthusiasm and warmth"
})
```

### 2. voice_clone
Clone a voice from an uploaded audio sample.
```python
# First upload a voice sample
# Then generate with the cloned voice
result = await execute_tool("qwen3-tts-voice", {
    "text": "This uses the cloned voice.",
    "mode": "voice_clone",
    "reference_audio": "my_voice_sample.wav",
    "reference_text": "Transcript of the reference audio"
})
```

### 3. voice_design (1.7B only)
Create a voice from a natural language description.
```python
result = await execute_tool("qwen3-tts-voice", {
    "text": "Good evening, and welcome to the show.",
    "mode": "voice_design",
    "voice_description": "A warm female voice with a slight British accent, speaking calmly"
})
```

### 4. voice_design_clone (1.7B only)
Design a voice, then use it as a clone reference for stability.
```python
result = await execute_tool("qwen3-tts-voice", {
    "text": "This is a longer piece that benefits from clone stability.",
    "mode": "voice_design_clone",
    "voice_description": "Deep male radio announcer voice"
})
```

## Parameters
- `text` (str, required): Text to convert to speech
- `mode` (str): custom_voice, voice_clone, voice_design, voice_design_clone
- `voice` (str): Built-in voice name (custom_voice mode)
- `instruct` (str): Style instruction, e.g., "Speak happily" (custom_voice mode)
- `reference_audio` (str): Uploaded voice filename (voice_clone mode)
- `reference_text` (str): Transcript of reference audio (voice_clone mode)
- `voice_description` (str): Natural language voice description (voice_design modes)

## Built-in Voices
| Name | Language | Description |
|------|----------|-------------|
| Vivian | Chinese | Female |
| Serena | Chinese | Female |
| Uncle_Fu | Chinese | Male |
| Dylan | Chinese | Male |
| Eric | Chinese | Male |
| Ryan | English | Male |
| Aiden | English | Male |
| Ono_Anna | Japanese | Female |
| Sohee | Korean | Female |

## Best Practices
1. **Voice cloning**: Use 5-15 second clean audio samples (minimal background noise)
2. **Reference text**: Providing a transcript of the reference audio improves clone quality
3. **Instructions**: Keep style instructions concise: "Speak warmly", "Whisper", "With excitement"
4. **Long text**: For stability on long paragraphs, use voice_design_clone instead of voice_design
5. **Model choice**: Use full (1.7B) for all features, lite (0.6B) if VRAM is limited""",
        "example_code": """# Generate speech with a built-in voice
async def generate_greeting():
    result = await tool_service.execute(
        tool_slug="qwen3-tts-voice",
        params={
            "text": "Welcome to Money Agents! Let's explore some exciting opportunities together.",
            "mode": "custom_voice",
            "voice": "Ryan",
            "instruct": "Speak with enthusiasm"
        }
    )
    return result

# Clone a voice from an uploaded sample
async def clone_and_generate():
    result = await tool_service.execute(
        tool_slug="qwen3-tts-voice",
        params={
            "text": "This message uses a cloned voice from your audio sample.",
            "mode": "voice_clone",
            "reference_audio": "brand_voice.wav",
            "reference_text": "The original words spoken in the sample"
        }
    )
    return result

# Design a custom voice from description
async def design_voice():
    result = await tool_service.execute(
        tool_slug="qwen3-tts-voice",
        params={
            "text": "Good evening, and welcome to tonight's special presentation.",
            "mode": "voice_design",
            "voice_description": "A warm, authoritative female voice with a calm, professional tone"
        }
    )
    return result""",
        "required_environment_variables": {
            "USE_QWEN3_TTS": "Set to 'true' to enable Qwen3-TTS",
            "QWEN3_TTS_API_URL": "Qwen3-TTS server URL (default: http://host.docker.internal:8002)",
            "QWEN3_TTS_TIER": "Model tier: auto, full (1.7B), or lite (0.6B)"
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_generation": 0,
            "notes": "Completely free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, generate as much as you want
- **Voice Cloning**: Clone any voice from a short audio sample
- **9 Built-in Voices**: Ready-to-use speakers in 4 languages
- **Voice Design**: Create voices from natural language descriptions
- **Instruction Control**: Fine-tune speech style with text instructions
- **Privacy**: All processing happens locally
- **Multi-lingual**: Chinese, English, Japanese, Korean
- **Commercial use**: Apache-2.0 license""",
        "weaknesses": """- **GPU required**: Needs NVIDIA GPU with 4GB+ VRAM
- **Initial download**: Model download on first use (~3-6GB depending on tier)
- **English voices limited**: Only 2 English built-in voices (Ryan, Aiden)
- **0.6B limitations**: Lite model only supports voice cloning
- **Memory sharing**: Shares GPU memory with ACE-Step and other models""",
        "best_use_cases": """- YouTube video narration and voiceovers
- Podcast introduction and outro
- Brand voice consistency (clone your brand voice)
- Multi-lingual content creation
- Audiobook generation
- Advertisement voiceovers
- Customer service voice responses
- Educational content narration
- Social media video narration
- Prototype voice content for client demos

**IDEAL FOR**: High-volume voice generation where cost and privacy matter""",
        "external_documentation_url": "https://github.com/QwenLM/Qwen3-TTS",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_QWEN3_TTS,
        "timeout_seconds": 300
    },
    
    # Z-Image Local Image Generation
    "zimage_generation": {
        "condition": lambda: bool(settings.use_zimage),
        "name": "Z-Image Generation",
        "slug": "zimage-generation",
        "category": ToolCategory.API,
        "description": "FREE local AI image generation using Z-Image Turbo (6B DiT model from Tongyi). Generates high-quality 1024x1024 images in ~3-8 seconds on consumer GPUs. 8-step turbo inference, no classifier-free guidance needed. Runs entirely on your GPU - UNLIMITED usage, NO API costs.",
        "tags": ["image", "generation", "ai", "local", "free", "gpu", "diffusion", "turbo"],
        "usage_instructions": """# Z-Image Generation

## Overview
Z-Image is a state-of-the-art 6B parameter Diffusion Transformer (DiT) from Tongyi/Alibaba
that generates high-quality images from text prompts. The Turbo variant uses 8-step distilled
inference for fast generation. Unlike DALL-E or Midjourney, it's **completely FREE** with
**unlimited generations**.

## Key Features
- 🎨 **High Quality**: 6B parameter DiT model produces stunning images
- ⚡ **Turbo Speed**: 8-step inference, ~3-8 seconds on RTX 3090/4090
- 💰 **FREE & UNLIMITED**: No API costs, no limits
- 🔒 **Privacy**: All processing happens locally on your GPU
- 📐 **Flexible Resolution**: Supports various aspect ratios (divisible by 16)
- 🎲 **Reproducible**: Seed control for consistent outputs
- 📦 **Auto-download**: Model weights auto-downloaded from HuggingFace (~12GB)

## Model Requirements
| Variant | VRAM | Speed (1024x1024) | Steps |
|---------|------|-------------------|-------|
| Turbo   | ~16-18GB | 3-8s on RTX 3090 | 8 |

## Parameters
- `prompt` (str, required): Text description of the image to generate
- `negative_prompt` (str, optional): What to avoid in the image
- `width` (int, default 1024): Image width (must be divisible by 16)
- `height` (int, default 1024): Image height (must be divisible by 16)
- `num_inference_steps` (int, default 8): Denoising steps
- `guidance_scale` (float, default 0.0): Classifier-free guidance scale (0.0 for turbo)
- `seed` (int, optional): Random seed for reproducibility (-1 = random)
- `num_images_per_prompt` (int, default 1): Number of images to generate (1-4)

## Common Resolutions
| Aspect Ratio | Resolution | Use Case |
|--------------|-----------|----------|
| 1:1 | 1024x1024 | Social media, avatars |
| 16:9 | 1024x576 | YouTube thumbnails, banners |
| 9:16 | 576x1024 | Phone wallpapers, stories |
| 3:2 | 1024x688 | Landscape photos |
| 2:3 | 688x1024 | Portrait photos |

## Best Practices
1. **Descriptive prompts**: Be detailed about subject, style, lighting, composition
2. **Negative prompts**: Use to exclude unwanted elements (e.g., "blurry, low quality, distorted")
3. **Seeds**: Use the same seed to generate variations by tweaking prompts
4. **Resolution**: Stick to standard aspect ratios for best results
5. **Memory sharing**: Shares GPU with ACE-Step and Qwen3-TTS - idle models auto-unload""",
        "example_code": """# Generate a high-quality image
async def generate_product_image():
    result = await tool_service.execute(
        tool_slug="zimage-generation",
        params={
            "prompt": "Professional product photo of a sleek wireless headphone on a marble surface, soft studio lighting, minimalist background, 8k quality",
            "negative_prompt": "blurry, low quality, distorted, watermark",
            "width": 1024,
            "height": 1024,
            "seed": 42
        }
    )
    return result

# Generate a YouTube thumbnail
async def generate_thumbnail():
    result = await tool_service.execute(
        tool_slug="zimage-generation",
        params={
            "prompt": "Eye-catching YouTube thumbnail showing excited person discovering treasure chest full of gold coins, vibrant colors, dramatic lighting",
            "width": 1024,
            "height": 576,
            "num_images_per_prompt": 2
        }
    )
    return result

# Generate a social media post image
async def generate_social_image():
    result = await tool_service.execute(
        tool_slug="zimage-generation",
        params={
            "prompt": "Modern flat illustration of a rocket launching from a laptop screen, startup concept, bold colors, clean design",
            "negative_prompt": "photorealistic, blurry, text, watermark",
            "width": 1024,
            "height": 1024
        }
    )
    return result""",
        "required_environment_variables": {
            "USE_ZIMAGE": "Set to 'true' to enable Z-Image generation",
            "ZIMAGE_API_URL": "Z-Image server URL (default: http://host.docker.internal:8003)",
            "ZIMAGE_MODEL": "Model variant: turbo (default)"
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_generation": 0,
            "notes": "Completely free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, generate as many images as you want
- **High Quality**: 6B parameter DiT model produces stunning results
- **Turbo Speed**: 8-step inference, much faster than standard diffusion
- **Privacy**: All processing happens locally on your GPU
- **Flexible Resolution**: Any aspect ratio (dimensions divisible by 16)
- **Reproducible**: Seed-based generation for consistent outputs
- **Auto-download**: Model auto-downloads from HuggingFace on first use
- **Commercial use**: Apache-2.0 license""",
        "weaknesses": """- **High VRAM**: Requires ~16-18GB VRAM (RTX 3090/4090 or better)
- **Initial download**: ~12GB model download on first use
- **Single variant**: Only Turbo model available currently
- **No inpainting**: Text-to-image only, no image editing capabilities
- **Memory sharing**: Shares GPU memory with ACE-Step and Qwen3-TTS models""",
        "best_use_cases": """- YouTube thumbnails and channel art
- Social media post images and ad creatives
- Product mockups and concept art
- Blog and website hero images
- Marketing campaign visuals
- Pitch deck illustrations
- Brand asset prototyping
- Moodboard and style exploration

**IDEAL FOR**: High-volume image generation where cost, speed, and privacy matter""",
        "external_documentation_url": "https://github.com/Tongyi-MAI/Z-Image",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_ZIMAGE,
        "timeout_seconds": 120
    },
    
    # SeedVR2 Local Image & Video Upscaler
    "seedvr2_upscaler": {
        "condition": lambda: bool(settings.use_seedvr2),
        "name": "SeedVR2 Upscaler",
        "slug": "seedvr2-upscaler",
        "category": ToolCategory.API,
        "description": "FREE local AI image & video upscaler using SeedVR2 (ByteDance 3B/7B DiT). One-step diffusion-based super-resolution that adds realistic detail while upscaling. Supports both images and videos with temporal consistency. Runs entirely on your GPU - UNLIMITED usage, NO API costs.",
        "tags": ["image", "video", "upscale", "super-resolution", "ai", "local", "free", "gpu", "diffusion"],
        "usage_instructions": """# SeedVR2 Image & Video Upscaler

## Overview
SeedVR2 is a one-step diffusion-based super-resolution model from ByteDance.
It upscales images and videos to higher resolutions while adding AI-generated detail.
Uses a 3B or 7B parameter DiT (Diffusion Transformer) architecture.

## Image Upscaling

### Parameters
- **image_path** (str, optional): Local file path to the image to upscale
- **image_url** (str, optional): URL to download the image from
- **resolution** (int): Target short-side resolution (default: 1080)
- **max_resolution** (int): Max resolution cap, 0 = no limit (default: 0)
- **color_correction** (str): Color correction method (default: "lab")
  - Options: lab, wavelet, wavelet_adaptive, hsv, adain, none
- **seed** (int, optional): Random seed for reproducibility

### Example: Upscale an image to 1080p
```python
result = upscale_image(
    image_path="/path/to/image.png",
    resolution=1080,
    color_correction="lab"
)
print(f"Upscaled: {result['output_url']}")
print(f"Resolution: {result['input_resolution']} → {result['output_resolution']}")
```

### Example: Upscale from URL to 4K
```python
result = upscale_image(
    image_url="https://example.com/low-res.jpg",
    resolution=2160,
    max_resolution=3840
)
```

## Video Upscaling

### Additional Parameters for Video
- **video_path** (str): Local file path to the video to upscale
- **batch_size** (int): Frames per batch, must follow 4n+1 formula: 5, 9, 13, 17... (default: 5)
- **temporal_overlap** (int): Overlap frames for smooth blending between batches (default: 2)

### Example: Upscale a video to 1080p
```python
result = upscale_video(
    video_path="/path/to/video.mp4",
    resolution=1080,
    batch_size=5,
    temporal_overlap=2
)
print(f"Upscaled: {result['output_url']}")
print(f"Frames: {result['total_frames']}")
```

## Resolution Guidelines
| Input | Target Resolution | Output |
|-------|------------------|--------|
| 360p  | 1080             | ~1080p |
| 480p  | 1080             | ~1080p |
| 720p  | 1080             | ~1080p |
| 720p  | 2160             | ~4K    |

## Pipeline Integration
- Generate image with Z-Image → upscale with SeedVR2 for 4K output
- Generate video with LTX-2 → upscale with SeedVR2 for 1080p output

## Important Notes
- First run downloads models from HuggingFace (~4GB for 3B FP8)
- Image upscaling takes ~5-15 seconds depending on resolution
- Video upscaling takes ~30s-5min depending on frame count and resolution
- Uses cooperative GPU memory — unloads when idle to free VRAM for other tools""",
        "example_functions": [
            {
                "name": "upscale_product_image",
                "description": "Upscale a product image to high resolution for marketing",
                "code": """async def upscale_product_image(image_path: str) -> dict:
    return await tool_execute("seedvr2-upscaler", {
        "image_path": image_path,
        "resolution": 2160,
        "color_correction": "lab"
    })"""
            },
            {
                "name": "upscale_ai_generated_image",
                "description": "Upscale an AI-generated image from Z-Image to 4K",
                "code": """async def upscale_ai_generated_image(image_url: str) -> dict:
    return await tool_execute("seedvr2-upscaler", {
        "image_url": image_url,
        "resolution": 2160,
        "max_resolution": 3840,
        "color_correction": "lab"
    })"""
            },
            {
                "name": "upscale_video_to_1080p",
                "description": "Upscale a video file to 1080p resolution",
                "code": """async def upscale_video_to_1080p(video_path: str) -> dict:
    return await tool_execute("seedvr2-upscaler", {
        "video_path": video_path,
        "resolution": 1080,
        "batch_size": 5,
        "temporal_overlap": 2
    })"""
            }
        ],
        "example_code": """# Upscale a product image to 4K
async def upscale_product_image():
    result = await tool_service.execute(
        tool_slug="seedvr2-upscaler",
        params={
            "image_path": "/path/to/product-photo.jpg",
            "resolution": 2160,
            "color_correction": "lab"
        }
    )
    return result

# Upscale an AI-generated image from Z-Image
async def upscale_ai_image():
    result = await tool_service.execute(
        tool_slug="seedvr2-upscaler",
        params={
            "image_url": "http://host.docker.internal:8003/output/zimage_001.png",
            "resolution": 2160,
            "max_resolution": 3840,
            "color_correction": "lab"
        }
    )
    return result

# Upscale a video to 1080p
async def upscale_video():
    result = await tool_service.execute(
        tool_slug="seedvr2-upscaler",
        params={
            "video_path": "/path/to/video.mp4",
            "resolution": 1080,
            "batch_size": 5,
            "temporal_overlap": 2
        }
    )
    return result""",
        "required_env_vars": ["USE_SEEDVR2", "SEEDVR2_API_URL"],
        "required_environment_variables": {
            "USE_SEEDVR2": "Set to 'true' to enable SeedVR2 upscaler",
            "SEEDVR2_API_URL": "SeedVR2 server URL (default: http://host.docker.internal:8004)",
            "SEEDVR2_MODEL": "DiT model filename (default: seedvr2_ema_3b_fp8_e4m3fn.safetensors)"
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_upscale": 0,
            "notes": "Free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, upscale as many images/videos as you want
- **Image & Video**: Handles both stills and video with temporal consistency
- **One-Step Diffusion**: Fast inference compared to multi-step upscalers
- **Multiple Models**: 3B and 7B variants, FP8 and FP16 precision options
- **Privacy**: All processing happens locally on your GPU
- **Color Correction**: Multiple methods (LAB, wavelet, HSV, AdaIN) preserve original colors
- **Pipeline Integration**: Chain with Z-Image or LTX-2 for generate→upscale workflows
- **Commercial use**: Apache-2.0 license""",
        "weaknesses": """- **GPU Required**: Needs 8-12GB+ VRAM depending on model variant
- **Initial download**: ~4GB model download on first use (3B FP8)
- **Video speed**: Video upscaling can take minutes for long clips
- **Memory sharing**: Shares GPU memory with other local AI services
- **No generation**: Upscaling only — cannot generate new content from text""",
        "best_use_cases": """- Upscaling AI-generated images (from Z-Image) to print/4K resolution
- Enhancing AI-generated videos (from LTX-2) to 1080p
- Upscaling low-resolution product photos for marketing campaigns
- Enhancing thumbnails and social media assets
- Batch upscaling campaign visual assets
- Video content enhancement for presentations and demos

**IDEAL FOR**: Post-processing AI-generated content and enhancing low-res assets""",
        "external_documentation_url": "https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler",
        "version": "2.5",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_SEEDVR2,
        "timeout_seconds": 1800
    },
    
    # Canary-STT — Local Speech-to-Text Transcription
    "canary_stt": {
        "condition": lambda: bool(settings.use_canary_stt),
        "name": "Canary-STT",
        "slug": "canary-stt",
        "category": ToolCategory.API,
        "description": "FREE local speech-to-text transcription using NVIDIA Canary-Qwen-2.5B. State-of-the-art English ASR with punctuation and capitalization, processing at 418x realtime. Runs entirely on your GPU - UNLIMITED usage, NO API costs.",
        "tags": ["audio", "speech", "transcription", "stt", "asr", "ai", "local", "free", "gpu", "nemo"],
        "usage_instructions": """# Canary-STT Speech-to-Text

## Overview
Canary-STT uses NVIDIA's Canary-Qwen-2.5B model for English speech-to-text transcription.
Built on the SALM (Speech-Augmented Language Model) architecture with FastConformer encoder
and Qwen3-1.7B decoder. Processes audio at 418x realtime speed.

## Parameters
- **audio_url** (str, optional): URL of audio file to transcribe
- **audio_path** (str, optional): Local file path to audio file
- **save_transcript** (bool): Save transcript to server (default: false)

One of audio_url or audio_path is required.

## Supported Formats
WAV, FLAC, MP3, OGG, M4A, WebM, MP4, WMA
Audio is automatically resampled to 16kHz mono if needed.

## Example: Transcribe from URL
```python
result = transcribe_audio(audio_url="https://example.com/meeting.wav")
print(result["text"])
print(f"Duration: {result['duration_seconds']}s")
print(f"Processing: {result['processing_time_seconds']}s")
```

## Example: Transcribe from file
```python
result = transcribe_audio(audio_path="/path/to/recording.mp3")
print(result["text"])
```

## Limitations
- **English only**: Trained on English speech data only
- **Max 40 seconds**: Audio clips longer than 40s must be split into segments
- **ASR mode only**: Transcription only, no conversational AI capabilities

## Pipeline Integration
- Record voice note → Canary-STT transcription → agent processes text
- Download podcast/video audio → transcribe → summarize with LLM
- Campaign manager records voice brief → agent extracts action items

## Important Notes
- First run downloads model from HuggingFace (~5GB)
- Transcription of 40s clip takes ~0.1s on RTX 3090
- Includes punctuation and capitalization in output
- Uses cooperative GPU memory — unloads when idle to free VRAM for other tools""",
        "example_functions": [
            {
                "name": "transcribe_audio_file",
                "description": "Transcribe a local audio file to text",
                "code": """async def transcribe_audio_file(audio_path: str) -> str:
    result = await tool_execute("canary-stt", {
        "audio_path": audio_path
    })
    return result["text"]"""
            },
            {
                "name": "transcribe_from_url",
                "description": "Transcribe audio from a URL",
                "code": """async def transcribe_from_url(url: str) -> str:
    result = await tool_execute("canary-stt", {
        "audio_url": url,
        "save_transcript": True
    })
    print(f"Transcribed {result['duration_seconds']}s in {result['processing_time_seconds']}s")
    return result["text"]"""
            },
        ],
        "example_code": """# Transcribe audio from a URL
async def transcribe_meeting():
    result = await tool_service.execute(
        tool_slug="canary-stt",
        params={
            "audio_url": "https://example.com/meeting-recording.wav",
            "save_transcript": True
        }
    )
    print(f"Text: {result['text']}")
    print(f"Duration: {result['duration_seconds']}s")
    return result

# Transcribe a local file
async def transcribe_local():
    result = await tool_service.execute(
        tool_slug="canary-stt",
        params={"audio_path": "/workspace/recording.mp3"}
    )
    return result["text"]""",
        "required_env_vars": ["USE_CANARY_STT"],
        "required_environment_variables": {
            "USE_CANARY_STT": "Set to 'true' to enable Canary-STT transcription",
            "CANARY_STT_API_URL": "Canary-STT server URL (default: http://host.docker.internal:8005)",
            "CANARY_STT_API_PORT": "Canary-STT server port (default: 8005)",
            "CANARY_STT_AUTO_START": "Auto-start server on demand (default: true)",
            "CANARY_STT_IDLE_TIMEOUT": "Idle timeout before unloading model (default: 300)",
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_transcription": 0,
            "notes": "Free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, transcribe as much audio as you want
- **State-of-the-art**: Top scores on HuggingFace OpenASR leaderboard
- **Blazing fast**: 418x realtime processing speed on GPU
- **Punctuation & capitalization**: Automatic PnC in transcription output
- **Multiple formats**: WAV, FLAC, MP3, OGG, M4A, WebM, MP4, WMA
- **Privacy**: All processing happens locally on your GPU
- **Commercial use**: CC-BY-4.0 license""",
        "weaknesses": """- **English only**: Not suited for multilingual transcription
- **40s max**: Audio must be split into segments of 40s or less
- **GPU Required**: Needs ~6-8GB VRAM for inference
- **Initial download**: ~5GB model download on first use
- **Memory sharing**: Shares GPU memory with other local AI services
- **No speaker diarization**: Does not identify different speakers""",
        "best_use_cases": """- Transcribing voice notes and audio messages from campaigns
- Converting meeting recordings to text for agent processing
- Extracting text from podcast or video clips for analysis
- Voice-to-text input for agent commands and instructions
- Transcribing customer call recordings for sentiment analysis

**IDEAL FOR**: Converting audio content to text for agent processing and analysis""",
        "external_documentation_url": "https://huggingface.co/nvidia/canary-qwen-2.5b",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_CANARY_STT,
        "timeout_seconds": 120
    },
    
    # AudioSR — Local Audio Super-Resolution
    "audiosr_enhance": {
        "condition": lambda: bool(settings.use_audiosr),
        "name": "AudioSR Audio Enhancement",
        "slug": "audiosr-enhance",
        "category": ToolCategory.API,
        "description": "FREE local audio super-resolution using AudioSR latent diffusion model. Upscales any audio (music, speech, environmental sounds) to 48kHz high-fidelity output. Works on all audio types and all input sampling rates. Runs entirely on your GPU - UNLIMITED usage, NO API costs.",
        "tags": ["audio", "enhancement", "super-resolution", "upscale", "ai", "local", "free", "gpu"],
        "usage_instructions": """# AudioSR Audio Super-Resolution

## Overview
AudioSR uses a latent diffusion model to perform versatile audio super-resolution.
It can upscale any type of audio (music, speech, environmental sounds) from any input
sampling rate to 48kHz high-fidelity output. The model works by reconstructing
high-frequency details that are missing in low-quality audio.

## Parameters
- **audio_url** (str, optional): URL of audio file to enhance
- **audio_path** (str, optional): Local file path to audio file
- **ddim_steps** (int): Diffusion denoising steps, 50 default (higher = better quality)
- **guidance_scale** (float): Classifier-free guidance scale, 3.5 default
- **seed** (int, optional): Random seed for reproducible results

One of audio_url or audio_path is required.

## Supported Input Formats
WAV, FLAC, MP3, OGG, M4A — any sampling rate

## Output
Always 48kHz WAV — high-fidelity enhanced audio

## Example: Enhance audio from URL
```python
result = enhance_audio(audio_url="https://example.com/low-quality.mp3")
print(result["output_file"])
print(f"Enhanced to {result['output_sample_rate']}Hz")
```

## Example: Enhance local file
```python
result = enhance_audio(audio_path="/path/to/recording.wav", ddim_steps=100)
print(result["output_file"])
```

## Model Variants
- **basic**: Works on all audio types (music, speech, environmental)
- **speech**: Optimized specifically for speech enhancement

## Pipeline Integration
- Record low-quality audio → AudioSR enhancement → high-fidelity output
- Enhance podcast/voiceover audio before publishing
- Upscale campaign audio assets to broadcast quality
- Pre-process audio before Canary-STT for better transcription accuracy

## Important Notes
- First run downloads model weights from HuggingFace
- Processing time depends on audio length and ddim_steps setting
- Uses cooperative GPU memory — unloads when idle to free VRAM for other tools
- Long audio is automatically chunked and processed with overlap for seamless results""",
        "example_functions": [
            {
                "name": "enhance_audio_file",
                "description": "Enhance a local audio file to 48kHz",
                "code": """async def enhance_audio_file(audio_path: str) -> str:
    result = await tool_execute("audiosr-enhance", {
        "audio_path": audio_path
    })
    return result["output_file"]"""
            },
            {
                "name": "enhance_from_url",
                "description": "Enhance audio from a URL",
                "code": """async def enhance_from_url(url: str, quality: int = 50) -> str:
    result = await tool_execute("audiosr-enhance", {
        "audio_url": url,
        "ddim_steps": quality
    })
    print(f"Enhanced {result['duration_seconds']}s in {result['processing_time_seconds']}s")
    return result["output_file"]"""
            },
        ],
        "example_code": """# Enhance audio from a URL
async def enhance_podcast():
    result = await tool_service.execute(
        tool_slug="audiosr-enhance",
        params={
            "audio_url": "https://example.com/podcast-episode.mp3",
            "ddim_steps": 50,
            "guidance_scale": 3.5
        }
    )
    print(f"Output: {result['output_file']}")
    print(f"Sample rate: {result['output_sample_rate']}Hz")
    return result

# Enhance a local file with higher quality
async def enhance_local():
    result = await tool_service.execute(
        tool_slug="audiosr-enhance",
        params={
            "audio_path": "/workspace/recording.wav",
            "ddim_steps": 100
        }
    )
    return result["output_file"]""",
        "required_env_vars": ["USE_AUDIOSR"],
        "required_environment_variables": {
            "USE_AUDIOSR": "Set to 'true' to enable AudioSR audio enhancement",
            "AUDIOSR_API_URL": "AudioSR server URL (default: http://host.docker.internal:8007)",
            "AUDIOSR_API_PORT": "AudioSR server port (default: 8007)",
            "AUDIOSR_MODEL": "Model variant: basic or speech (default: basic)",
            "AUDIOSR_AUTO_START": "Auto-start server on demand (default: true)",
            "AUDIOSR_IDLE_TIMEOUT": "Idle timeout before unloading model (default: 300)",
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_enhancement": 0,
            "notes": "Free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, enhance as much audio as you want
- **Versatile**: Works on music, speech, and environmental sounds
- **Any input rate**: Accepts audio at any sampling rate
- **High quality**: 48kHz output using latent diffusion super-resolution
- **Privacy**: All processing happens locally on your GPU
- **Long audio**: Automatic chunking with overlap for seamless results""",
        "weaknesses": """- **GPU Required**: Needs ~4-8GB VRAM for inference
- **Processing time**: Diffusion-based, slower than simple resampling
- **Initial download**: Model weights downloaded on first use
- **Memory sharing**: Shares GPU memory with other local AI services
- **Output format**: Always outputs 48kHz WAV (no format selection)""",
        "best_use_cases": """- Enhancing low-quality audio recordings to broadcast quality
- Upscaling podcast and voiceover audio before publishing
- Improving campaign audio assets (music, voice, SFX)
- Pre-processing audio before speech-to-text for better accuracy
- Restoring old or compressed audio recordings

**IDEAL FOR**: Upgrading any audio content to high-fidelity 48kHz quality""",
        "external_documentation_url": "https://github.com/haoheliu/versatile_audio_super_resolution",
        "version": "1.0",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_AUDIOSR,
        "timeout_seconds": 300
    },
    
    # Media Toolkit — FFmpeg-based Media Composition (CPU-only)
    "media_toolkit": {
        "condition": lambda: bool(settings.use_media_toolkit),
        "name": "Media Toolkit",
        "slug": "media-toolkit",
        "category": ToolCategory.API,
        "description": """FREE local media composition tool powered by FFmpeg. Split, combine, mix, and assemble media files from other tools. CPU-only — no GPU needed, runs alongside any GPU tool. Operations: probe (inspect metadata), extract_audio (pull audio from video), strip_audio (remove audio from video), combine (mux video + audio tracks with volume/timing/fade/loop), mix_audio (layer multiple audio tracks), adjust_volume (normalize or change volume), trim (cut time range), concat (join clips sequentially with optional crossfade), create_slideshow (images → video with Ken Burns zoom/pan effects + audio). Use this to chain outputs: e.g., split LTX-2 video → enhance audio with AudioSR + upscale video with SeedVR2 → recombine; or layer ACEStep music + Qwen3 narration → combine with video; or assemble Z-Image frames into a montage with music.""",
        "tags": ["media", "video", "audio", "ffmpeg", "composition", "mixing", "splitting", "combine", "montage", "slideshow", "local", "free", "cpu"],
        "usage_instructions": """# Media Toolkit

## Overview
FFmpeg-based media composition server (CPU-only, port 8008). Provides the glue layer for
chaining outputs between GPU tools (LTX-Video, Z-Image, ACEStep, Qwen3-TTS, AudioSR, SeedVR2).

## Operations

### probe — Inspect media file metadata
Returns duration, codecs, resolution, sample rate, tracks, bitrate.
```python
result = await tool_execute("media-toolkit", {
    "operation": "probe",
    "url": "http://localhost:8006/output/LTX2_00001.mp4"
})
# → {has_video, has_audio, duration_seconds, streams: [{type, codec, width, height, fps, sample_rate}]}
```

### extract_audio — Pull audio track from a video
```python
result = await tool_execute("media-toolkit", {
    "operation": "extract_audio",
    "video_url": "http://localhost:8006/output/LTX2_00001.mp4",
    "format": "wav",         # wav, mp3, flac, aac, ogg
    "sample_rate": 48000     # optional
})
# → {output_file, format, sample_rate, duration_seconds}
```

### strip_audio — Remove audio, keep video only
```python
result = await tool_execute("media-toolkit", {
    "operation": "strip_audio",
    "video_url": "http://localhost:8006/output/LTX2_00001.mp4"
})
# → {output_file, duration_seconds, resolution, has_audio: false}
```

### combine — Mux video with audio tracks (volume, timing, fade, loop)
```python
result = await tool_execute("media-toolkit", {
    "operation": "combine",
    "video_url": "http://localhost:8004/output/SEEDVR2_00001.mp4",
    "audio_tracks": [
        {"url": "http://localhost:8007/output/AUDIOSR_abc123.wav", "volume": 1.0},
        {"url": "http://localhost:8001/v1/audio?file=/path/to/music.mp3", "volume": 0.15, "loop": true, "fade_in": 1.0, "fade_out": 2.0}
    ]
})
# → {output_file, duration_seconds, audio_tracks_mixed}
```

### mix_audio — Layer multiple audio tracks with volume/timing/fade control
```python
result = await tool_execute("media-toolkit", {
    "operation": "mix_audio",
    "tracks": [
        {"url": "http://localhost:8001/v1/audio?file=/path/to/music.mp3", "volume": 0.15, "loop": true},
        {"url": "http://localhost:8002/output/tts_narration.wav", "volume": 1.0, "start_time": 2.0}
    ],
    "duration": 30,
    "output_format": "wav"
})
# → {output_file, format, duration_seconds, tracks_mixed}
```

### adjust_volume — Normalize or change volume of a single file
```python
result = await tool_execute("media-toolkit", {
    "operation": "adjust_volume",
    "url": "http://localhost:8002/output/tts_output.wav",
    "normalize": true   # EBU R128 loudness normalization to -16 LUFS
})
```

### trim — Cut a time range from any media file
```python
result = await tool_execute("media-toolkit", {
    "operation": "trim",
    "url": "http://localhost:8006/output/LTX2_00001.mp4",
    "start_time": 2.0,
    "end_time": 8.0       # or use "duration": 6.0
})
# → {output_file, format, duration_seconds, start_time, end_time}
```

### concat — Join multiple clips sequentially
```python
result = await tool_execute("media-toolkit", {
    "operation": "concat",
    "files": [
        "http://localhost:8006/output/LTX2_00001.mp4",
        "http://localhost:8006/output/LTX2_00002.mp4"
    ],
    "transition": "crossfade",     # none or crossfade
    "transition_duration": 0.5
})
# → {output_file, format, duration_seconds, files_concatenated}
```

### create_slideshow — Images → video with Ken Burns effects + audio
```python
result = await tool_execute("media-toolkit", {
    "operation": "create_slideshow",
    "images": [
        {"url": "http://localhost:8003/output/ZIMG_00001.png", "duration": 5, "effect": "zoom_in"},
        {"url": "http://localhost:8003/output/ZIMG_00002.png", "duration": 5, "effect": "pan_left"},
        {"url": "http://localhost:8003/output/ZIMG_00003.png", "duration": 5, "effect": "zoom_out"}
    ],
    "audio_url": "http://localhost:8001/v1/audio?file=/path/to/music.mp3",
    "fps": 24,
    "transition": "crossfade",
    "transition_duration": 0.5
})
# → {output_file, duration_seconds, image_count, has_audio, resolution, fps}
```

## Common Pipelines

### Pipeline 1: LTX-2 → Split → AudioSR + SeedVR2 → Recombine
1. Generate video with LTX-2
2. extract_audio from video → audio_url
3. strip_audio from video → silent_video_url
4. Enhance audio with AudioSR (audio_url)
5. Upscale video with SeedVR2 (silent_video_url)
6. combine upscaled video + enhanced audio

### Pipeline 2: Video + Background Music + Narration
1. Generate video with LTX-2
2. Generate music with ACEStep (instrumental: true)
3. Generate narration with Qwen3-TTS
4. mix_audio: music at volume 0.15 (looped) + narration at volume 1.0
5. combine video + mixed audio

### Pipeline 3: Image Montage with Music
1. Generate images with Z-Image (multiple calls)
2. Generate music with ACEStep
3. create_slideshow: images with zoom/pan effects + music

## Important Notes
- CPU-only — no GPU required, runs alongside any GPU tool
- Accepts URLs from any local service (auto-resolves to filesystem)
- All outputs saved to media-toolkit/output/ and served via /output/{filename}
- Processing is fast (seconds) for most operations — FFmpeg is highly optimized
- Crossfade transitions require re-encoding; simple concat uses stream copy (instant)""",
        "example_functions": [
            {
                "name": "extract_and_enhance_audio",
                "description": "Extract audio from video, enhance with AudioSR, then recombine",
                "code": """async def extract_and_enhance_audio(video_url: str) -> str:
    # 1. Extract audio
    audio = await tool_execute("media-toolkit", {
        "operation": "extract_audio",
        "video_url": video_url,
        "format": "wav"
    })
    # 2. Enhance with AudioSR
    enhanced = await tool_execute("audiosr-enhance", {
        "audio_url": audio["output_file"]
    })
    # 3. Strip audio from original video
    silent = await tool_execute("media-toolkit", {
        "operation": "strip_audio",
        "video_url": video_url
    })
    # 4. Recombine
    final = await tool_execute("media-toolkit", {
        "operation": "combine",
        "video_url": silent["output_file"],
        "audio_tracks": [{"url": enhanced["output_file"]}]
    })
    return final["output_file"]"""
            },
            {
                "name": "create_narrated_video",
                "description": "Combine video with background music and narration",
                "code": """async def create_narrated_video(video_url: str, music_url: str, narration_url: str) -> str:
    mixed = await tool_execute("media-toolkit", {
        "operation": "mix_audio",
        "tracks": [
            {"url": music_url, "volume": 0.15, "loop": True},
            {"url": narration_url, "volume": 1.0, "start_time": 1.0}
        ],
        "duration": 30
    })
    final = await tool_execute("media-toolkit", {
        "operation": "combine",
        "video_url": video_url,
        "audio_tracks": [{"url": mixed["output_file"]}]
    })
    return final["output_file"]"""
            },
        ],
        "example_code": """# Probe a video file
info = await tool_service.execute("media-toolkit", {"operation": "probe", "url": video_url})

# Create a slideshow from images with music
result = await tool_service.execute("media-toolkit", {
    "operation": "create_slideshow",
    "images": [
        {"url": img1_url, "duration": 5, "effect": "zoom_in"},
        {"url": img2_url, "duration": 5, "effect": "pan_right"},
    ],
    "audio_url": music_url,
    "transition": "crossfade",
    "transition_duration": 0.5
})""",
        "required_env_vars": ["USE_MEDIA_TOOLKIT"],
        "required_environment_variables": {
            "USE_MEDIA_TOOLKIT": "Set to 'true' to enable Media Toolkit",
            "MEDIA_TOOLKIT_API_URL": "Media Toolkit server URL (default: http://host.docker.internal:8008)",
            "MEDIA_TOOLKIT_API_PORT": "Media Toolkit server port (default: 8008)",
            "MEDIA_TOOLKIT_AUTO_START": "Auto-start server on demand (default: true)",
        },
        "integration_complexity": "low",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_operation": 0,
            "notes": "Free — CPU-only FFmpeg processing, no GPU required"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, pure local FFmpeg processing
- **CPU-only**: No GPU needed — runs alongside any GPU tool without contention
- **Fast**: Most operations complete in seconds (FFmpeg is highly optimized)
- **Versatile**: 9 operations covering split, combine, mix, trim, concat, slideshow
- **Pipeline enabler**: The glue layer for chaining outputs between GPU tools
- **Audio mixing**: Layer multiple tracks with per-track volume, timing, fade, loop
- **Ken Burns effects**: Zoom in/out, pan left/right for image slideshows
- **Cross-service resolution**: Automatically resolves URLs from other local services""",
        "weaknesses": """- **No AI generation**: Doesn't create new content — only composes existing media
- **FFmpeg required**: Host must have FFmpeg installed (standard on most systems)
- **No advanced editing**: No color grading, text overlays, or complex video effects
- **Re-encoding cost**: Crossfade transitions require re-encoding (slower than stream copy)""",
        "best_use_cases": """- Splitting LTX-2 video audio for separate enhancement with AudioSR
- Recombining upscaled SeedVR2 video with enhanced AudioSR audio
- Layering ACEStep music + Qwen3-TTS narration + video
- Creating image montages from Z-Image frames with Ken Burns effects
- Trimming and concatenating video clips for campaigns
- Normalizing audio volume across mixed sources
- Probing media files to make intelligent pipeline decisions

**IDEAL FOR**: Multi-step media production pipelines that chain multiple GPU tools""",
        "external_documentation_url": "https://ffmpeg.org/documentation.html",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_MEDIA_TOOLKIT,
        "timeout_seconds": 600
    },
    
    # Real-ESRGAN CPU — CPU-only Image & Video Upscaling
    "realesrgan_cpu_upscaler": {
        "condition": lambda: bool(settings.use_realesrgan_cpu),
        "name": "Real-ESRGAN CPU Upscaler",
        "slug": "realesrgan-cpu-upscaler",
        "category": ToolCategory.API,
        "description": """FREE local image & video upscaling using Real-ESRGAN on CPU. No GPU required — designed as a fallback when GPU is busy or unavailable. Upscales images and videos by 2x or 4x using Real-ESRGAN neural network models running on CPU. Models: realesr-animevideov3 (best for video/anime, fast), realesrgan-x4plus (best quality for photos), realesrnet-x4plus (faster photos), realesrgan-x4plus-anime (anime images). For video: extracts frames with FFmpeg, upscales each frame, and reassembles with audio. WARNING: CPU upscaling is significantly slower than GPU — a 10-second video may take 10-30 minutes. Use for short clips or when GPU is unavailable. For GPU-accelerated upscaling, use SeedVR2 Upscaler instead.""",
        "tags": ["upscale", "super-resolution", "image", "video", "cpu", "local", "free", "realesrgan", "esrgan"],
        "usage_instructions": """# Real-ESRGAN CPU Upscaler

## Overview
CPU-only image & video upscaler using Real-ESRGAN neural network models.
No GPU required — runs entirely on CPU. Port 8009.

IMPORTANT: This is a CPU fallback. For GPU-accelerated upscaling, use SeedVR2 Upscaler.
CPU upscaling is significantly slower but works on any system.

## Image Upscaling
```python
result = await tool_execute("realesrgan-cpu-upscaler", {
    "image_url": "http://localhost:8003/output/ZIMG_00001.png",
    "scale": 2  # 2x or 4x
})
# → {output_file, input_size, output_size, scale, processing_time_seconds}
```

## Video Upscaling (SLOW on CPU)
```python
result = await tool_execute("realesrgan-cpu-upscaler", {
    "video_url": "http://localhost:8006/output/LTX2_00001.mp4",
    "scale": 2,
    "tile": 4  # Lower = faster but more memory
})
# → {output_file, input_size, output_size, total_frames, seconds_per_frame, ...}
```

## Performance Expectations (CPU)
- Image: 2-15 seconds per image (depending on resolution)
- Video: 2-10+ seconds PER FRAME on CPU
- A 10-second 24fps video = 240 frames × ~5s/frame = ~20 minutes
- Use scale=2 instead of 4 for faster processing
- Use tile=4-8 to manage memory usage

## When to Use This vs SeedVR2
- **Use Real-ESRGAN CPU**: No GPU available, GPU busy with other tasks, simple upscaling needs
- **Use SeedVR2**: GPU available, need faster results, diffusion-based upscaling (adds detail)

## Models
- realesr-animevideov3: Best for video and anime content (default, fastest)
- realesrgan-x4plus: Best quality for photographs (slower)
- realesrnet-x4plus: Faster alternative for photographs
- realesrgan-x4plus-anime: Anime/illustration images

## Notes
- CPU-only — no GPU, no VRAM, no CUDA required
- Preserves audio in video upscaling (extracted and re-muxed)
- Output saved to realesrgan-cpu/output/ and served via /output/{filename}
- For large videos, consider trimming with Media Toolkit first""",
        "example_functions": [
            {
                "name": "upscale_image",
                "description": "Upscale a single image by 2x on CPU",
                "code": """result = await tool_execute("realesrgan-cpu-upscaler", {
    "image_url": "http://localhost:8003/output/ZIMG_00001.png",
    "scale": 2
})
print(f"Upscaled: {result['input_size']} → {result['output_size']}")
print(f"Output: {result['output_file']}")"""
            },
            {
                "name": "upscale_short_video",
                "description": "Upscale a short video clip on CPU (slow!)",
                "code": """# First trim to a short clip with Media Toolkit
trimmed = await tool_execute("media-toolkit", {
    "operation": "trim",
    "url": "http://localhost:8006/output/LTX2_00001.mp4",
    "start_time": 0,
    "duration": 5  # Only 5 seconds to keep CPU time reasonable
})
# Then upscale the trimmed clip
result = await tool_execute("realesrgan-cpu-upscaler", {
    "video_url": trimmed["output_file"],
    "scale": 2
})
print(f"Upscaled {result['total_frames']} frames in {result['processing_time_seconds']}s")"""
            },
        ],
        "example_code": """# Upscale an image
result = await tool_service.execute("realesrgan-cpu-upscaler", {
    "image_url": image_url,
    "scale": 2
})

# Upscale a short video
result = await tool_service.execute("realesrgan-cpu-upscaler", {
    "video_url": video_url,
    "scale": 2,
    "tile": 4
})""",
        "required_env_vars": ["USE_REALESRGAN_CPU"],
        "required_environment_variables": {
            "USE_REALESRGAN_CPU": "Set to 'true' to enable Real-ESRGAN CPU upscaler",
            "REALESRGAN_CPU_API_URL": "Real-ESRGAN CPU server URL (default: http://host.docker.internal:8009)",
            "REALESRGAN_CPU_API_PORT": "Real-ESRGAN CPU server port (default: 8009)",
            "REALESRGAN_CPU_MODEL": "Model name (default: realesr-animevideov3)",
            "REALESRGAN_CPU_AUTO_START": "Auto-start server on demand (default: true)",
        },
        "integration_complexity": "low",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_operation": 0,
            "notes": "Free — CPU-only Real-ESRGAN processing, no GPU required"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, pure local CPU processing
- **No GPU required**: Works on any system — perfect fallback when GPU is busy
- **Image + Video**: Upscales both images and video files
- **Multiple models**: General purpose, anime-optimized, and video-optimized models
- **Audio preservation**: Video upscaling preserves the original audio track
- **Tiled processing**: Configurable tile size keeps memory usage manageable on CPU
- **Auto-download**: Models auto-download from the Real-ESRGAN project""",
        "weaknesses": """- **SLOW on CPU**: 2-10+ seconds per frame (vs milliseconds on GPU)
- **No diffusion enhancement**: Unlike SeedVR2, does not add new detail — only sharpens
- **Limited to 2x/4x**: Fixed upscale factors (no arbitrary resolution)
- **Memory usage**: Can use significant RAM for high-resolution inputs
- **Video duration limited**: Max 2 minutes by default (CPU time would be excessive)""",
        "best_use_cases": """- Upscaling images when GPU is occupied by other tools (LTX-2, Z-Image, etc.)
- Systems without GPU (cloud VMs, older hardware, CI/CD pipelines)
- Batch processing images overnight on CPU
- Quick 2x upscale of screenshots or thumbnails
- Upscaling short video clips (< 30 seconds) when GPU upscaler is unavailable

**IDEAL FOR**: CPU-only environments or as GPU fallback for simple upscaling needs""",
        "external_documentation_url": "https://github.com/xinntao/Real-ESRGAN",
        "version": "1.0",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_REALESRGAN_CPU,
        "timeout_seconds": 3600
    },
    
    # LTX-2 Video — Local Text-to-Video Generation with Audio
    "ltx_video_generation": {
        "condition": lambda: bool(settings.use_ltx_video),
        "name": "LTX-2.3 Video Generation",
        "slug": "ltx-video-generation",
        "category": ToolCategory.API,
        "description": "FREE local video generation with synchronized audio using LTX-2.3 22B distilled FP8. Produces MP4 clips up to ~10 seconds at 768x512 resolution. Runs entirely on your GPU - UNLIMITED usage, NO API costs. PROMPTING: Write a single flowing paragraph (max 200 words) describing the scene chronologically. Use present-progressive verbs ('is walking', 'speaking'). Include: specific movements, character appearances (gender, clothing, hair, expressions), lighting, camera angles, environment details. Describe audio: background sounds, ambient noise, SFX, speech with exact dialogue in quotes and voice characteristics. Start directly with the action. Be specific and literal — think like a cinematographer. Prefix with 'Style: realistic with cinematic lighting.' or similar. Do NOT use timestamps, scene cuts, or preamble.",
        "tags": ["video", "generation", "ai", "local", "free", "gpu", "audio", "text-to-video", "ltx"],
        "usage_instructions": """# LTX-2 Video Generation

## Overview
LTX-2.3 uses a 22B parameter distilled DiT model (FP8 quantized) with Gemma 3 12B text encoder
to generate videos with synchronized audio from text prompts. Two-stage pipeline: 8 steps at
384x256, spatial upscale 2x, then 4 steps at 768x512. Includes audio generation.

## Parameters
- **prompt** (str, required): Detailed scene description (see prompting guide below)
- **width** (int): Output width, divisible by 32 (default: 768, max verified: 768)
- **height** (int): Output height, divisible by 32 (default: 512, max verified: 512)
- **num_frames** (int): Frame count, must be (N*8)+1 (default: 241 = ~10s at 24fps)
- **fps** (int): Frames per second (default: 24)
- **seed** (int, optional): Random seed for reproducibility
- **enhance_prompt** (bool): Enhance prompt via Ollama before generation (default: false)

## Prompting Guide
Write a SINGLE FLOWING PARAGRAPH (max 200 words) describing the scene chronologically:
- Use present-progressive verbs ("is walking", "is speaking")
- Include: specific movements, character details (gender, clothing, hair, expressions)
- Include: lighting, camera angles, environment details
- For audio: describe background sounds, ambient noise, SFX
- For speech: exact dialogue in quotes with voice characteristics
- Start directly with the action — no preamble
- Be specific and literal — think like a cinematographer
- Prefix with style if needed: "Style: realistic with cinematic lighting."

## Example: Generate a video
```python
result = await tool_execute("ltx-video-generation", {
    "prompt": "Style: realistic with cinematic lighting. A woman in a red coat is walking through a rainy city street at night, her heels clicking on wet cobblestones as neon signs reflect in the puddles. She pauses to look at a shop window, her breath visible in the cold air, while ambient city sounds and distant traffic fill the background.",
    "num_frames": 241,
    "seed": 42
})
print(f"Video: {result['video_url']}")
print(f"Duration: {result['duration_seconds']}s")
```

## Duration Guide
- 97 frames = ~4 seconds
- 161 frames = ~6.7 seconds
- 241 frames = ~10 seconds (maximum verified on RTX 3090)

## Limitations
- ~1-2 minutes generation time per clip
- Max 768x512 on 24GB VRAM (RTX 3090)
- Max ~10s duration per generation
- Audio quality varies (best for ambient/SFX, weaker for speech)
- First run loads model files (~48GB disk, ~20GB VRAM peak)

## Pipeline Integration
- Generate product demo videos for campaigns
- Create social media video content from text descriptions
- Generate video + audio → upscale with SeedVR2 for higher resolution
- Chain with Canary-STT to verify generated speech content

## Important Notes
- First run downloads model from HuggingFace (~48GB total)
- Generation of 10s clip takes ~1-2 minutes on RTX 3090
- Includes synchronized audio in output MP4
- Uses cooperative GPU memory — unloads when idle to free VRAM for other tools""",
        "example_functions": [
            {
                "name": "generate_video",
                "description": "Generate a video with audio from a text prompt",
                "code": """async def generate_video(prompt: str, duration_frames: int = 241, seed: int = None) -> str:
    params = {"prompt": prompt, "num_frames": duration_frames}
    if seed is not None:
        params["seed"] = seed
    result = await tool_execute("ltx-video-generation", params)
    print(f"Generated {result['duration_seconds']}s video in {result['inference_time']}s")
    return result["video_url"]"""
            },
            {
                "name": "generate_short_clip",
                "description": "Generate a short 4-second video clip",
                "code": """async def generate_short_clip(prompt: str) -> str:
    result = await tool_execute("ltx-video-generation", {
        "prompt": prompt,
        "num_frames": 97,  # ~4 seconds at 24fps
    })
    return result["video_url"]"""
            },
        ],
        "example_code": """# Generate a 10-second video with audio
async def create_product_video():
    result = await tool_service.execute(
        tool_slug="ltx-video-generation",
        params={
            "prompt": "Style: professional product showcase. A sleek smartphone is rotating slowly on a white pedestal, studio lighting casting soft reflections on its glass surface, while a subtle electronic ambient soundscape plays in the background.",
            "num_frames": 241,
            "seed": 42
        }
    )
    print(f"Video URL: {result['video_url']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Has audio: {result['has_audio']}")
    return result

# Generate a short clip
async def create_social_clip():
    result = await tool_service.execute(
        tool_slug="ltx-video-generation",
        params={
            "prompt": "A golden retriever is running through a sunlit meadow, tail wagging, as birds chirp overhead and grass rustles in a gentle breeze.",
            "num_frames": 97,
        }
    )
    return result["video_url"]""",
        "required_env_vars": ["USE_LTX_VIDEO"],
        "required_environment_variables": {
            "USE_LTX_VIDEO": "Set to 'true' to enable LTX-2 video generation",
            "LTX_VIDEO_API_URL": "LTX-2 server URL (default: http://host.docker.internal:8006)",
            "LTX_VIDEO_API_PORT": "LTX-2 server port (default: 8006)",
            "LTX_VIDEO_AUTO_START": "Auto-start server on demand (default: true)",
            "LTX_VIDEO_IDLE_TIMEOUT": "Idle timeout before unloading model (default: 300)",
            "LTX_VIDEO_MODEL_DIR": "Path to model weights (default: models/ltx-2)",
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_generation": 0,
            "notes": "Free - runs locally on your GPU"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, generate as many videos as you want
- **Synchronized audio**: Video and audio generated together in one pass
- **22B model quality**: State-of-the-art distilled DiT architecture
- **Fast distilled inference**: 8+4 steps total (vs 40+ for non-distilled)
- **Up to 10 seconds**: 241 frames at 24fps with audio
- **Reproducible**: Seed control for deterministic output
- **768x512 HD**: Two-stage pipeline with spatial upsampling
- **Privacy**: All processing happens locally on your GPU
- **Commercial use**: Apache 2.0 license""",
        "weaknesses": """- **~1-2 min generation**: Slower than image generation
- **Max 768x512**: Limited resolution on 24GB VRAM
- **Max ~10s**: Short clips only per generation call
- **~48GB disk**: Large model downloads required
- **GPU Required**: Needs ~20-21GB peak VRAM (RTX 3090 or better)
- **Audio quality varies**: Best for ambient/SFX, weaker for speech
- **Memory sharing**: Shares GPU memory with other local AI services""",
        "best_use_cases": """- Generating product demo videos for marketing campaigns
- Creating social media video content from text descriptions
- Prototyping video concepts before professional production
- Adding visual demos to presentations and proposals
- Generating ambient/atmospheric video backgrounds with audio

**IDEAL FOR**: Creating short video content with audio for campaigns and marketing""",
        "external_documentation_url": "https://huggingface.co/Lightricks/LTX-2",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_LTX_VIDEO,
        "timeout_seconds": 600
    },
    
    # Dev Sandbox — Isolated Agent Development Environment
    "dev_sandbox": {
        "condition": lambda: settings.use_dev_sandbox,
        "name": "Dev Sandbox",
        "slug": "dev-sandbox",
        "category": ToolCategory.API,
        "description": "Isolated Linux development environment for building, testing, and running code. Creates ephemeral Docker containers where agents can safely execute commands, install packages, write/read files, and build complete applications. Containers are automatically destroyed after use. FREE and UNLIMITED — uses local Docker, no external APIs.",
        "tags": ["sandbox", "development", "code-execution", "docker", "container", "build", "test", "deploy", "isolated"],
        "usage_instructions": """# Dev Sandbox — Isolated Development Environment

## Overview
The Dev Sandbox gives you a safe, isolated Linux environment (Docker container) where you can
run commands, install packages, write code, build applications, and test — all without affecting
the host system. Each sandbox is ephemeral and automatically destroyed after its timeout expires.

## Actions
The Dev Sandbox uses an action-based interface. Every call requires an `action` parameter.

### action: create — Create a new sandbox
**Parameters:**
- **image** (str): Docker image to use (default: python:3.12-slim)
- **memory_limit** (str): Memory limit, e.g. "512m", "1g", "2g" (default: 512m, max: 2g)
- **cpu_count** (float): CPU cores (default: 1.0, max: 4.0)
- **network_access** (bool): Allow internet access for pip install, npm install, etc. (default: false)
- **timeout_seconds** (int): Auto-destroy after this many seconds (default: 300, max: 1800)

**Returns:** sandbox_id (use this for all subsequent actions)

```python
result = tool_execute("dev-sandbox", {
    "action": "create",
    "image": "python:3.12-slim",
    "network_access": True,
    "timeout_seconds": 600
})
sandbox_id = result["sandbox_id"]
```

### action: exec — Run a command
**Parameters:**
- **sandbox_id** (str): Sandbox to run in
- **command** (str): Shell command to execute
- **workdir** (str): Working directory (default: /workspace)
- **timeout** (int): Command timeout in seconds (default: 60)

```python
result = tool_execute("dev-sandbox", {
    "action": "exec",
    "sandbox_id": sandbox_id,
    "command": "pip install requests beautifulsoup4"
})
print(result["stdout"])
```

### action: write — Write a file
**Parameters:**
- **sandbox_id** (str): Sandbox to write to
- **path** (str): File path inside the sandbox (parent dirs auto-created)
- **content** (str): File content

```python
tool_execute("dev-sandbox", {
    "action": "write",
    "sandbox_id": sandbox_id,
    "path": "/workspace/scraper.py",
    "content": "import requests\\nprint('hello')"
})
```

### action: read — Read a file
**Parameters:**
- **sandbox_id** (str): Sandbox to read from
- **path** (str): File path inside the sandbox

```python
result = tool_execute("dev-sandbox", {
    "action": "read",
    "sandbox_id": sandbox_id,
    "path": "/workspace/output.json"
})
print(result["content"])
```

### action: list — List files in a directory
**Parameters:**
- **sandbox_id** (str): Sandbox to list
- **path** (str): Directory path (default: /workspace)

### action: extract — Copy artifacts to host
**Parameters:**
- **sandbox_id** (str): Sandbox to extract from
- **paths** (list[str]): Specific paths to copy (default: entire /workspace)

### action: destroy — Tear down a sandbox
**Parameters:**
- **sandbox_id** (str): Sandbox to destroy
- **extract_first** (bool): Copy artifacts before destroying (default: false)

### action: info — Get sandbox status
**Parameters:**
- **sandbox_id** (str, optional): Specific sandbox, or omit to list all

### action: run_script — Write and execute a script in one call (EFFICIENT)
**Parameters:**
- **sandbox_id** (str): Sandbox to run in
- **script** (str): Multi-line script content
- **interpreter** (str): Interpreter — sh, bash, python3, node (default: sh)
- **workdir** (str): Working directory (default: /workspace)
- **timeout** (int): Timeout in seconds (default: 120)

```python
result = tool_execute("dev-sandbox", {
    "action": "run_script",
    "sandbox_id": sandbox_id,
    "interpreter": "python3",
    "script": "import json\ndata = {'result': 42}\nwith open('/workspace/out.json','w') as f:\n    json.dump(data,f)\nprint('done')"
})
print(result["stdout"])
```

### action: write_files — Write multiple files in one call (EFFICIENT)
**Parameters:**
- **sandbox_id** (str): Sandbox to write to
- **files** (dict): Mapping of {path: content} pairs

```python
tool_execute("dev-sandbox", {
    "action": "write_files",
    "sandbox_id": sandbox_id,
    "files": {
        "/workspace/app.py": "import flask\napp = flask.Flask(__name__)",
        "/workspace/requirements.txt": "flask\nrequests",
        "/workspace/config.json": '{"debug": true}'
    }
})
```

### action: setup — Create sandbox + write files + run commands in one call (MOST EFFICIENT)
Combines create, write_files, and exec into a single tool call. Best for common workflows.
**Parameters:**
- **image** (str): Docker image (default: python:3.12-slim)
- **memory_limit** (str): Memory limit (default: 512m)
- **network_access** (bool): Allow internet (default: false)
- **timeout_seconds** (int): Sandbox timeout (default: 300)
- **files** (dict): Optional {path: content} to write after creation
- **commands** (list[str]): Optional shell commands to run in sequence
- **workdir** (str): Working directory for commands (default: /workspace)

```python
result = tool_execute("dev-sandbox", {
    "action": "setup",
    "network_access": True,
    "timeout_seconds": 600,
    "files": {
        "/workspace/scraper.py": scraper_code,
        "/workspace/config.json": config_json
    },
    "commands": [
        "pip install requests beautifulsoup4",
        "python /workspace/scraper.py"
    ]
})
sandbox_id = result["sandbox_id"]
for cmd_result in result["command_results"]:
    print(cmd_result["command"], "->", cmd_result["exit_code"])
```

## Typical Workflow

**Efficient (preferred — 2 tool calls):**
1. **setup** with files + commands (creates sandbox, writes code, installs deps, runs script)
2. **read/extract** output, then **destroy**

**Granular (when you need step-by-step control):**
1. **create** a sandbox with network access
2. **write** or **write_files** your source files
3. **exec** or **run_script** to install dependencies and run code
4. **read** or **extract** the output
5. **destroy** when done

## Important Notes
- Each sandbox is completely isolated — no access to host files, database, or other services
- Default: no internet access. Set network_access=true if you need pip/npm/apt
- Sandboxes auto-destroy after their timeout (default 5 min, max 30 min)
- Maximum 5 concurrent sandboxes (configurable)
- Memory limited to 512MB by default (max 2GB)
- Output is truncated at 100KB per command to prevent memory issues""",
        "example_functions": [
            {
                "name": "build_python_scraper",
                "description": "Create, build, and run a Python web scraper in a sandbox",
                "code": """async def build_python_scraper(url: str) -> dict:
    # Create sandbox with internet for pip install
    sb = await tool_execute("dev-sandbox", {
        "action": "create",
        "network_access": True,
        "timeout_seconds": 600
    })
    sid = sb["sandbox_id"]

    # Install dependencies
    await tool_execute("dev-sandbox", {
        "action": "exec",
        "sandbox_id": sid,
        "command": "pip install requests beautifulsoup4"
    })

    # Write scraper code
    await tool_execute("dev-sandbox", {
        "action": "write",
        "sandbox_id": sid,
        "path": "/workspace/scraper.py",
        "content": f'''import requests, json
from bs4 import BeautifulSoup
resp = requests.get("{url}")
soup = BeautifulSoup(resp.text, "html.parser")
data = [h.text for h in soup.find_all("h2")]
with open("/workspace/output.json", "w") as f:
    json.dump(data, f)
print(f"Found {{len(data)}} items")
'''
    })

    # Run it
    result = await tool_execute("dev-sandbox", {
        "action": "exec",
        "sandbox_id": sid,
        "command": "python /workspace/scraper.py"
    })

    # Read output
    output = await tool_execute("dev-sandbox", {
        "action": "read",
        "sandbox_id": sid,
        "path": "/workspace/output.json"
    })

    # Cleanup
    await tool_execute("dev-sandbox", {
        "action": "destroy",
        "sandbox_id": sid
    })
    return json.loads(output["content"])"""
            },
            {
                "name": "run_data_analysis_efficient",
                "description": "Run a Python data analysis in a sandbox using setup action (2 calls instead of 7)",
                "code": """async def run_data_analysis(csv_data: str, script: str) -> str:
    # One call: create sandbox + write both files + install pandas + run script
    result = await tool_execute("dev-sandbox", {
        "action": "setup",
        "network_access": True,
        "files": {
            "/workspace/data.csv": csv_data,
            "/workspace/analyze.py": script
        },
        "commands": [
            "pip install pandas",
            "python /workspace/analyze.py"
        ]
    })
    sid = result["sandbox_id"]
    run_output = result["command_results"][-1]["stdout"]

    await tool_execute("dev-sandbox", {
        "action": "destroy",
        "sandbox_id": sid
    })
    return run_output"""
            },
        ],
        "example_code": """# Build a Python web scraper in a sandbox
async def scrape_website():
    sb = await tool_service.execute(
        tool_slug="dev-sandbox",
        params={"action": "create", "network_access": True, "timeout_seconds": 600}
    )
    sid = sb["sandbox_id"]

    await tool_service.execute(
        tool_slug="dev-sandbox",
        params={"action": "exec", "sandbox_id": sid,
                "command": "pip install requests beautifulsoup4"}
    )

    await tool_service.execute(
        tool_slug="dev-sandbox",
        params={"action": "write", "sandbox_id": sid,
                "path": "/workspace/scraper.py",
                "content": "import requests; print(requests.get('https://example.com').status_code)"}
    )

    result = await tool_service.execute(
        tool_slug="dev-sandbox",
        params={"action": "exec", "sandbox_id": sid,
                "command": "python /workspace/scraper.py"}
    )
    print(result["stdout"])

    await tool_service.execute(
        tool_slug="dev-sandbox",
        params={"action": "destroy", "sandbox_id": sid}
    )""",
        "required_env_vars": ["USE_DEV_SANDBOX"],
        "required_environment_variables": {
            "USE_DEV_SANDBOX": "Enable/disable the dev sandbox tool (default: true)",
            "DEV_SANDBOX_DEFAULT_IMAGE": "Default Docker image (default: python:3.12-slim)",
            "DEV_SANDBOX_MAX_CONCURRENT": "Max concurrent sandboxes (default: 5)",
            "DEV_SANDBOX_DEFAULT_TIMEOUT": "Default sandbox timeout in seconds (default: 300)",
            "DEV_SANDBOX_MAX_TIMEOUT": "Maximum sandbox timeout (default: 1800)",
            "DEV_SANDBOX_DEFAULT_MEMORY": "Default memory limit (default: 512m)",
            "DEV_SANDBOX_MAX_MEMORY": "Maximum memory limit (default: 2g)",
            "DEV_SANDBOX_NETWORK_ACCESS": "Default network access (default: false)",
        },
        "integration_complexity": "low",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "notes": "Uses local Docker — no external APIs, no API costs"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs — uses local Docker
- **Full Linux environment**: Any language, any tool, any package
- **Isolated & safe**: Each sandbox is completely isolated from host and other sandboxes
- **Ephemeral**: Auto-cleaned up — no leftover files or processes
- **Cross-platform**: Works on Linux, macOS, and Windows (Docker Desktop)
- **No GPU required**: Runs on CPU, no VRAM needed
- **Fast startup**: Sub-second container creation
- **Network control**: Internet access opt-in per sandbox""",
        "weaknesses": """- **No GPU access**: Cannot run ML/AI workloads inside sandboxes
- **Docker socket required**: Needs /var/run/docker.sock mount
- **Resource limited**: Max 2GB memory, 4 CPUs per sandbox
- **No persistence**: Sandboxes are destroyed after timeout (artifacts must be extracted)
- **Image pull time**: First use of a new Docker image requires download""",
        "best_use_cases": """- Building web scrapers and data pipelines
- Prototyping and testing applications
- Running data analysis scripts
- Creating and testing Node.js/Python/Go applications
- Generating reports from data
- Package dependency isolation
- Running untrusted or experimental code safely

**IDEAL FOR**: Any task where an agent needs to write, build, or run code""",
        "external_documentation_url": "https://docs.docker.com/engine/api/",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_DEV_SANDBOX
    },
    
    # Ollama Local LLM
    "ollama_llm": {
        "condition": lambda: bool(settings.use_ollama),
        "name": "Ollama LLM",
        "slug": "ollama-llm",
        "category": ToolCategory.API,
        "description": "Local LLM provider via Ollama. Supports three tiers: fast (Nanbeige4.1-3B), reasoning (mistral-nemo:12b), and quality (glm-4.7-flash:latest). Best for offline/private operations or when cloud APIs are unavailable.",
        "tags": ["llm", "ai", "text-generation", "local", "offline", "privacy", "tiered"],
        "usage_instructions": """# Ollama LLM Provider

## Overview
Ollama runs LLMs locally on your machine. It's the fourth provider in the fallback chain, used when cloud APIs are unavailable or for privacy-sensitive operations.

## Tiers
| Tier | Default Model | Use Case | Notes |
|------|---------------|----------|-------|
| **fast** | Nanbeige4.1-3B (GGUF Q8_0) | Quick responses | Small, fast, 262K context window |
| **reasoning** | mistral-nemo:12b | Complex tasks | Larger context, better reasoning |
| **quality** | glm-4.7-flash:latest | Best output | Highest quality, slower |

## Configuration
- `USE_OLLAMA=true` - Enable Ollama provider
- `OLLAMA_BASE_URL=http://host.docker.internal:11434` - API endpoint
- `OLLAMA_MODEL_TIERS=hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0,mistral-nemo:12b,glm-4.7-flash:latest` - Models per tier
- `OLLAMA_CONTEXT_LENGTHS=262144,65536,8192` - Context windows
- `OLLAMA_MAX_CONCURRENT=1` - Max concurrent requests (rate limiting)

## Prerequisites
1. Install Ollama: https://ollama.ai/download
2. Pull required models:
   ```bash
   ollama pull hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0
   ollama pull mistral-nemo:12b
   ollama pull glm-4.7-flash:latest
   ```
3. Ollama v0.14.3+ is required for glm-4.7-flash
4. Start Ollama service: `ollama serve`

## Usage in Agents
```python
# Force Ollama provider
response = await llm_service.generate(
    messages=messages,
    model="ollama:fast",  # Forces local Ollama Nanbeige4.1-3B
    max_tokens=4096
)

# Or just request a tier (uses priority order, Ollama is last)
response = await llm_service.generate(
    messages=messages,
    model="quality",  # Will try GLM, Claude, OpenAI first
)
```

## Docker Configuration
When running in Docker, use `host.docker.internal:11434` to reach Ollama on the host machine.

## Firewall Configuration
If using Ollama from a remote machine:
```bash
# Allow Ollama port through firewall
sudo ufw allow 11434/tcp
```

## Best Practices
- Use for offline/air-gapped environments
- Good for development and testing
- Rate limit carefully (GPU resources)
- Monitor GPU memory usage
- Consider smaller models for faster responses""",
        "example_code": """# Via LLM Service (recommended)
from app.services.llm_service import LLMService, LLMMessage

llm = LLMService()

# Force Ollama fast tier
response = await llm.generate(
    messages=[
        LLMMessage(role="system", content="You are a helpful assistant."),
        LLMMessage(role="user", content="Summarize this article...")
    ],
    model="ollama:fast",  # Forces local Nanbeige4.1-3B
    temperature=0.7,
    max_tokens=4096
)

# Force Ollama quality tier
response = await llm.generate(
    messages=messages,
    model="ollama:quality",  # Forces local glm-4.7-flash:latest
    max_tokens=8192  # Higher for complex tasks
)

# Direct Ollama API usage (not recommended - use LLM service)
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0",
            "prompt": "Hello, how are you?",
            "stream": False
        }
    )
    result = response.json()
    print(result["response"])""",
        "required_environment_variables": {
            "USE_OLLAMA": "Set to 'true' to enable Ollama LLM provider",
            "OLLAMA_BASE_URL": "Ollama API endpoint (default: http://host.docker.internal:11434)",
            "OLLAMA_MODEL_TIERS": "Models for fast,reasoning,quality tiers (comma-separated)",
            "OLLAMA_CONTEXT_LENGTHS": "Context window sizes per tier (comma-separated)",
            "OLLAMA_MAX_CONCURRENT": "Max concurrent requests (default: 1)"
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "local",
            "compute_cost": "Electricity + hardware depreciation",
            "notes": "No API fees - runs on your hardware"
        },
        "strengths": """- **FREE**: No API costs, runs locally
- **Privacy**: Data never leaves your machine
- **Offline**: Works without internet
- **Customizable**: Use any supported model
- **No rate limits**: Only limited by your hardware
- **Fast local inference**: Low latency for local GPU""",
        "weaknesses": """- **Hardware required**: Needs capable GPU (8GB+ VRAM recommended)
- **Last in fallback**: Only used when cloud unavailable
- **Setup required**: Must install Ollama and pull models
- **Context limits**: Smaller context than cloud models
- **Quality variance**: Local models may be less capable
- **Concurrent limits**: Typically 1 request at a time""",
        "best_use_cases": """- Development and testing without API costs
- Offline/air-gapped environments
- Privacy-sensitive operations
- High-volume low-stakes tasks
- Fallback when cloud APIs are down
- Learning and experimentation""",
        "external_documentation_url": "https://ollama.ai/",
        "version": "local",
        "priority": "low",
        "input_schema": INPUT_SCHEMA_LLM
    },

    # LND Lightning — Bitcoin Lightning Network operations
    "lnd_lightning": {
        "condition": lambda: bool(settings.lnd_rest_url and settings.lnd_macaroon_hex.get_secret_value()),
        "name": "LND Lightning",
        "slug": "lnd-lightning",
        "category": ToolCategory.API,
        "description": "Bitcoin Lightning Network operations via LND. Agents can check balances, create invoices, pay invoices, send on-chain transactions, and more. All spend operations are budget-enforced through the Bitcoin Budget System — payments exceeding campaign or global limits are automatically blocked.",
        "tags": ["bitcoin", "lightning", "lnd", "payments", "invoices", "sats", "budget"],
        "usage_instructions": """# LND Lightning — Bitcoin Lightning Network

## Overview
This tool connects agents to your LND Lightning node, enabling them to send/receive
Bitcoin payments on the Lightning Network and on-chain. All outgoing payments are
subject to budget enforcement (global safety limits + campaign budgets).

## Actions

### Read-Only (no budget check)
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **balance** | Get wallet + channel balances | (none) |
| **info** | Get node info (alias, pubkey, sync status) | (none) |
| **list_payments** | Recent outgoing payments | limit (default 20) |
| **list_invoices** | Recent incoming invoices | limit (default 20) |
| **list_channels** | Active Lightning channels | (none) |
| **estimate_fee** | Estimate on-chain fee | address, amount_sats |
| **decode_invoice** | Decode a BOLT-11 invoice | payment_request |

### Receive (no budget check)
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **create_invoice** | Create invoice to receive sats | amount_sats, memo (optional) |

### Spend (budget-enforced)
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **pay_invoice** | Pay a Lightning invoice | payment_request, campaign_id (optional) |
| **send_onchain** | Send an on-chain transaction | address, amount_sats, campaign_id (optional) |

## Budget Enforcement
Spend operations (pay_invoice, send_onchain) check:
1. **Global safety limit** — configurable max per payment (LND_MAX_PAYMENT_SATS)
2. **Campaign budget** — if campaign_id is provided, checks remaining campaign budget

If budget check fails, the tool returns an error with details about which limit was exceeded.

## Examples

```python
# Check your balance
result = await tool_execute("lnd-lightning", {"action": "balance"})

# Create an invoice to receive 1000 sats
result = await tool_execute("lnd-lightning", {
    "action": "create_invoice",
    "amount_sats": 1000,
    "memo": "Payment for campaign work"
})
print(result["payment_request"])  # BOLT-11 string

# Pay a Lightning invoice (budget-enforced)
result = await tool_execute("lnd-lightning", {
    "action": "pay_invoice",
    "payment_request": "lnbc...",
    "campaign_id": "uuid-of-campaign"  # optional
})

# Send on-chain (budget-enforced)
result = await tool_execute("lnd-lightning", {
    "action": "send_onchain",
    "address": "bc1q...",
    "amount_sats": 50000,
    "campaign_id": "uuid-of-campaign"
})
```""",
        "example_code": """# Agent workflow: receive payment, check balance, make payment
async def lightning_payment_flow():
    # 1. Check current balance
    balance = await tool_service.execute(
        tool_slug="lnd-lightning",
        params={"action": "balance"}
    )
    print(f"Channel balance: {balance['channel_balance']}")

    # 2. Create invoice for incoming payment
    invoice = await tool_service.execute(
        tool_slug="lnd-lightning",
        params={
            "action": "create_invoice",
            "amount_sats": 5000,
            "memo": "Service payment"
        }
    )
    print(f"Invoice: {invoice['payment_request']}")

    # 3. Pay an outgoing invoice (budget-checked)
    payment = await tool_service.execute(
        tool_slug="lnd-lightning",
        params={
            "action": "pay_invoice",
            "payment_request": "lnbc50u1p...",
            "campaign_id": "campaign-uuid"
        }
    )""",
        "required_env_vars": ["LND_REST_URL", "LND_MACAROON_HEX"],
        "required_environment_variables": {
            "LND_REST_URL": "LND REST API URL (e.g., https://your-node.onion:8080)",
            "LND_MACAROON_HEX": "Admin macaroon in hex format for LND authentication",
            "LND_TLS_CERT_PATH": "Path to TLS cert (optional, verification disabled for .onion)",
            "LND_MAX_PAYMENT_SATS": "Global safety limit per payment in sats (default: 10000)",
        },
        "integration_complexity": "medium",
        "cost_model": "per_use",
        "cost_details": {
            "type": "bitcoin",
            "notes": "Costs are in satoshis. Lightning fees typically 0.01-1% of payment amount. On-chain fees vary with network congestion."
        },
        "strengths": """- **Real Bitcoin payments**: Send and receive actual Bitcoin via Lightning Network
- **Budget enforcement**: Automatic spend limits per campaign and globally
- **Lightning speed**: Near-instant payments with minimal fees
- **Full LND access**: Balance, channels, invoices, payments, on-chain
- **Tor support**: Works with .onion addresses for maximum privacy""",
        "weaknesses": """- **Requires LND node**: Must have a running LND node with REST API enabled
- **Channel liquidity**: Lightning payments limited by channel capacity
- **Irreversible**: Bitcoin payments cannot be reversed once confirmed
- **Network fees**: On-chain transactions incur mining fees""",
        "best_use_cases": """- Paying for services or APIs that accept Lightning
- Receiving payments for completed work
- Automated campaign spending with budget guardrails
- Cross-platform value transfer
- Micropayments for per-use services""",
        "external_documentation_url": "https://lightning.engineering/api-docs/api/lnd/",
        "version": "1.0",
        "priority": "high",
        "input_schema": INPUT_SCHEMA_LND_LIGHTNING
    },

    # Nostr — Decentralized social protocol interactions
    "nostr": {
        "condition": lambda: settings.use_nostr,
        "name": "Nostr",
        "slug": "nostr",
        "category": ToolCategory.API,
        "description": "Interact with the Nostr decentralized social protocol. Create and manage pseudonymous identities, post content (notes and long-form articles), search and discover content/users, follow/unfollow, react, repost, and reply. When USE_LND is enabled, supports sending and receiving Lightning Zaps (NIP-57). All identities use independently generated keypairs with encrypted storage.",
        "tags": ["nostr", "social", "decentralized", "bitcoin", "zaps", "content", "publishing"],
        "usage_instructions": """# Nostr — Decentralized Social Protocol

## Overview
This tool lets agents interact with the Nostr decentralized social network.
Agents can create pseudonymous identities, publish content, search, follow users,
and optionally send/receive Lightning zaps.

## Actions

### Identity Management
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **create_identity** | Generate new keypair + set profile | name, about (optional: picture, nip05, lud16, relays) |
| **list_identities** | List all managed identities | (none) |
| **get_identity** | Get identity details + stats | identity_id |
| **update_profile** | Update profile metadata | identity_id + fields to update |

### Content Publishing
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **post_note** | Publish a short text note | identity_id, content (optional: hashtags, reply_to) |
| **post_article** | Publish long-form content | identity_id, title, content (optional: summary, hashtags, image) |
| **react** | React to an event | identity_id, event_id (optional: reaction, default "+") |
| **repost** | Repost an event | identity_id, event_id |
| **reply** | Reply to an event | identity_id, event_id, content |
| **follow** | Follow users | identity_id, pubkeys |
| **unfollow** | Unfollow users | identity_id, pubkeys |
| **delete_event** | Request event deletion | identity_id, event_ids |

### Discovery
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **search** | NIP-50 full-text search | query (optional: kinds, limit) |
| **get_feed** | Posts from followed users | identity_id (optional: limit) |
| **get_thread** | Note and its replies | event_id (optional: limit) |
| **get_profile** | User's profile info | pubkey_or_npub (optional: include_posts) |
| **get_engagement** | Reactions/replies for identity | identity_id (optional: since, limit) |

### Zaps (requires USE_LND=true)
| Action | Description | Required Params |
|--------|-------------|-----------------|
| **send_zap** | Send Lightning zap | identity_id, target, amount_sats (optional: comment) |
| **get_zap_receipts** | Get received zaps | identity_id (optional: since, limit) |

## Identity Tips
- Each identity has independent keys — no link between them
- Set a lud16 (Lightning address) to receive zaps
- Use different identities for different campaign niches
- Private keys are never exposed — signing happens internally

## Examples

```python
# Create a niche identity
result = await tool_execute("nostr", {
    "action": "create_identity",
    "name": "Bitcoin Tips Daily",
    "about": "Daily Bitcoin tips for beginners",
    "lud16": "tips@walletofsatoshi.com"
})
identity_id = result["identity_id"]

# Post content
result = await tool_execute("nostr", {
    "action": "post_note",
    "identity_id": identity_id,
    "content": "Tip #42: Always verify your backup seed phrase!",
    "hashtags": ["bitcoin", "security"]
})

# Search for related content
result = await tool_execute("nostr", {
    "action": "search",
    "query": "bitcoin self custody",
    "limit": 10
})

# Follow relevant accounts
result = await tool_execute("nostr", {
    "action": "follow",
    "identity_id": identity_id,
    "pubkeys": ["npub1..."]
})
```""",
        "example_code": """# Agent workflow: create identity, build presence, engage
async def nostr_campaign_flow():
    # 1. Create a niche identity
    identity = await tool_service.execute(
        tool_slug="nostr",
        params={
            "action": "create_identity",
            "name": "Crypto Privacy Hub",
            "about": "Exploring privacy in the Bitcoin ecosystem",
        }
    )
    identity_id = identity["identity_id"]

    # 2. Post an introductory note
    post = await tool_service.execute(
        tool_slug="nostr",
        params={
            "action": "post_note",
            "identity_id": identity_id,
            "content": "Hello Nostr! Excited to share privacy tips here.",
            "hashtags": ["bitcoin", "privacy", "introductions"]
        }
    )

    # 3. Search for content in the niche
    results = await tool_service.execute(
        tool_slug="nostr",
        params={"action": "search", "query": "bitcoin privacy coinjoin"}
    )

    # 4. Engage with relevant posts
    for item in results.get("results", [])[:3]:
        await tool_service.execute(
            tool_slug="nostr",
            params={
                "action": "react",
                "identity_id": identity_id,
                "event_id": item["id"]
            }
        )""",
        "required_env_vars": ["USE_NOSTR"],
        "required_environment_variables": {
            "USE_NOSTR": "Set to 'true' to enable Nostr tool (default: false)",
            "NOSTR_DEFAULT_RELAYS": "Comma-separated relay URLs (default: damus, nos.lol, nostr.band, snort.social)",
            "NOSTR_POST_RATE_LIMIT_HOUR": "Max posts per identity per hour (default: 10)",
            "NOSTR_POST_RATE_LIMIT_DAY": "Max posts per identity per day (default: 50)",
        },
        "integration_complexity": "medium",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "notes": "Nostr is free — no API keys needed. Zap sends cost sats (budget-enforced via LND)."
        },
        "strengths": """- **Censorship-resistant**: No central authority can ban identities
- **Free**: No API keys or usage fees — just cryptographic keypairs
- **Bitcoin-native**: Lightning Zaps for monetization when LND is connected
- **Pseudonymous**: Independent identities per campaign or niche
- **Growing ecosystem**: Millions of users, active developer community
- **No rate limits**: Protocol-level — post freely (tool-level limits for safety)""",
        "weaknesses": """- **Relay dependent**: Content availability depends on relay uptime
- **No edit/delete guarantee**: Deletion is a request, not enforced
- **Search varies**: NIP-50 search only on supporting relays
- **No media uploads**: Text-only (images via external URLs)
- **Zaps require LND**: Lightning payments need a connected LND node""",
        "best_use_cases": """- Building niche presences for campaign topics
- Content marketing in Bitcoin/crypto communities
- Engaging with decentralized social communities
- Earning Bitcoin via Lightning Zaps
- Cross-posting campaign content to censorship-resistant platform
- Monitoring trends and engagement in Nostr ecosystem""",
        "external_documentation_url": "https://github.com/nostr-protocol/nostr",
        "version": "1.0",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_NOSTR
    },
    
    # Docling — Document Parsing & Conversion (CPU-only)
    "docling_parser": {
        "condition": lambda: bool(settings.use_docling),
        "name": "Docling Document Parser",
        "slug": "docling-parser",
        "category": ToolCategory.API,
        "description": """FREE local document parsing and conversion using IBM Docling. Converts PDF, DOCX, PPTX, XLSX, HTML, Markdown, AsciiDoc, CSV, and images (PNG, JPG, TIFF, BMP) into structured Markdown, JSON, or plain text. Features: advanced table structure recognition (TableFormer), OCR for scanned documents and images (EasyOCR), figure detection, metadata extraction. CPU-only — no GPU required, runs entirely on your machine. Ideal for extracting content from research papers, contracts, reports, invoices, presentations, and web pages for agent analysis and campaign research.""",
        "tags": ["document", "parser", "pdf", "docx", "pptx", "html", "ocr", "table", "markdown", "conversion", "cpu", "local", "free", "docling"],
        "usage_instructions": """# Docling Document Parser

## Overview
Parse and convert documents using IBM Docling. Supports 15+ input formats
including PDF, DOCX, PPTX, HTML, images, and more.
CPU-only — no GPU required. Port 8010.

## Parse a PDF from URL
```python
result = await tool_execute("docling-parser", {
    "url": "https://example.com/report.pdf",
    "output_format": "markdown"
})
# → {content, output_file, metadata, page_count, tables_found, figures_found, ...}
print(result["content"])  # Full Markdown text
```

## Parse a local file
```python
result = await tool_execute("docling-parser", {
    "file_path": "/path/to/document.docx",
    "output_format": "markdown"
})
```

## Parse from sibling service output
```python
# Parse an image from Z-Image to extract text (OCR)
result = await tool_execute("docling-parser", {
    "url": "http://localhost:8003/output/ZIMG_00001.png",
    "output_format": "text"
})
```

## Get structured JSON output
```python
result = await tool_execute("docling-parser", {
    "url": "https://example.com/paper.pdf",
    "output_format": "json"
})
# → Full document structure with sections, tables, figures, metadata
```

## Supported Input Formats
- **Documents**: PDF, DOCX, PPTX, XLSX
- **Web**: HTML
- **Text**: Markdown, AsciiDoc, CSV
- **Images**: PNG, JPG, JPEG, TIFF, BMP, GIF (OCR extraction)

## Output Formats
- **markdown**: Structured Markdown with headers, tables, lists (default)
- **json**: Full document structure as JSON (sections, tables, figures)
- **text**: Plain text with formatting stripped

## Performance
- PDF: 1-10 seconds depending on page count and complexity
- DOCX/PPTX: 1-5 seconds
- Images with OCR: 2-15 seconds
- Large documents (100+ pages): may take 30-60 seconds

## Use Cases
- Extract content from research papers for opportunity analysis
- Parse invoices and contracts for data extraction
- Convert presentations to readable Markdown summaries
- OCR scanned documents and images
- Extract tables from financial reports""",
        "example_functions": [
            {
                "name": "parse_pdf_to_markdown",
                "description": "Parse a PDF document to Markdown",
                "code": """result = await tool_execute("docling-parser", {
    "url": "https://example.com/report.pdf",
    "output_format": "markdown"
})
print(f"Parsed {result['page_count']} pages, {result['tables_found']} tables")
print(result["content"][:500])  # Preview first 500 chars"""
            },
            {
                "name": "parse_image_ocr",
                "description": "Extract text from an image using OCR",
                "code": """result = await tool_execute("docling-parser", {
    "url": "http://localhost:8003/output/ZIMG_00001.png",
    "output_format": "text"
})
print(f"Extracted text: {result['content']}")"""
            },
        ],
        "example_code": """# Parse a document to Markdown
result = await tool_service.execute("docling-parser", {
    "url": document_url,
    "output_format": "markdown"
})

# Parse a document to JSON for structured extraction
result = await tool_service.execute("docling-parser", {
    "url": document_url,
    "output_format": "json"
})""",
        "required_env_vars": ["USE_DOCLING"],
        "required_environment_variables": {
            "USE_DOCLING": "Set to 'true' to enable Docling Document Parser",
            "DOCLING_API_URL": "Docling server URL (default: http://host.docker.internal:8010)",
            "DOCLING_API_PORT": "Docling server port (default: 8010)",
            "DOCLING_AUTO_START": "Auto-start server on demand (default: true)",
        },
        "integration_complexity": "low",
        "cost_model": "free",
        "cost_details": {
            "type": "free",
            "monthly_cost": 0,
            "per_operation": 0,
            "notes": "Free — CPU-only document parsing using Docling, no GPU or API keys required"
        },
        "strengths": """- **FREE & UNLIMITED**: No API costs, pure local CPU processing
- **15+ input formats**: PDF, DOCX, PPTX, XLSX, HTML, Markdown, images, and more
- **Advanced table recognition**: TableFormer model extracts complex table structures
- **OCR built-in**: EasyOCR for scanned documents and images
- **Multiple output formats**: Markdown, JSON, or plain text
- **No GPU required**: Runs entirely on CPU — works on any system
- **Metadata extraction**: Document titles, page counts, figure/table counts
- **Cross-service integration**: Can parse output files from sibling services""",
        "weaknesses": """- **Large documents are slow**: 100+ page PDFs may take 30-60 seconds on CPU
- **OCR quality varies**: Depends on image quality and resolution
- **No GPU acceleration**: Slower than GPU-based OCR solutions
- **Memory usage**: Large documents may use significant RAM
- **First request slow**: Model loading takes 30-60 seconds on first parse""",
        "best_use_cases": """- Parsing research papers and reports for opportunity analysis
- Extracting data from invoices, contracts, and financial documents
- Converting presentations (PPTX) to readable summaries
- OCR for scanned documents and screenshots
- Extracting tables from PDF reports for structured analysis
- Converting HTML web pages to clean Markdown
- Processing campaign materials and competitor documents

**IDEAL FOR**: Document intelligence and content extraction for agent-driven research""",
        "external_documentation_url": "https://github.com/docling-project/docling",
        "version": "1.0",
        "priority": "medium",
        "input_schema": INPUT_SCHEMA_DOCLING,
        "timeout_seconds": 600
    }
}


async def get_or_create_system_user(db: AsyncSession) -> User:
    """Get or create the system user for tool initialization."""
    result = await db.execute(
        select(User).where(User.email == "system@money-agents.dev")
    )
    user = result.scalar_one_or_none()
    
    if not user:
        from app.core.security import get_password_hash
        user = User(
            username="system",
            email="system@money-agents.dev",
            password_hash=get_password_hash("system_only_no_login"),
            role="admin",
            is_active=True,
            is_superuser=True
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    
    return user


async def create_or_update_tool(
    db: AsyncSession,
    system_user: User,
    tool_def: dict,
    is_available: bool
) -> Tool:
    """Create or update a tool in the catalog."""
    
    # Check if tool exists
    result = await db.execute(
        select(Tool).where(Tool.slug == tool_def["slug"])
    )
    existing_tool = result.scalar_one_or_none()
    
    if existing_tool:
        # Update existing tool
        if is_available:
            # Enable tool
            if existing_tool.status in [ToolStatus.DEPRECATED, ToolStatus.RETIRED]:
                existing_tool.status = ToolStatus.IMPLEMENTED
                print(f"  ✅ Re-enabled: {tool_def['name']}")
            else:
                print(f"  ℹ️  Already exists: {tool_def['name']}")
        else:
            # Disable tool (API key removed)
            if existing_tool.status == ToolStatus.IMPLEMENTED:
                existing_tool.status = ToolStatus.DEPRECATED
                print(f"  ⚠️  Deprecated (API key missing): {tool_def['name']}")
            else:
                print(f"  ℹ️  Already disabled: {tool_def['name']}")
        
        # Update fields
        existing_tool.description = tool_def["description"]
        existing_tool.tags = tool_def["tags"]
        existing_tool.usage_instructions = tool_def["usage_instructions"]
        existing_tool.example_code = tool_def["example_code"]
        existing_tool.required_environment_variables = tool_def["required_environment_variables"]
        existing_tool.integration_complexity = tool_def["integration_complexity"]
        existing_tool.cost_model = tool_def["cost_model"]
        existing_tool.cost_details = tool_def["cost_details"]
        existing_tool.strengths = tool_def["strengths"]
        existing_tool.weaknesses = tool_def["weaknesses"]
        existing_tool.best_use_cases = tool_def["best_use_cases"]
        existing_tool.external_documentation_url = tool_def["external_documentation_url"]
        existing_tool.version = tool_def["version"]
        existing_tool.priority = tool_def["priority"]
        existing_tool.input_schema = tool_def.get("input_schema")
        if "timeout_seconds" in tool_def:
            existing_tool.timeout_seconds = tool_def["timeout_seconds"]
        existing_tool.updated_at = datetime.utcnow()
        
        return existing_tool
    
    elif is_available:
        # Create new tool
        tool = Tool(
            name=tool_def["name"],
            slug=tool_def["slug"],
            category=tool_def["category"],
            description=tool_def["description"],
            tags=tool_def["tags"],
            status=ToolStatus.IMPLEMENTED,
            requester_id=system_user.id,
            approved_by_id=system_user.id,
            usage_instructions=tool_def["usage_instructions"],
            example_code=tool_def["example_code"],
            required_environment_variables=tool_def["required_environment_variables"],
            integration_complexity=tool_def["integration_complexity"],
            cost_model=tool_def["cost_model"],
            cost_details=tool_def["cost_details"],
            strengths=tool_def["strengths"],
            weaknesses=tool_def["weaknesses"],
            best_use_cases=tool_def["best_use_cases"],
            external_documentation_url=tool_def["external_documentation_url"],
            version=tool_def["version"],
            priority=tool_def["priority"],
            input_schema=tool_def.get("input_schema"),
            timeout_seconds=tool_def.get("timeout_seconds", 30),
            approved_at=datetime.utcnow(),
            implemented_at=datetime.utcnow()
        )
        db.add(tool)
        print(f"  ✅ Created: {tool_def['name']}")
        return tool
    
    else:
        # API key not available, skip
        print(f"  ⏭️  Skipped (API key not configured): {tool_def['name']}")
        return None


async def initialize_tools_catalog():
    """Initialize the tools catalog based on available API keys."""
    
    print("=" * 70)
    print("🛠️  Money Agents - Tools Catalog Initialization")
    print("=" * 70)
    print()
    
    # Get database session
    async with get_session_maker()() as db:
        try:
            # Get or create system user
            print("📋 Checking system user...")
            system_user = await get_or_create_system_user(db)
            print(f"   System user: {system_user.email}")
            print()
            
            # Check available API keys
            print("🔑 Checking API keys...")
            print(f"   Z.ai API Key: {'✅ Configured' if settings.z_ai_api_key else '❌ Missing'}")
            print(f"   Anthropic API Key: {'✅ Configured' if settings.anthropic_api_key else '❌ Missing'}")
            print(f"   OpenAI API Key: {'✅ Configured' if settings.openai_api_key else '❌ Missing'}")
            print(f"   Ollama (Local LLM): {'✅ Enabled' if settings.use_ollama else '❌ Disabled'}")
            print(f"   Serper API Key: {'✅ Configured' if settings.serper_api_key else '❌ Missing'}")
            if settings.use_serper_clone:
                print(f"   Serper Clone: ✅ {settings.serper_clone_url}")
            print(f"   ElevenLabs API Key: {'✅ Configured' if settings.elevenlabs_api_key else '❌ Missing'}")
            print(f"   Suno Enabled: {'✅ Yes' if settings.use_suno else '❌ No'}")
            print()
            
            # Validate required keys - Ollama counts as a valid LLM provider
            has_llm = settings.z_ai_api_key or settings.anthropic_api_key or settings.openai_api_key or settings.use_ollama
            has_search = settings.serper_api_key
            
            if not has_llm:
                print("❌ ERROR: No LLM provider configured!")
                print("   At least one of the following is required:")
                print("   - Z_AI_API_KEY (preferred)")
                print("   - ANTHROPIC_API_KEY")
                print("   - OPENAI_API_KEY")
                print("   - USE_OLLAMA=true (local LLM)")
                return
            
            if not has_search:
                print("⚠️  WARNING: SERPER_API_KEY not configured!")
                print("   Web search is required for Opportunity Scout agent.")
                print("   System will have limited functionality.")
                print()
            
            # Process each tool
            print("🔨 Processing tools...")
            print()
            
            for tool_key, tool_def in TOOL_DEFINITIONS.items():
                is_available = tool_def["condition"]()
                await create_or_update_tool(db, system_user, tool_def, is_available)
            
            await db.commit()
            print()
            print()
            print("=" * 70)
            print("✅ Tools catalog initialization complete!")
            print("=" * 70)
            
        except Exception as e:
            await db.rollback()
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            await db.close()


if __name__ == "__main__":
    print("\n")
    asyncio.run(initialize_tools_catalog())
    print("\n")
