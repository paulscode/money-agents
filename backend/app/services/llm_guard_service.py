"""
LLM Guard integration for ML-based prompt injection detection.

Provides a singleton scanner that lazy-loads the protectai/deberta-v3-base
prompt injection classification model on first use. This adds a neural-network
layer of defense on top of the existing regex-based sanitization.

The scanner is **optional** — if the ``llm-guard`` package is not installed or
model loading fails, all public functions degrade gracefully and return
"no threat detected" results so the rest of the pipeline keeps working.

Usage:
    from app.services.llm_guard_service import scan_prompt, is_available

    if is_available():
        result = scan_prompt(text)
        if not result.is_safe:
            log.warning("ML scanner flagged input: score=%.2f", result.score)

Configuration via environment variables:
    LLM_GUARD_ENABLED   – "true" to enable (default "true" when package installed)
    LLM_GUARD_THRESHOLD – float injection score threshold (default 0.90)
    LLM_GUARD_USE_ONNX  – "true" to use ONNX runtime for faster inference

See: internal_docs/PROMPT_INJECTION_AUDIT.md
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.injection")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ENV_ENABLED = os.getenv("LLM_GUARD_ENABLED", "true").lower() in ("true", "1", "yes")
_ENV_THRESHOLD = float(os.getenv("LLM_GUARD_THRESHOLD", "0.90"))
_ENV_USE_ONNX = os.getenv("LLM_GUARD_USE_ONNX", "false").lower() in ("true", "1", "yes")
# GAP-13: When True, scanner unavailability or errors cause content to be
# treated as unsafe instead of passing through (fail-closed vs fail-open).
_ENV_FAIL_CLOSED = os.getenv("LLM_GUARD_FAIL_CLOSED", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanResult:
    """Result of an LLM Guard prompt injection scan."""

    is_safe: bool
    """True if the content is considered safe (below threshold)."""

    score: float
    """Injection risk score, 0.0 (safe) → 1.0 (certain injection).
    Returns -1.0 when the scanner is unavailable."""

    scanner_available: bool
    """Whether the ML scanner was actually used for this result."""


# Sentinel for "scanner not available"
_SAFE_FALLBACK = ScanResult(is_safe=True, score=-1.0, scanner_available=False)
# GAP-13: Fail-closed sentinel — used when LLM_GUARD_FAIL_CLOSED=true
_BLOCKED_FALLBACK = ScanResult(is_safe=False, score=1.0, scanner_available=False)


# ---------------------------------------------------------------------------
# Lazy-loaded singleton scanner
# ---------------------------------------------------------------------------

class _ScannerHolder:
    """Thread-safe lazy-loaded holder for the PromptInjection scanner."""

    def __init__(self) -> None:
        self._scanner = None  # type: ignore[assignment]
        self._lock = threading.Lock()
        self._initialized = False
        self._available = False

    @property
    def available(self) -> bool:
        """Return True if the scanner has been successfully loaded."""
        return self._available

    def get(self):
        """Return the scanner instance, initializing it on first call.

        Returns None if the package is missing or model loading fails.
        """
        if self._initialized:
            return self._scanner

        with self._lock:
            # Double-check after acquiring lock
            if self._initialized:
                return self._scanner

            self._scanner = self._try_load()
            self._available = self._scanner is not None
            self._initialized = True
            return self._scanner

    @staticmethod
    def _try_load():
        """Attempt to import llm-guard and instantiate the scanner."""
        if not _ENV_ENABLED:
            logger.info("LLM Guard disabled via LLM_GUARD_ENABLED=false")
            return None

        try:
            from llm_guard.input_scanners import PromptInjection
            from llm_guard.input_scanners.prompt_injection import MatchType

            scanner = PromptInjection(
                threshold=_ENV_THRESHOLD,
                match_type=MatchType.FULL,
                use_onnx=_ENV_USE_ONNX,
            )
            logger.info(
                "LLM Guard PromptInjection scanner loaded "
                "(threshold=%.2f, onnx=%s)",
                _ENV_THRESHOLD,
                _ENV_USE_ONNX,
            )
            return scanner

        except ImportError:
            logger.info(
                "llm-guard package not installed — ML prompt injection "
                "scanning disabled. Install with: pip install llm-guard"
            )
            return None
        except Exception:
            logger.exception(
                "Failed to load LLM Guard scanner — ML scanning disabled"
            )
            return None


_holder = _ScannerHolder()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if the LLM Guard scanner is loaded and ready."""
    # Trigger lazy init so the value is current
    _holder.get()
    return _holder.available


def scan_prompt(text: str, source: str = "unknown") -> ScanResult:
    """
    Scan text for prompt injection using the ML model.

    This is the primary entry point. It returns a ``ScanResult`` with:
    - ``is_safe``  — False if the model believes text is an injection attempt
    - ``score``    — 0.0–1.0 risk score
    - ``scanner_available`` — whether the ML model was actually used

    If the scanner is unavailable, returns a safe fallback so callers can
    always check ``result.is_safe`` without branching on availability.

    Args:
        text: Content to scan (user input, tool output, search result, etc.)
        source: Label for logging (e.g., "web_search", "nostr_post")

    Returns:
        ScanResult
    """
    if not text or not text.strip():
        return ScanResult(is_safe=True, score=0.0, scanner_available=_holder.available)

    scanner = _holder.get()
    if scanner is None:
        if _ENV_FAIL_CLOSED:
            security_logger.warning(
                "LLM_GUARD_FAIL_CLOSED | scanner unavailable, blocking "
                "content from source=%s",
                source,
            )
            return _BLOCKED_FALLBACK
        return _SAFE_FALLBACK

    try:
        _sanitized, valid, risk_score = scanner.scan(text)
        is_safe = bool(valid)

        if not is_safe:
            security_logger.warning(
                "LLM_GUARD_INJECTION | source=%s | score=%.3f | "
                "threshold=%.2f | preview=%s",
                source,
                risk_score,
                _ENV_THRESHOLD,
                text[:200].replace("\n", " "),
            )

        return ScanResult(is_safe=is_safe, score=risk_score, scanner_available=True)

    except Exception:
        logger.exception("LLM Guard scan failed for source=%s", source)
        if _ENV_FAIL_CLOSED:
            security_logger.warning(
                "LLM_GUARD_FAIL_CLOSED | scan error, blocking "
                "content from source=%s",
                source,
            )
            return _BLOCKED_FALLBACK
        # Fail open — don't block the pipeline on scanner errors
        return _SAFE_FALLBACK


def scan_output(text: str, source: str = "unknown") -> ScanResult:
    """
    Scan LLM output for injected content that may have passed through.

    Uses the same model as ``scan_prompt`` but called on LLM responses
    to detect if the model was successfully manipulated into producing
    injection-like content.

    Args:
        text: LLM-generated output text
        source: Label for logging

    Returns:
        ScanResult
    """
    # The PromptInjection scanner works on both inputs and outputs
    return scan_prompt(text, source=f"output:{source}")


def reset() -> None:
    """Reset the scanner (for testing). Forces re-initialization on next call."""
    global _holder
    _holder = _ScannerHolder()
