"""memory.py 核心路径回归：remember / recall / forget / get_context 索引。

此前仅 detect_signal 有测试，核心读写路径裸奔。重点回归：frontmatter 的
闭合分隔符曾用 find("---") 子串匹配，当 description/正文含 '---' 时会错位
→ type 解析为空、body 串入 metadata、索引 desc 丢失。三处解析器共用同一 bug。
"""
from __future__ import annotations

import pytest

from src import asset_usage
from src.tools import memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """把记忆目录重定向到 tmp，并重置模块级索引缓存，避免污染真实记忆。

    ARCHIVE_DIR 默认指向不存在的 tmp 子目录 → recall 跳过归档库，隔离真实 tasks/archive；
    需要测跨库召回的用例自行重指（见 test_recall_surfaces_archive_lessons）。
    """
    monkeypatch.setattr(memory, "MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(memory, "ARCHIVE_DIR", str(tmp_path / "_noarchive"))
    monkeypatch.setattr(memory, "_context_cache", None)
    monkeypatch.setattr(memory, "_context_dirty", True)
    monkeypatch.setattr(asset_usage, "USAGE_FILE", tmp_path / "asset-usage.jsonl")
    monkeypatch.setattr(asset_usage, "_current_session_id", lambda: "test-session")
    monkeypatch.setattr(asset_usage, "current_task_key", lambda text=None: "test-task")
    # 蒸馏调用真 API 太贵且不可控 → 全部 mock 掉。要测蒸馏的用例自行 monkeypatch 覆盖。
    monkeypatch.setattr(memory, "_distill", lambda _content: [])
    return memory


# ── characterization：锁住正常往返 ──────────────────────────

def test_remember_then_recall(mem):
    mem.execute("remember", {"name": "foo-bar", "content": "用户偏好简短回答", "type": "user"})
    out = mem.execute("recall", {"query": "简短"})
    assert "foo-bar" in out
    assert "[user]" in out


def test_recall_records_returned_asset_and_explicit_adoption(mem):
    mem.execute("remember", {"name": "rollback-rule", "content": "失败时先回滚", "type": "strategy"})
    out = mem.execute("recall", {"query": "失败 回滚"})
    assert "调用 adopt_asset" in out
    retrieved = [
        event for event in asset_usage._read_events() if event.stage == "retrieved"
    ]
    assert [event.asset_id for event in retrieved] == ["rollback-rule"]

    adopted = mem.execute(
        "adopt_asset",
        {
            "asset_kind": "memory",
            "asset_id": "rollback-rule",
            "purpose": "决定本次失败恢复策略",
        },
    )
    assert adopted.startswith("✅")


def test_feedback_asset_routes_to_completed_adoption(mem):
    mem.execute("remember", {"name": "review-rule", "content": "先验证再完成", "type": "strategy"})
    mem.execute("recall", {"query": "验证 完成"})
    mem.execute(
        "adopt_asset",
        {
            "asset_kind": "memory",
            "asset_id": "review-rule",
            "purpose": "决定任务完成门",
        },
    )
    asset_usage.record_task_outcome("passed", "acceptance passed")

    result = mem.execute(
        "feedback_asset",
        {
            "asset_kind": "memory",
            "asset_id": "review-rule",
            "verdict": "helpful",
            "reason": "阻止了未验证完成",
        },
    )

    assert result.startswith("✅")
    assert "显式 helpful 1 次" in mem.execute("memory_audit", {})


def test_remember_rebuilds_index(mem):
    mem.execute("remember", {"name": "a-note", "content": "某条知识", "type": "knowledge"})
    ctx = mem.get_context()
    assert ctx is not None and "a-note" in ctx


def test_remember_overwrites_same_name(mem):
    mem.execute("remember", {"name": "dup", "content": "旧内容", "type": "user"})
    mem.execute("remember", {"name": "dup", "content": "新内容 fresh_token", "type": "user"})
    out = mem.execute("recall", {"query": "fresh_token"})
    assert "dup" in out
    assert "旧内容" not in out


def test_same_name_update_tracks_revision_and_preserves_identity(mem):
    mem.execute("remember", {
        "name": "evolving",
        "content": "第一版",
        "type": "strategy",
        "fingerprint": "stable-scene",
    })
    before, _ = memory._split_frontmatter(
        (mem._dir() / "evolving.md").read_text(encoding="utf-8")
    )
    mem.execute("remember", {
        "name": "evolving",
        "content": "第二版",
        "type": "strategy",
    })
    after, _ = memory._split_frontmatter(
        (mem._dir() / "evolving.md").read_text(encoding="utf-8")
    )
    assert after["created"] == before["created"]
    assert after["revision"] == "2"
    assert after["fingerprint"] == "stable-scene"


def test_forget_removes(mem):
    mem.execute("remember", {"name": "gone", "content": "临时 disposable", "type": "user"})
    assert "已忘记" in mem.execute("forget", {"name": "gone"})
    assert "未找到" in mem.execute("recall", {"query": "disposable"})


def test_forget_missing(mem):
    assert "不存在" in mem.execute("forget", {"name": "never-existed"})


def test_recall_miss(mem):
    mem.execute("remember", {"name": "a", "content": "apple", "type": "user"})
    assert "未找到" in mem.execute("recall", {"query": "zzz-no-such-token"})


def test_empty_context(mem):
    assert mem.get_context() is None  # 无任何记忆 → None


# ── 回归：frontmatter 含 '---' 不得破坏解析（修复前失败）──────

def test_dashes_in_content_keep_type(mem):
    """正文首句含 '---' → description 含 '---'，type 仍须正确解析。"""
    mem.execute("remember", {
        "name": "dashy", "content": "--- 重要分隔，记住这个 token_xyz", "type": "strategy",
    })
    out = mem.execute("recall", {"query": "token_xyz"})
    assert "dashy" in out
    assert "[strategy]" in out, f"type 解析错位(应为 strategy): {out!r}"


def test_dashes_in_content_keep_index_desc(mem):
    """摘要含 '---' 时索引 desc 不得丢失。"""
    mem.execute("remember", {
        "name": "dashy2", "content": "--- 边界笔记 marker_q", "type": "user",
    })
    ctx = mem.get_context()
    assert "边界笔记" in ctx, f"索引 desc 因 frontmatter 错位丢失: {ctx!r}"


# ── recall 排序检索 ──────────────────────────────────────────

def test_tokenize_ascii_and_cjk_bigram():
    toks = memory._tokenize("AD科研 paper")
    assert "ad" in toks and "paper" in toks  # ASCII alnum
    assert "科研" in toks                      # 2 字 CJK → 1 bigram


def test_recall_matches_reordered_terms(mem):
    """换序也命中:存'分析论文…',搜'论文分析'(bigram 重叠,旧子串匹配做不到)。"""
    mem.execute("remember", {"name": "p", "content": "如何分析论文的方法论", "type": "knowledge"})
    out = mem.execute("recall", {"query": "论文分析"})
    assert "p" in out


def test_recall_ranks_name_match_above_body(mem):
    """字段 name 命中权重 > body:词在 name 的记忆排在仅 body 命中的之前。"""
    mem.execute("remember", {"name": "driving-research", "content": "无关内容", "type": "user"})
    mem.execute("remember", {"name": "misc", "content": "driving 只在正文里", "type": "user"})
    out = mem.execute("recall", {"query": "driving"})
    assert out.index("driving-research") < out.index("misc")


def test_recall_top_k_caps_shown(mem, monkeypatch):
    """top-K 只显示前 K 条,但总数仍如实报全。"""
    from dataclasses import replace
    monkeypatch.setattr(memory, "_PARAMS", replace(memory._PARAMS, recall_top_k=2))
    for i in range(4):
        mem.execute("remember", {"name": f"m{i}", "content": "common_tok 内容", "type": "user"})
    out = mem.execute("recall", {"query": "common_tok"})
    assert sum(1 for i in range(4) if f"m{i}" in out) == 2  # 只显示 2 条名字
    assert "找到 4 条" in out                                # 总数报全


def test_recall_exact_phrase_still_found(mem):
    """精确子串短语仍能命中(exact_bonus 保留旧语义)。"""
    mem.execute("remember", {"name": "phrase", "content": "一句完整的话 abc_def_ghi", "type": "user"})
    assert "phrase" in mem.execute("recall", {"query": "abc_def_ghi"})


def test_recall_ignores_frontmatter_structure_words(mem):
    """打分只看语义字段:frontmatter 结构词(metadata/type/'ad'∈metadata)不得命中。

    回归:旧实现对整个文件(含 frontmatter)做 exact 子串 → 'metadata'/'type'/'ad'
    误命中所有记忆。'metadata' 每个文件都有,若误匹配则永远返回全部。
    """
    mem.execute("remember", {"name": "zzz", "content": "纯净中文内容", "type": "knowledge"})
    assert "未找到" in mem.execute("recall", {"query": "metadata"})
    assert "未找到" in mem.execute("recall", {"query": "ad"})  # 'ad' ∈ 'metadata' 不算


# ── C16 缝:recall 跨库索引 tasks/archive ──────────────────────

def test_recall_surfaces_archive_lessons(mem, tmp_path, monkeypatch):
    """跨库召回:archive 里无 frontmatter 的归档教训也能被 recall 命中,标 [task]。

    缝:架构教训写在 tasks/archive(无 frontmatter),只索引 memory/ 会让它们对检索
    成"只写不读的坟场"(agent 曾因 recall 捞不到而重造已存在的机制)。
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-01-01-zxqv-audit.md").write_text(
        "# zxqv 审计 — 已完成\n落地: src/zxqv_audit.py。教训: zxqv 机制已存在,别重造。\n",
        encoding="utf-8")
    monkeypatch.setattr(memory, "ARCHIVE_DIR", str(archive))
    out = memory.execute("recall", {"query": "zxqv 审计 机制"})
    assert "zxqv-audit" in out and "[task]" in out


def test_recall_skips_archive_when_absent(mem):
    """归档目录不存在(新克隆/隔离)时 recall 不报错,只搜 memory/。"""
    assert "未找到" in memory.execute("recall", {"query": "zxqv-no-such"})


# ── fingerprint 合并（代码级兜底，治同质散成 N 条）──────────────

def test_fingerprint_merge_same_fp(mem):
    """同 fingerprint → 合并到已有文件，不新建。"""
    mem.execute("remember", {
        "name": "lesson-a", "content": "工具 A 参数总出错", "type": "strategy",
        "fingerprint": "tool_a_error",
    })
    mem.execute("remember", {
        "name": "lesson-a-v2", "content": "工具 A 参数又出错了（第二版）", "type": "strategy",
        "fingerprint": "tool_a_error",
    })
    import os
    files = os.listdir(mem._dir())
    assert "lesson-a.md" in files
    assert "lesson-a-v2.md" not in files


def test_fingerprint_merge_increments_times(mem):
    """合并时 times_encountered 递增。"""
    mem.execute("remember", {
        "name": "count-me", "content": "第一次遇到", "type": "strategy",
        "fingerprint": "count_test",
    })
    mem.execute("remember", {
        "name": "count-me-v2", "content": "第二次遇到啦", "type": "strategy",
        "fingerprint": "count_test",
    })
    path = mem._dir() / "count-me.md"
    meta, _ = memory._split_frontmatter(path.read_text(encoding="utf-8"))
    assert meta.get("times_encountered") == "2"


def test_fingerprint_merge_updates_content(mem):
    """合并后内容是新的。"""
    mem.execute("remember", {
        "name": "update-me", "content": "旧内容 old_token", "type": "strategy",
        "fingerprint": "update_test",
    })
    mem.execute("remember", {
        "name": "update-me-v2", "content": "新内容 new_token_xyz", "type": "strategy",
        "fingerprint": "update_test",
    })
    out = mem.execute("recall", {"query": "new_token_xyz"})
    assert "update-me" in out
    assert "旧内容" not in out


def test_fingerprint_merge_keeps_original_name(mem):
    """合并保留原文件名，提示使用旧名。"""
    mem.execute("remember", {
        "name": "original-name", "content": "原始内容 token_a", "type": "strategy",
        "fingerprint": "keep_name_test",
    })
    result = mem.execute("remember", {
        "name": "new-name", "content": "新内容 token_b", "type": "strategy",
        "fingerprint": "keep_name_test",
    })
    assert "original-name" in result
    assert "已合并" in result


def test_no_fingerprint_still_works(mem):
    """无 fingerprint 时完全按 name 覆盖（向后兼容）。"""
    mem.execute("remember", {"name": "nofp", "content": "第一版 abc123", "type": "user"})
    mem.execute("remember", {"name": "nofp", "content": "第二版 xyz789", "type": "user"})
    out = mem.execute("recall", {"query": "xyz789"})
    assert "nofp" in out
    assert "abc123" not in out


def test_find_by_fingerprint_hit(mem):
    """_find_by_fingerprint 命中返回 Path。"""
    mem.execute("remember", {
        "name": "hit-me", "content": "命中目标", "type": "strategy",
        "fingerprint": "hit_test",
    })
    found = memory._find_by_fingerprint("hit_test")
    assert found is not None
    assert found.name == "hit-me.md"


def test_find_by_fingerprint_miss(mem):
    """_find_by_fingerprint 未命中返回 None。"""
    assert memory._find_by_fingerprint("never_existed_fp") is None


def test_fingerprint_not_confused_by_body_word(mem):
    """Body 里出现 fingerprint 这个词不算命中——只看 metadata 字段。"""
    mem.execute("remember", {
        "name": "meta-fp", "content": "正文里写 fingerprint 但 metadata 没写", "type": "user",
        "fingerprint": "meta_real",
    })
    found = memory._find_by_fingerprint("fingerprint")
    assert found is None


# ── Jaccard 相似度 ─────────────────────────────────────────

def test_jaccard_identical():
    assert memory._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint():
    assert memory._jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    assert memory._jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 0.5


def test_jaccard_both_empty():
    assert memory._jaccard(set(), set()) == 0.0


# ── recall 相似度检测 ──────────────────────────────────────

def test_recall_warns_on_similar_memories(mem):
    """两条高度重叠的记忆 recall 时合并展示而非各自 raw dump。"""
    mem.execute("remember", {
        "name": "sim-a", "content": "用户偏好简短回答讨厌啰嗦", "type": "user",
    })
    mem.execute("remember", {
        "name": "sim-b", "content": "用户喜欢简短回答不要啰嗦内容", "type": "user",
    })
    out = mem.execute("recall", {"query": "简短回答"})
    assert "内容重叠已合并" in out
    assert "sim-a" in out and "sim-b" in out
    # sim-a 是主要条目，sim-b 被合并 — 不应在输出中单独出现两次
    assert out.count("用户") >= 1  # 至少出现一次，不各自 dump


def test_recall_no_warning_dissimilar(mem):
    """内容差异大的记忆各自独立展示，不合并。"""
    mem.execute("remember", {
        "name": "topic-a", "content": "自动驾驶世界模型研究方向", "type": "knowledge",
    })
    mem.execute("remember", {
        "name": "topic-b", "content": "TUI 终端界面开发工具链配置", "type": "knowledge",
    })
    out = mem.execute("recall", {"query": "自动驾驶 TUI"})
    assert "内容重叠已合并" not in out


def test_recall_single_no_warning(mem):
    """只有 1 条记忆时正常展示，无合并标记。"""
    mem.execute("remember", {
        "name": "alone", "content": "唯一的记忆", "type": "user",
    })
    out = mem.execute("recall", {"query": "唯一"})
    assert "内容重叠已合并" not in out


# ── contradicts 参数 ───────────────────────────────────────

def test_remember_contradicts(mem):
    """新记忆标记 contradicts 旧记忆，双方 frontmatter 均有标注。"""
    mem.execute("remember", {
        "name": "old-view", "content": "用户喜欢长篇详细回答", "type": "user",
    })
    result = mem.execute("remember", {
        "name": "new-view", "content": "用户偏好简短回答", "type": "user",
        "contradicts": "old-view",
    })
    assert "标记为替代 old-view" in result

    # 新文件有 contradicts
    meta_new, _ = memory._split_frontmatter(
        (mem._dir() / "new-view.md").read_text(encoding="utf-8"))
    assert meta_new.get("contradicts") == "old-view"

    # 旧文件有 contradicted_by
    meta_old, _ = memory._split_frontmatter(
        (mem._dir() / "old-view.md").read_text(encoding="utf-8"))
    assert meta_old.get("contradicted_by") == "new-view"


def test_remember_contradicts_missing_target(mem):
    """Contradicts 指向不存在的记忆时不崩溃。"""
    result = mem.execute("remember", {
        "name": "only-me", "content": "新知识", "type": "knowledge",
        "contradicts": "no-such-memory",
    })
    assert "已记住" in result
    assert "标记为替代" in result


def test_remember_rejects_self_contradiction(mem):
    result = mem.execute("remember", {
        "name": "same",
        "content": "不能替代自身",
        "type": "strategy",
        "contradicts": "same",
    })
    assert "不能让记忆" in result
    assert not (mem._dir() / "same.md").exists()


# ── superseded_by 软删除 ───────────────────────────────────

def test_forget_superseded_by_soft_deletes(mem):
    """superseded_by 软删除后文件保留，recall 跳过。"""
    mem.execute("remember", {
        "name": "v1", "content": "旧版本规则 token_v1", "type": "strategy",
    })
    result = mem.execute("forget", {"name": "v1", "superseded_by": "v2"})
    assert "软删除" in result
    assert "已被 v2 取代" in result or "被 v2 取代" in result

    # 文件仍存在
    assert (mem._dir() / "v1.md").exists()

    # recall 跳过
    out = mem.execute("recall", {"query": "token_v1"})
    assert "未找到" in out or "v1" not in out


def test_forget_hard_delete_still_works(mem):
    """不传 superseded_by 时退出活跃库，但保留可恢复归档。"""
    mem.execute("remember", {
        "name": "gone-hard", "content": "硬删除测试", "type": "user",
    })
    result = mem.execute("forget", {"name": "gone-hard"})
    assert "已忘记" in result
    assert not (mem._dir() / "gone-hard.md").exists()
    assert (mem._dir() / "_retired" / "gone-hard.md").exists()


def test_superseded_memory_absent_from_context_and_index(mem):
    mem.execute("remember", {
        "name": "old-rule", "content": "旧规则 old_marker", "type": "strategy",
    })
    mem.execute("remember", {
        "name": "new-rule",
        "content": "新规则 new_marker",
        "type": "strategy",
        "contradicts": "old-rule",
    })
    assert "old-rule" not in (mem.get_context() or "")
    assert "old-rule" not in (mem._index_path().read_text(encoding="utf-8"))
    assert "old-rule" not in mem.execute("recall", {"query": "old_marker"})


def test_memory_audit_reports_stale_and_retired(mem):
    mem.execute("remember", {
        "name": "old-memory", "content": "旧知识", "type": "knowledge",
    })
    path = mem._dir() / "old-memory.md"
    raw = path.read_text(encoding="utf-8").replace(
        "updated:",
        "updated: 2020-01-01T00:00:00Z\n  previous_updated:",
        1,
    )
    path.write_text(raw, encoding="utf-8")
    mem.execute("remember", {
        "name": "retire-me", "content": "无效知识", "type": "knowledge",
    })
    mem.execute("forget", {"name": "retire-me", "reason": "已失效"})

    result = mem.execute("memory_audit", {"stale_days": 30})

    assert "old-memory" in result
    assert "可恢复归档: 1" in result


# ── 蒸馏 ────────────────────────────────────────────────────

def test_extract_distilled():
    text = "---\nname: foo\ndistilled: user prefers short answers\ndistilled: dislikes verbose\n---\n\ncontent here"
    result = memory._extract_distilled(text)
    assert result == ["user prefers short answers", "dislikes verbose"]


def test_extract_distilled_none():
    text = "---\nname: foo\ndescription: just desc\n---\n\ncontent here"
    result = memory._extract_distilled(text)
    assert result == []


def test_extract_distilled_no_frontmatter():
    result = memory._extract_distilled("just plain text")
    assert result == []


def test_remember_stores_distilled(mem):
    """写入时蒸馏产物存入 frontmatter。"""
    mem._distill = lambda content: ["assertion a", "assertion b"] if len(content) >= 80 else []
    mem.execute("remember", {
        "name": "with-distill",
        "content": "这是一条足够长的内容用来触发蒸馏 " + "X" * 80,
        "type": "knowledge",
    })
    raw = (mem._dir() / "with-distill.md").read_text(encoding="utf-8")
    assert "assertion a" in raw
    assert "assertion b" in raw


def test_remember_no_distill_on_short_content(mem, monkeypatch):
    """内容太短不触发蒸馏。"""
    called = []
    monkeypatch.setattr(memory, "_distill", lambda c: called.append(c) or [])
    mem.execute("remember", {
        "name": "short", "content": "太短了", "type": "user",
    })
    assert called == []


def test_recall_ranks_distilled_higher(mem, monkeypatch):
    """有 distilled 断言的记忆在 recall 中排名应高于仅有 body 命中的。"""
    # 还原 mock — 需要真 _distill 但替换为返回固定断言
    monkeypatch.setattr(memory, "_distill", lambda _: ["autonomous driving world model"])
    mem.execute("remember", {
        "name": "with-d", "content": "一段自动驾驶相关的研究笔记 " + "X" * 80, "type": "knowledge",
    })
    mem.execute("remember", {
        "name": "body-only", "content": "autonomous driving 只在 body 里", "type": "knowledge",
    })
    out = mem.execute("recall", {"query": "autonomous driving world model"})
    assert out.index("with-d") < out.index("body-only")


# lesson.py 子系统已整体删除（2026-06-23），severity 双系统边界测试随之移除。
