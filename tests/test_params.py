"""测试 params.py：按域 frozen dataclass、env 可覆盖、各模块单一来源。"""

import importlib
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.params as params


def test_context_is_frozen():
    """旋钮只读：防止运行时被意外改写。"""
    with pytest.raises(FrozenInstanceError):
        params.CONTEXT.compact_threshold = 0.9


def test_context_defaults():
    c = params.CONTEXT
    assert c.max_context_tokens == 960_000
    assert c.compact_threshold == 0.65
    assert c.cleanup_threshold == 0.60
    assert c.cleanup_min_tool_results == 2


def test_config_and_llm_source_from_params():
    """config / llm 的旧名值 == params 单一来源（没有第二处真值）。"""
    import src.config as config
    import src.llm as llm
    assert config.MAX_CONTEXT_TOKENS == params.CONTEXT.max_context_tokens
    assert config.TOOL_LOOP_THRESHOLD == params.CONTEXT.tool_loop_threshold
    assert llm._COMPACT_THRESHOLD == params.CONTEXT.compact_threshold
    assert llm._CLEANUP_CONTEXT_THRESHOLD == params.CONTEXT.cleanup_threshold


def test_env_override(monkeypatch):
    """CTG_* 环境变量覆盖默认值。"""
    monkeypatch.setenv("CTG_COMPACT_THRESHOLD", "0.5")
    monkeypatch.setenv("CTG_CLEANUP_MIN_TOOL_RESULTS", "7")
    reloaded = importlib.reload(params)
    try:
        assert reloaded.CONTEXT.compact_threshold == 0.5
        assert reloaded.CONTEXT.cleanup_min_tool_results == 7
    finally:
        # 复原，避免污染同进程后续测试（reload 回干净 env）
        monkeypatch.delenv("CTG_COMPACT_THRESHOLD", raising=False)
        monkeypatch.delenv("CTG_CLEANUP_MIN_TOOL_RESULTS", raising=False)
        importlib.reload(params)
