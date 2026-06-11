"""memory.py 核心路径回归：remember / recall / forget / get_context 索引。

此前仅 detect_signal 有测试，核心读写路径裸奔。重点回归：frontmatter 的
闭合分隔符曾用 find("---") 子串匹配，当 description/正文含 '---' 时会错位
→ type 解析为空、body 串入 metadata、索引 desc 丢失。三处解析器共用同一 bug。
"""
from __future__ import annotations

import pytest

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
    return memory


# ── characterization：锁住正常往返 ──────────────────────────

def test_remember_then_recall(mem):
    mem.execute("remember", {"name": "foo-bar", "content": "用户偏好简短回答", "type": "user"})
    out = mem.execute("recall", {"query": "简短"})
    assert "foo-bar" in out
    assert "[user]" in out


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



# ── lesson.py 边界 — severity 文件不被 _find_by_fingerprint 匹配 ──

def test_find_by_fingerprint_skips_lesson_py_files(mem):
    """有 severity 的文件（lesson.py 写的）不被 _find_by_fingerprint 匹配。

    这是双系统安全边界：lesson.py 用 fingerprint 做程序化去重，
    memory.py 用 fingerprint 做手动合并，两者共用 memory/ 目录——
    severity 字段是分界线，防止 _remember 覆盖 lesson.py 的积累教训。
    """
    # 模拟 lesson.py 写入的文件（含 severity）
    lesson_file = mem._dir() / "lesson-tool-arg-errors.md"
    lesson_file.write_text(
        "---\n"
        "name: lesson-tool-arg-errors\n"
        "description: tool_arg_error 模式\n"
        "metadata:\n"
        "  type: strategy\n"
        "  fingerprint: tool_arg_error\n"
        "  severity: medium\n"
        "  times_encountered: 19\n"
        "  last_encountered: 2026-06-11T09:43:26Z\n"
        "---\n\n"
        "19 次积累的结构化教训，绝对不能丢。\n",
        encoding="utf-8",
    )
    # _find_by_fingerprint 应该跳过它
    found = memory._find_by_fingerprint("tool_arg_error")
    assert found is None, (
        "severity 文件应被跳过——否则 _remember 会把 lesson.py 19 次积累的教训覆盖掉"
    )


def test_fingerprint_remember_never_touches_lesson_py_file(mem):
    """通过 execute 调 remember，同 fingerprint 但 target 是 lesson.py 文件 →
    创建新文件而非合并覆盖。
    """
    # 模拟 lesson.py 写的文件
    lesson_file = mem._dir() / "lesson-something.md"
    lesson_file.write_text(
        "---\n"
        "name: lesson-something\n"
        "description: 程序化教训\n"
        "metadata:\n"
        "  type: strategy\n"
        "  fingerprint: dangerous_fp\n"
        "  severity: high\n"
        "  times_encountered: 10\n"
        "---\n\n"
        "lesson.py 写入的重要内容。\n",
        encoding="utf-8",
    )
    # 手动 remember 同指纹
    result = mem.execute("remember", {
        "name": "new-memory", "content": "手动记住的内容 token_zz99", "type": "strategy",
        "fingerprint": "dangerous_fp",
    })
    # 应创建新文件，不是合并到 lesson-something
    assert "已记住" in result
    # 原文件未被覆盖
    original = lesson_file.read_text(encoding="utf-8")
    assert "lesson.py 写入的重要内容" in original
    assert "times_encountered: 10" in original
    # 新文件存在
    new_file = mem._dir() / "new-memory.md"
    assert new_file.exists()
    assert "token_zz99" in new_file.read_text(encoding="utf-8")
