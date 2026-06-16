"""器官生命体征 census 测试 — 派生自产物、非探针的衰竭检测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.organs as organs


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nname: x\nmetadata:\n  type: strategy\n---\nbody\n", encoding="utf-8")
    import os
    os.utime(p, (mtime, mtime))


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    """把 organs 模块的产物目录指向临时空目录，隔离真实 stats/memory/sessions。"""
    stats = tmp_path / "stats"
    mem = tmp_path / "memory"
    sess = tmp_path / "sessions"
    for d in (stats, mem, sess):
        d.mkdir()
    monkeypatch.setattr(organs, "_STATS_DIR", stats)
    monkeypatch.setattr(organs, "_MEMORY", mem)
    monkeypatch.setattr(organs, "_SESSIONS", sess)
    return tmp_path, stats, mem, sess


# ── _sessions_ago：核心心跳算法 ──

def test_sessions_ago_zero_when_artifact_newest():
    """产物比所有会话都新 → 0（本会话刚跳过）。"""
    timeline = [100.0, 90.0, 80.0]
    assert organs._sessions_ago(150.0, timeline) == 0


def test_sessions_ago_counts_newer_sessions():
    """有 2 个会话比产物新 → 2 个会话没跳了。"""
    timeline = [100.0, 90.0, 80.0, 70.0]
    assert organs._sessions_ago(85.0, timeline) == 2


def test_sessions_ago_minus_one_when_never():
    """从无产物 → -1。"""
    assert organs._sessions_ago(None, [100.0]) == -1


# ── _memory_by_type：按 frontmatter type 过滤 + 排除前缀 ──

def test_memory_by_type_filters_and_excludes_prefix(iso_dirs):
    _, _stats, mem, _sess = iso_dirs
    _touch(mem / "lesson-a.md", 100.0)            # type=strategy
    _touch(mem / "pin-b.md", 100.0)               # type=strategy 但 pin- 前缀
    (mem / "MEMORY.md").write_text("index", encoding="utf-8")
    (mem / "user.md").write_text(
        "---\nname: u\nmetadata:\n  type: user\n---\nx\n", encoding="utf-8")

    strat = organs._memory_by_type("strategy", exclude_prefix="pin-")
    names = {p.name for p in strat}
    assert names == {"lesson-a.md"}, "只 strategy、排除 pin-、排除 MEMORY.md/user"


# ── census + render：端到端，衰竭器官能被标红 ──

def test_census_flags_silent_organ(iso_dirs):
    """造 3 个会话(新) + 一个很旧的产物 → 该器官 sessions_ago>0 标红。"""
    _, stats, mem, sess = iso_dirs
    # 三个会话存档（新）
    for i, t in enumerate([300.0, 310.0, 320.0]):
        f = sess / f"sess-{i}"
        f.write_text("{}", encoding="utf-8")
        import os
        os.utime(f, (t, t))
    # 用户档案很旧（3 个会话都比它新）
    prof = mem / "user-profile.md"
    prof.write_text("---\nname: user-profile\nmetadata:\n  type: user\n---\nx\n",
                    encoding="utf-8")
    import os
    os.utime(prof, (100.0, 100.0))

    vitals = {o.name: o for o in organs.census()}
    assert vitals["用户档案"].sessions_ago == 3
    assert vitals["会话存档"].sessions_ago == 0     # 会话存档自己永远是最新心跳


def test_census_never_fired_is_minus_one(iso_dirs):
    """无任何产物的器官 sessions_ago=-1（从未）。"""
    vitals = {o.name: o for o in organs.census()}
    assert vitals["用户档案"].last is None
    assert vitals["用户档案"].sessions_ago == -1


def test_render_contains_markers_and_baseline(iso_dirs):
    out = organs.render_census()
    assert "器官生命体征" in out
    assert "心跳基准" in out
    # 空目录下所有器官从未跳 → 全红
    assert "🔴" in out


def test_render_is_readonly(iso_dirs):
    """只读保证：render 不写任何文件。"""
    tmp, stats, mem, sess = iso_dirs
    before = set(tmp.rglob("*"))
    organs.render_census()
    after = set(tmp.rglob("*"))
    assert before == after, "census/render 不得产生任何新文件"
