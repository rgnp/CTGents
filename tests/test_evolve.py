"""evolve.py 测试 — 进化档案的存储、查询和相似度搜索。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import contextlib

from src.evolve import (
    EvolutionRecord,
    find_similar,
    get_last_n,
    get_stats,
    query,
    record_attempt,
)


class TestRecordAndQuery:
    """写入和查询测试。"""

    def setup_method(self):
        # 使用临时文件替代真实路径
        import src.evolve as ev
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = ev.EVOLVE_DIR
        self._orig_log = ev.EVOLVE_LOG
        ev.EVOLVE_DIR = Path(self._tmpdir.name) / "evolution"
        ev.EVOLVE_LOG = ev.EVOLVE_DIR / "evolution.jsonl"

    def teardown_method(self):
        import src.evolve as ev
        ev.EVOLVE_DIR = self._orig_dir
        ev.EVOLVE_LOG = self._orig_log
        self._tmpdir.cleanup()

    def test_record_and_read_back(self):
        record_attempt(EvolutionRecord(
            goal="优化文件搜索性能",
            files_changed=["src/tools/file.py"],
            outcome="merged",
            tags=["performance", "tools"],
            lessons_learned="用 dict 索引代替 list 遍历效果显著",
            duration_total_ms=1500.0,
        ))
        results = query(goal_keywords=["文件搜索"])
        assert len(results) >= 1
        assert results[0]["outcome"] == "merged"
        assert "performance" in results[0]["tags"]

    def test_record_simple(self):
        rid = record_attempt(EvolutionRecord(
            goal="测试简单记录",
            files_changed=["test.py"],
            outcome="reverted",
            lessons_learned="测试失败，语法错误",
        ))
        assert len(rid) == 12
        results = get_last_n(1)
        assert results[0]["outcome"] == "reverted"

    def test_query_by_outcome(self):
        record_attempt(EvolutionRecord(goal="bug fix A", files_changed=["a.py"], outcome="merged"))
        record_attempt(EvolutionRecord(goal="bug fix B", files_changed=["b.py"], outcome="reverted"))
        record_attempt(EvolutionRecord(goal="bug fix C", files_changed=["c.py"], outcome="merged"))

        merged = query(outcome="merged")
        assert len(merged) >= 2
        assert all(r["outcome"] == "merged" for r in merged)

        reverted = query(outcome="reverted")
        assert len(reverted) >= 1
        assert all(r["outcome"] == "reverted" for r in reverted)

    def test_query_by_tags(self):
        record_attempt(EvolutionRecord(goal="perf A", files_changed=["a.py"], outcome="merged", tags=["performance"]))
        record_attempt(EvolutionRecord(
            goal="perf B", files_changed=["b.py"], outcome="merged", tags=["performance", "tools"]))
        record_attempt(EvolutionRecord(goal="bug A", files_changed=["c.py"], outcome="merged", tags=["bugfix"]))

        perf = query(tags=["performance"])
        assert len(perf) >= 2

        both = query(tags=["performance", "tools"])
        assert len(both) >= 1

    def test_query_combined(self):
        record_attempt(EvolutionRecord(
            goal="优化数据库查询", files_changed=["db.py"], outcome="merged", tags=["performance"]))
        record_attempt(EvolutionRecord(
            goal="修复数据库连接泄漏", files_changed=["db.py"], outcome="merged", tags=["bugfix"]))

        results = query(goal_keywords=["数据库"], outcome="merged")
        assert len(results) >= 2

    def test_query_limit(self):
        for i in range(10):
            record_attempt(EvolutionRecord(goal=f"test {i}", files_changed=[f"file{i}.py"], outcome="merged"))
        results = query(limit=3)
        assert len(results) <= 3


class TestSimilarity:
    """TF-IDF 相似度搜索测试。"""

    def setup_method(self):
        import src.evolve as ev
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = ev.EVOLVE_DIR
        self._orig_log = ev.EVOLVE_LOG
        ev.EVOLVE_DIR = Path(self._tmpdir.name) / "evolution"
        ev.EVOLVE_LOG = ev.EVOLVE_DIR / "evolution.jsonl"

    def teardown_method(self):
        import src.evolve as ev
        ev.EVOLVE_DIR = self._orig_dir
        ev.EVOLVE_LOG = self._orig_log
        self._tmpdir.cleanup()

    def test_find_similar(self):
        record_attempt(EvolutionRecord(
            goal="优化文件读取的缓存性能", files_changed=["file.py"], outcome="merged", tags=["performance"]))
        record_attempt(EvolutionRecord(
            goal="修复 web 搜索的超时问题", files_changed=["web.py"], outcome="merged", tags=["bugfix"]))
        record_attempt(EvolutionRecord(
            goal="添加 git 状态检查的缓存", files_changed=["git.py"], outcome="merged", tags=["performance"]))

        similar = find_similar("文件缓存优化", top_n=3)
        assert len(similar) >= 1
        # 最相似的应该是第一条
        assert "文件" in similar[0]["goal"] or "缓存" in similar[0]["goal"]

    def test_find_similar_empty(self):
        similar = find_similar("不存在的话题")
        assert similar == []

    def test_find_similar_exact_match(self):
        record_attempt(EvolutionRecord(
            goal="精确匹配测试：优化 import 检查速度", files_changed=["validate.py"], outcome="merged"))
        record_attempt(EvolutionRecord(goal="不相关的其他修改", files_changed=["other.py"], outcome="merged"))

        similar = find_similar("import 检查性能优化", top_n=2)
        assert len(similar) >= 1
        assert "import" in similar[0]["goal"].lower()


class TestStats:
    """统计测试。"""

    def setup_method(self):
        import src.evolve as ev
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = ev.EVOLVE_DIR
        self._orig_log = ev.EVOLVE_LOG
        ev.EVOLVE_DIR = Path(self._tmpdir.name) / "evolution"
        ev.EVOLVE_LOG = ev.EVOLVE_DIR / "evolution.jsonl"

    def teardown_method(self):
        import src.evolve as ev
        ev.EVOLVE_DIR = self._orig_dir
        ev.EVOLVE_LOG = self._orig_log
        self._tmpdir.cleanup()

    def test_get_stats(self):
        record_attempt(EvolutionRecord(goal="A", files_changed=["a.py"], outcome="merged", duration_total_ms=1000))
        record_attempt(EvolutionRecord(goal="B", files_changed=["b.py"], outcome="reverted", duration_total_ms=2000))
        record_attempt(EvolutionRecord(goal="C", files_changed=["c.py"], outcome="merged", duration_total_ms=1500))

        stats = get_stats()
        assert stats["total_attempts"] == 3
        assert stats["merged"] == 2
        assert stats["reverted"] == 1
        assert stats["success_rate"] == round(2 / 3 * 100, 1)

    def test_get_stats_empty(self):
        # 空临时目录无记录
        stats = get_stats()
        assert stats.get("total_attempts", 0) == 0 or stats.get("message") is not None

    def test_get_last_n(self):
        record_attempt(EvolutionRecord(goal="first", files_changed=["a.py"], outcome="merged"))
        record_attempt(EvolutionRecord(goal="second", files_changed=["b.py"], outcome="reverted"))
        record_attempt(EvolutionRecord(goal="third", files_changed=["c.py"], outcome="merged"))

        last = get_last_n(2)
        assert len(last) == 2
        assert last[0]["goal"] == "third"  # 最新的在前


class TestEvolutionRecord:
    """EvolutionRecord dataclass 测试。"""

    def test_defaults(self):
        rec = EvolutionRecord(goal="test")
        assert len(rec.id) == 12
        assert rec.outcome == "unknown"
        assert rec.tags == []
        assert rec.files_changed == []

    def test_full_record(self):
        rec = EvolutionRecord(
            goal="重构缓存层",
            files_changed=["src/cache.py", "tests/test_cache.py"],
            outcome="merged",
            tags=["refactor", "performance"],
            lessons_learned="分阶段重构更安全",
            duration_total_ms=5000.0,
        )
        rec.__dict__ if hasattr(rec, '__dict__') else {
            'goal': rec.goal, 'outcome': rec.outcome, 'tags': rec.tags}
        assert rec.outcome == "merged"
        assert len(rec.files_changed) == 2


if __name__ == "__main__":
    tests = []

    # 收集所有测试
    for cls in [TestRecordAndQuery, TestSimilarity, TestStats,
                TestEvolutionRecord]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(instance, name)))

    passed = 0
    for name, fn in tests:
        try:
            if hasattr(fn.__self__, 'setup_method'):
                fn.__self__.setup_method()
            fn()
            if hasattr(fn.__self__, 'teardown_method'):
                fn.__self__.teardown_method()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            if hasattr(fn.__self__, 'teardown_method'):
                with contextlib.suppress(Exception):
                    fn.__self__.teardown_method()
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            if hasattr(fn.__self__, 'teardown_method'):
                with contextlib.suppress(Exception):
                    fn.__self__.teardown_method()

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
