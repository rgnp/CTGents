"""self.py 自画像保真 + 树渲染回归。

self.py 是 agent 的自我认知，静态描述漂移=agent 在错误自我认知上运转。
两个回归：
1. 记忆系统描述曾虚构"RAG 混合检索+时间衰减评分"，实际是子串匹配；且 files
   误写成 Claude Code 的 ~/.claude 路径。
2. _render_tree 的 is_last 用未过滤的 entries 长度算 → 末尾项被跳时错配连接符。
"""
from __future__ import annotations

from src.tools.self import _render_tree, build_self_portrait

# ── 自画像保真：记忆描述必须匹配实际行为 ────────────────────

def test_portrait_no_fictional_memory_scoring():
    """不得再出现虚构的时间衰减评分公式。"""
    out = build_self_portrait("capabilities")
    assert "时间衰减评分" not in out, "自画像仍在虚构记忆评分"
    assert "相似度×0.6" not in out


def test_portrait_memory_path_not_claude():
    """记忆路径不得指向 Claude Code 的 ~/.claude 目录。"""
    out = build_self_portrait("capabilities")
    assert "~/.claude" not in out, "记忆路径误写成 Claude 的目录"


def test_portrait_describes_actual_recall():
    """应如实描述 recall 是子串匹配（非语义检索）。"""
    out = build_self_portrait("capabilities")
    assert "子串" in out and "非语义检索" in out


# ── _render_tree：先过滤再判 is_last ────────────────────────

def test_render_tree_skipped_last_entry_uses_correct_connector(tmp_path):
    """末尾是被跳过的 __pycache__ 时，最后可见项应是 └── 而非 ├──。"""
    # "Zoo"(0x5A) 排在 "__pycache__"(0x5F) 前 → __pycache__ 是排序末项且被跳过
    (tmp_path / "Zoo").mkdir()
    (tmp_path / "__pycache__").mkdir()
    lines = _render_tree(tmp_path, prefix="", root=str(tmp_path))
    joined = "\n".join(lines)
    assert "└── Zoo" in joined, f"最后可见项连接符错: {lines}"
    assert "├── Zoo" not in joined
    assert "__pycache__" not in joined  # 仍被跳过


def test_render_tree_skips_dotfiles(tmp_path):
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("y", encoding="utf-8")
    joined = "\n".join(_render_tree(tmp_path, prefix="", root=str(tmp_path)))
    assert "keep.py" in joined and ".hidden" not in joined
