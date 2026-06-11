"""测试 /self 的 params 视图：列出各域旋钮当前值 + env 覆盖情况。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.self import build_self_portrait


def test_params_scope_lists_all_domains():
    out = build_self_portrait("params")
    for domain in ("CONTEXT", "RAG", "EVOLUTION", "RUNTIME"):
        assert domain in out
    assert "compact_threshold = 0.8" in out
    assert "compact_keep_ratio = 0.5" in out
    assert "default_top_k = 5" in out
    assert "max_retries = 3" in out


def test_params_scope_shows_no_override_by_default():
    out = build_self_portrait("params")
    assert "env 覆盖中" in out


def test_params_scope_reports_env_override(monkeypatch):
    """设了 CTG_* 后，params 视图把它列入『env 覆盖中』。"""
    monkeypatch.setenv("CTG_COMPACT_THRESHOLD", "0.5")
    out = build_self_portrait("params")
    assert "CTG_COMPACT_THRESHOLD" in out


def test_full_scope_includes_params():
    out = build_self_portrait("full")
    assert "可调旋钮" in out
