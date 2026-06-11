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
    assert c.compact_threshold == 0.80
    assert c.compact_keep_ratio == 0.50


def test_config_and_llm_source_from_params():
    """Config / llm 的旧名值 == params 单一来源（没有第二处真值）。"""
    import src.config as config
    import src.llm as llm
    assert params.CONTEXT.max_context_tokens == config.MAX_CONTEXT_TOKENS
    assert params.CONTEXT.tool_loop_threshold == config.TOOL_LOOP_THRESHOLD
    assert params.CONTEXT.compact_threshold == llm._COMPACT_THRESHOLD
    assert params.CONTEXT.compact_keep_ratio == llm._COMPACT_KEEP_RATIO


def test_rag_and_evolution_defaults():
    assert params.RAG.default_top_k == 5
    assert params.RAG.weight_name == 3.0
    assert params.RAG.max_file_size == 512 * 1024
    assert params.EVOLUTION.git_timeout_seconds == 10
    assert params.EVOLUTION.require_clean is False


def test_rag_and_evolution_wired_to_modules():
    """rag.py / evolution_runner.py / config 的旧名 == params 单一来源。"""
    import src.config as config
    import src.evolution_runner as er
    import src.tools.rag as rag
    assert params.RAG.default_top_k == rag.DEFAULT_TOP_K
    assert params.RAG.weight_name == rag.WEIGHT_NAME
    assert params.RAG.max_file_size == rag.MAX_FILE_SIZE
    assert params.EVOLUTION.git_timeout_seconds == er.GIT_TIMEOUT_SECONDS
    assert params.EVOLUTION.require_clean == config.EVOLVE_REQUIRE_CLEAN


def test_runtime_defaults_and_wiring():
    assert params.RUNTIME.max_retries == 3
    assert params.RUNTIME.max_exec_timeout == 5
    assert params.RUNTIME.token_per_char_cjk == 0.6
    assert params.RUNTIME.token_per_char_other == 0.3
    assert params.RUNTIME.tool_result_compress_threshold == 1200
    assert params.RUNTIME.max_requests_per_turn == 60
    import src.config as config
    import src.llm as llm
    assert params.RUNTIME.max_retries == config.MAX_RETRIES
    assert params.RUNTIME.max_exec_timeout == config.MAX_EXEC_TIMEOUT
    assert params.RUNTIME.tool_result_budget == config.TOOL_RESULT_BUDGET
    assert params.RUNTIME.token_per_char_cjk == config.TOKEN_PER_CHAR_CJK
    assert params.RUNTIME.token_per_char_other == config.TOKEN_PER_CHAR_OTHER
    assert params.RUNTIME.max_requests_per_turn == llm._MAX_REQUESTS_PER_TURN
    # 原 llm.py 内联 magic number 现绑定到 params.RUNTIME
    assert params.RUNTIME.tool_result_compress_threshold == llm._TOOL_RESULT_COMPRESS_THRESHOLD


def test_env_override(monkeypatch):
    """CTG_* 环境变量覆盖默认值。"""
    monkeypatch.setenv("CTG_COMPACT_THRESHOLD", "0.5")
    monkeypatch.setenv("CTG_MAX_CONTEXT_TOKENS", "500000")
    reloaded = importlib.reload(params)
    try:
        assert reloaded.CONTEXT.compact_threshold == 0.5
        assert reloaded.CONTEXT.max_context_tokens == 500000
    finally:
        # 复原，避免污染同进程后续测试（reload 回干净 env）
        monkeypatch.delenv("CTG_COMPACT_THRESHOLD", raising=False)
        monkeypatch.delenv("CTG_MAX_CONTEXT_TOKENS", raising=False)
        importlib.reload(params)
