"""evolve.py 测试 — 进化档案的存储、查询和相似度搜索。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.evolve import (
    EvolutionRecord,
    record_attempt,
    record_simple,
    query,
    find_similar,
    get_lessons,
    get_failure_patterns,
    get_stats,
    get_last_n,
    EVOLVE_DIR,
    EVOLVE_LOG,
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
        rid = record_simple(
            goal="测试简单记录",
            files_changed=["test.py"],
            outcome="reverted",
            lessons="测试失败，语法错误",
        )
        assert len(rid) == 12
        results = get_last_n(1)
        assert results[0]["outcome"] == "reverted"

    def test_query_by_outcome(self):
        record_simple("bug fix A", ["a.py"], "merged")
        record_simple("bug fix B", ["b.py"], "reverted")
        record_simple("bug fix C", ["c.py"], "merged")

        merged = query(outcome="merged")
        assert len(merged) >= 2
        assert all(r["outcome"] == "merged" for r in merged)

        reverted = query(outcome="reverted")
        assert len(reverted) >= 1
        assert all(r["outcome"] == "reverted" for r in reverted)

    def test_query_by_tags(self):
        record_simple("perf A", ["a.py"], "merged", tags=["performance"])
        record_simple("perf B", ["b.py"], "merged", tags=["performance", "tools"])
        record_simple("bug A", ["c.py"], "merged", tags=["bugfix"])

        perf = query(tags=["performance"])
        assert len(perf) >= 2

        both = query(tags=["performance", "tools"])
        assert len(both) >= 1

    def test_query_combined(self):
        record_simple("优化数据库查询", ["db.py"], "merged", tags=["performance"])
        record_simple("修复数据库连接泄漏", ["db.py"], "merged", tags=["bugfix"])

        results = query(goal_keywords=["数据库"], outcome="merged")
        assert len(results) >= 2

    def test_query_limit(self):
        for i in range(10):
            record_simple(f"test {i}", [f"file{i}.py"], "merged")
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
        record_simple("优化文件读取的缓存性能", ["file.py"], "merged", tags=["performance"])
        record_simple("修复 web 搜索的超时问题", ["web.py"], "merged", tags=["bugfix"])
        record_simple("添加 git 状态检查的缓存", ["git.py"], "merged", tags=["performance"])

        similar = find_similar("文件缓存优化", top_n=3)
        assert len(similar) >= 1
        # 最相似的应该是第一条
        assert "文件" in similar[0]["goal"] or "缓存" in similar[0]["goal"]

    def test_find_similar_empty(self):
        similar = find_similar("不存在的话题")
        assert similar == []

    def test_find_similar_exact_match(self):
        record_simple("精确匹配测试：优化 import 检查速度", ["validate.py"], "merged")
        record_simple("不相关的其他修改", ["other.py"], "merged")

        similar = find_similar("import 检查性能优化", top_n=2)
        assert len(similar) >= 1
        assert "import" in similar[0]["goal"].lower()


class TestLessons:
    """教训提取测试。"""

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

    def test_get_lessons(self):
        record_simple("task A", ["a.py"], "reverted",
                      lessons="不要在高频路径上添加同步 I/O", tags=["performance"])
        record_simple("task B", ["b.py"], "merged",
                      lessons="用 LRU 缓存减少重复计算", tags=["performance"])
        record_simple("task C", ["c.py"], "reverted",
                      lessons="正则表达式需要预编译", tags=["bugfix"])

        lessons = get_lessons()
        assert len(lessons) >= 3

        perf_lessons = get_lessons(tag="performance")
        assert len(perf_lessons) >= 2

    def test_get_failure_patterns(self):
        record_simple("修改 A 导致问题", ["a.py"], "reverted",
                      lessons="缺少类型检查导致运行时错误")
        patterns = get_failure_patterns()
        assert len(patterns) >= 1
        assert patterns[0]["outcome"] == "reverted" if "outcome" in patterns[0] else True


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
        record_simple("A", ["a.py"], "merged", duration_ms=1000)
        record_simple("B", ["b.py"], "reverted", duration_ms=2000)
        record_simple("C", ["c.py"], "merged", duration_ms=1500)

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
        record_simple("first", ["a.py"], "merged")
        record_simple("second", ["b.py"], "reverted")
        record_simple("third", ["c.py"], "merged")

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
        d = rec.__dict__ if hasattr(rec, '__dict__') else {
            'goal': rec.goal, 'outcome': rec.outcome, 'tags': rec.tags}
        assert rec.outcome == "merged"
        assert len(rec.files_changed) == 2


if __name__ == "__main__":
    tests = []

    # 收集所有测试
    for cls in [TestRecordAndQuery, TestSimilarity, TestLessons, TestStats,
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
                try:
                    fn.__self__.teardown_method()
                except Exception:
                    pass
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            if hasattr(fn.__self__, 'teardown_method'):
                try:
                    fn.__self__.teardown_method()
                except Exception:
                    pass

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
