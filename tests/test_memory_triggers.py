"""记忆触发：_inject_memory_triggers 的关键词匹配与宁缺毋滥策略验证。"""

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


def test_trigger_on_self_evolution_with_roadmap(monkeypatch):
    """用户说'自进化路线' → 命中有 self+evolution 和 evolution+roadmap 的记忆。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "2026-06-16 诊断「越用越懂」的三个差距。",
                       fingerprint="self_evolution_diagnosis")
        _make_mem_file(mem_dir, "evolution-roadmap-2026-06-16",
                       "自进化路线：先做最基础的闭环。",
                       fingerprint="evolution_roadmap")
        _make_mem_file(mem_dir, "unrelated-memory",
                       "跟自进化无关的内容。")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "自进化路线")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 1, f"应触发 1 条，实际 {len(triggers)}: {triggers}"
        content = triggers[0]["content"]
        assert "self-evolution-three-gaps" in content
        assert "evolution-roadmap" in content
        assert "unrelated" not in content


def test_no_trigger_on_irrelevant_input(monkeypatch):
    """无关输入 → 不触发（宁缺毋滥）。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis")
        _make_mem_file(mem_dir, "user-profile",
                       "用户偏好档案。")

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "今天天气怎么样")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 0, f"无关输入不应触发: {triggers}"


def test_trigger_clears_previous(monkeypatch):
    """每轮清除上一轮的 _memory_trigger 消息。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        _make_mem_file(mem_dir, "self-evolution-three-gaps-2026-06-16",
                       "越用越懂的三个差距。",
                       fingerprint="self_evolution_diagnosis")

        ctx = _FakeCtx()
        ctx.log.append({"role": "system", "content": "旧的触发", "_memory_trigger": True})

        _inject_memory_triggers(ctx, "自进化")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 1, f"应只有 1 条（旧已被清），实际 {len(triggers)}"
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
        assert len(triggers) == 0, f"单关键词命中不应触发: {triggers}"


def test_empty_memory_dir_no_error(monkeypatch):
    """空记忆目录 → 不报错。"""
    with tempfile.TemporaryDirectory() as td:
        mem_dir = Path(td)
        monkeypatch.setattr("src.tools.memory._dir", lambda: mem_dir)

        ctx = _FakeCtx()
        _inject_memory_triggers(ctx, "自进化")

        triggers = [m for m in ctx.log if m.get("_memory_trigger")]
        assert len(triggers) == 0
