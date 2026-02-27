"""Tests for GPU configuration parsing in config.py.

Tests _parse_gpu_indices(), GPU affinity properties, and comfyui_tools_list parsing.
"""
import pytest
from unittest.mock import patch

from app.core.config import _parse_gpu_indices, Settings


# =============================================================================
# _parse_gpu_indices() — pure function tests
# =============================================================================


class TestParseGpuIndices:
    """Test the _parse_gpu_indices helper function."""

    def test_single_gpu_zero(self):
        assert _parse_gpu_indices("0") == [0]

    def test_single_gpu_nonzero(self):
        assert _parse_gpu_indices("2") == [2]

    def test_multiple_gpus(self):
        assert _parse_gpu_indices("0,1") == [0, 1]

    def test_multiple_gpus_unordered(self):
        assert _parse_gpu_indices("2,0,1") == [2, 0, 1]

    def test_whitespace_handling(self):
        assert _parse_gpu_indices(" 0 , 1 , 2 ") == [0, 1, 2]

    def test_empty_string(self):
        assert _parse_gpu_indices("") == []

    def test_trailing_comma(self):
        assert _parse_gpu_indices("0,1,") == [0, 1]

    def test_leading_comma(self):
        assert _parse_gpu_indices(",0,1") == [0, 1]

    def test_double_comma(self):
        assert _parse_gpu_indices("0,,1") == [0, 1]

    def test_non_digit_ignored(self):
        """Non-digit entries should be silently ignored."""
        assert _parse_gpu_indices("0,abc,1") == [0, 1]

    def test_negative_ignored(self):
        """Negative indices are not digits, should be ignored."""
        assert _parse_gpu_indices("-1,0,1") == [0, 1]

    def test_large_index(self):
        assert _parse_gpu_indices("7") == [7]


# =============================================================================
# GPU Affinity Properties
# =============================================================================


class TestGpuAffinityProperties:
    """Test Settings.gpu_*_indices properties."""

    def _make_settings(self, **overrides):
        """Create a Settings instance with test defaults."""
        defaults = {
            "secret_key": "test-secret",
            "database_url": "sqlite:///test.db",
        }
        defaults.update(overrides)
        with patch.dict("os.environ", defaults, clear=False):
            return Settings(**defaults)

    def test_default_gpu_ollama_indices(self):
        s = self._make_settings()
        assert s.gpu_ollama_indices == [0]

    def test_default_gpu_acestep_indices(self):
        s = self._make_settings()
        assert s.gpu_acestep_indices == [0]

    def test_default_gpu_qwen3_tts_indices(self):
        s = self._make_settings()
        assert s.gpu_qwen3_tts_indices == [0]

    def test_default_gpu_zimage_indices(self):
        s = self._make_settings()
        assert s.gpu_zimage_indices == [0]

    def test_custom_gpu_ollama_multi(self):
        s = self._make_settings(gpu_ollama="0,1")
        assert s.gpu_ollama_indices == [0, 1]

    def test_custom_gpu_zimage_second_gpu(self):
        s = self._make_settings(gpu_zimage="1")
        assert s.gpu_zimage_indices == [1]

    def test_custom_gpu_acestep_three_gpus(self):
        s = self._make_settings(gpu_acestep="0,2,4")
        assert s.gpu_acestep_indices == [0, 2, 4]


# =============================================================================
# comfyui_tools_list Property
# =============================================================================


class TestComfyuiToolsList:
    """Test Settings.comfyui_tools_list parsing."""

    def _make_settings(self, **overrides):
        defaults = {
            "secret_key": "test-secret",
            "database_url": "sqlite:///test.db",
        }
        defaults.update(overrides)
        with patch.dict("os.environ", defaults, clear=False):
            return Settings(**defaults)

    def test_empty_string_returns_empty_list(self):
        s = self._make_settings(comfyui_tools="")
        assert s.comfyui_tools_list == []

    def test_single_tool(self):
        s = self._make_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0"
        )
        result = s.comfyui_tools_list
        assert len(result) == 1
        assert result[0]["slug"] == "comfy-ltx-2"
        assert result[0]["name"] == "ltx-2"
        assert result[0]["display_name"] == "LTX-2 Video"
        assert result[0]["port"] == 9902
        assert result[0]["comfyui_url"] == "http://localhost:8189"
        assert result[0]["gpu_indices"] == [0]

    def test_multiple_tools_semicolon_separated(self):
        s = self._make_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0;wan-video|WAN Video|9903|http://localhost:8190|1"
        )
        result = s.comfyui_tools_list
        assert len(result) == 2
        assert result[0]["slug"] == "comfy-ltx-2"
        assert result[0]["gpu_indices"] == [0]
        assert result[1]["slug"] == "comfy-wan-video"
        assert result[1]["gpu_indices"] == [1]

    def test_multi_gpu_tool(self):
        """A ComfyUI tool can span multiple GPUs."""
        s = self._make_settings(
            comfyui_tools="big-render|Big Render|9904|http://localhost:8189|0,1,2"
        )
        result = s.comfyui_tools_list
        assert len(result) == 1
        assert result[0]["gpu_indices"] == [0, 1, 2]

    def test_trailing_semicolon(self):
        s = self._make_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0;"
        )
        result = s.comfyui_tools_list
        assert len(result) == 1

    def test_whitespace_in_entries(self):
        s = self._make_settings(
            comfyui_tools=" ltx-2 | LTX-2 Video | 9902 | http://localhost:8189 | 0 "
        )
        result = s.comfyui_tools_list
        assert len(result) == 1
        assert result[0]["name"] == "ltx-2"
        assert result[0]["display_name"] == "LTX-2 Video"
        assert result[0]["port"] == 9902

    def test_too_few_parts_skipped(self):
        """Entries with fewer than 5 pipe-separated parts are silently skipped."""
        s = self._make_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189"
        )
        result = s.comfyui_tools_list
        assert len(result) == 0

    def test_shared_comfyui_server(self):
        """Two tools sharing the same ComfyUI server (different wrapper ports)."""
        s = self._make_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8189|0"
        )
        result = s.comfyui_tools_list
        assert len(result) == 2
        assert result[0]["comfyui_url"] == result[1]["comfyui_url"]
        assert result[0]["port"] != result[1]["port"]
