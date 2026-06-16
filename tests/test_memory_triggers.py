"""记忆触发：_inject_memory_triggers 的关键词匹配、两级输出、宁缺毋滥策略验证。"""

import tempfile
from pathlib import Path

from src.main import _inject_memory_triggers


class _FakeCtx:
    """最小 CacheContext 替身：只需要 log 列表和 prefix 属性。"""

    def __init__(self):
        self.log = []
        self.prefix = []


def _make_mem_file(mem_dir: Path, name: str, content: str,
                   fingerprint: str = "", mem_type: str = "knowledge") -> None:
    """在临时记忆目录中创建一个符合格式的 .md 文件。"""
    first = content.split("。")[0].split("\n")[0].strip()
    desc = first[:30] if first else content[:30]
    meta_lines = [
        f"name: {name}",
        f"description: {desc}",
        "metadata:",
        f"  type: {mem_type}",
        "  updated: 2026-06-16T00:00:00Z",
    ]
    if fingerprint:
        meta_lines.append(f"  fingerprint: {fingerprint}")
    text = "---\n" + "\n".join(meta_lines) + f"\n---\n\n{content}\n"
    (mem_dir / f"{name}.md").write_text(text, encoding="utf-8")


def test_knowledge_trigger_hint(monkeypatch):
    """知识型记忆触发 → 一行摘要提示。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis", mem_type="knowledge")
        _make_mem_file(mem_dir, "evolution-roadmap-2026-06-16",
                       "自进化路线：先做基础闭环。",
                       fingerprint="evolution_roadmap", mem_type="knowledge")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "自进化路线")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 1
        content = triggers[0]["content"]
        assert "self-evolution-three-gaps" in content
        assert "evolution-roadmap" in content
        assert "[记忆触发]" in content


def test_strategy_trigger_constraint(monkeypatch):
    """策略型记忆触发 → 可执行约束模板，非摘要提示。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "three-systematic-errors-2026-06-16",
                       "三个系统性问题：调研、行编辑、异步。",
                       fingerprint="systematic_errors", mem_type="strategy")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "系统性错误 编辑代码 调研")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) >= 1
        content = triggers[0]["content"]
        assert "[约束]" in content
        assert "执行前必查" in content


def test_no_trigger_on_irrelevant_input(monkeypatch):
    """无关输入 → 不触发（宁缺毋滥）。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "今天天气怎么样")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 0


def test_trigger_clears_previous(monkeypatch):
    """每轮清除上一轮的 _memory_trigger 消息。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis", mem_type="knowledge")

        ctx = _FakeCtx()
        ctx.log.append({"role": "system", "content": "旧的触发", "_memory_trigger": True})

        _inject_memory_triggers(ctx, "自进化")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 1
        assert "旧的触发" not in triggers[0]["content"]


def test_no_trigger_below_threshold(monkeypatch):
    """单关键词命中（匹配数 < 2）→ 不触发。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "user-profile",
                       "用户偏好档案。",
                       mem_type="user")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "用户的偏好是什么")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 0


def test_empty_memory_dir_no_error(monkeypatch):
    """空记忆目录 → 不报错。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "自进化")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 0


def test_mixed_strategy_and_knowledge(monkeypatch):
    """同时命中策略型+知识型 → 两路输出都注入。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "three-systematic-errors-2026-06-16",
                       "系统性问题。",
                       fingerprint="systematic_errors", mem_type="strategy")
        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis", mem_type="knowledge")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "系统性错误 进化 诊断")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) >= 2
        contents = " ".join(m["content"] for m in triggers)
        assert "[约束]" in contents
        assert "[记忆触发]" in contents
