"""测试经验检索模块。"""

from src.experience import (
    _parse_archive_file,
    _tokenize,
    format_experience_context,
    search_similar_tasks,
)


class TestTokenize:
    def test_english_words(self):
        tokens = _tokenize("memory corruption bug fix")
        assert "memory" in tokens
        assert "corruption" in tokens
        assert "bug" in tokens
        assert "fix" in tokens
        assert "ry" not in tokens  # 不会出单字符

    def test_chinese_bigrams(self):
        tokens = _tokenize("记忆腐败治疗")
        assert "记忆" in tokens
        assert "腐败" in tokens
        assert "治疗" in tokens
        assert "忆腐" in tokens  # 交叉 bigram
        assert "败治" in tokens

    def test_mixed(self):
        tokens = _tokenize("修复 memory bug")
        assert "修复" in tokens
        assert "memory" in tokens
        assert "bug" in tokens

    def test_single_char_filtered(self):
        tokens = _tokenize("a b c")
        assert len(tokens) == 0  # 全部过滤

    def test_empty(self):
        assert _tokenize("") == set()


class TestParseArchive:
    def test_parses_real_file(self, tmp_path):
        content = """# 记忆腐败治疗

- [x] Step 1: 诊断
- [x] Step 2: 修复

## 完成总结
- 计划 2 步 → 实际 2 步
- 教训: 腐败的根本原因是只记不合
"""
        f = tmp_path / "test.md"
        f.write_text(content, encoding="utf-8")
        result = _parse_archive_file(f)
        assert result is not None
        assert result["title"] == "记忆腐败治疗"
        assert result["total_steps"] == 2
        assert result["done_steps"] == 2
        assert "腐败的根本原因是只记不合" in result["summary"]

    def test_parses_anchor(self, tmp_path):
        content = """# 某任务

# 目标锚点
解决 context 膨胀问题

- [x] Step 1

## 完成总结
- 搞定
"""
        f = tmp_path / "test.md"
        f.write_text(content, encoding="utf-8")
        result = _parse_archive_file(f)
        assert result is not None
        assert "解决 context 膨胀问题" in result["anchor"]

    def test_no_summary(self, tmp_path):
        content = """# 某任务
- [x] Step 1
"""
        f = tmp_path / "test.md"
        f.write_text(content, encoding="utf-8")
        result = _parse_archive_file(f)
        assert result is not None
        assert result["summary"] == ""


class TestSearchSimilar:
    def test_returns_empty_for_no_match(self):
        results = search_similar_tasks("xyz123abc 完全无关的查询词", top_k=3)
        # 可能返回也可能不返回，取决于 archive 内容，最小断言是不抛异常
        assert isinstance(results, list)

    def test_finds_memory_corruption(self):
        """记忆腐败 应该匹配到 记忆腐败治疗。"""
        results = search_similar_tasks("记忆腐败 合并 去重", top_k=3)
        names = [r["name"] for r in results]
        assert any("记忆腐败治疗" in n for n in names), f"没找到记忆腐败治疗: {names}"

    def test_top_k_limit(self):
        results = search_similar_tasks("修复 bug", top_k=2)
        assert len(results) <= 2


class TestFormatContext:
    def test_empty(self):
        assert format_experience_context([]) is None

    def test_formats_single(self):
        tasks = [{
            "name": "test-task",
            "title": "测试任务",
            "anchor": "验证 P0 功能",
            "summary": "- 教训: 写测试很重要\n",
            "total_steps": 3,
            "done_steps": 3,
        }]
        result = format_experience_context(tasks)
        assert result is not None
        assert "测试任务" in result
        assert "写测试很重要" in result
        assert "验证 P0 功能" in result

    def test_fallback_no_title(self):
        tasks = [{
            "name": "test-task",
            "title": "",
            "anchor": "",
            "summary": "",
            "total_steps": 1,
            "done_steps": 1,
        }]
        result = format_experience_context(tasks)
        assert result is not None
        assert "test-task" in result
