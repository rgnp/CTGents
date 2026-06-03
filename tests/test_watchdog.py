"""watchdog.py 测试 — 看门狗逻辑：心跳检查、崩溃计数、复活限制。"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestHeartbeatCheck:
    """心跳检查测试。"""

    def setup_method(self):
        import src.watchdog as wd
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_heartbeat = wd.HEARTBEAT_FILE
        wd.HEARTBEAT_FILE = Path(self._tmpdir.name) / "heartbeat"

    def teardown_method(self):
        import src.watchdog as wd
        wd.HEARTBEAT_FILE = self._orig_heartbeat
        self._tmpdir.cleanup()

    def test_no_heartbeat_file(self):
        import src.watchdog as wd
        age = wd._check_heartbeat()
        assert age < 0

    def test_fresh_heartbeat(self):
        import src.watchdog as wd
        wd.HEARTBEAT_FILE.write_text(str(time.time()))
        age = wd._check_heartbeat()
        assert 0 <= age < 10  # 刚写的，不超过 10 秒

    def test_stale_heartbeat(self):
        import src.watchdog as wd
        wd.HEARTBEAT_FILE.write_text(str(time.time() - 200))
        age = wd._check_heartbeat()
        assert age > 120


class TestCrashLimit:
    """崩溃上限检查测试。"""

    def test_no_crashes(self):
        import src.watchdog as wd
        assert not wd._check_crash_limit({"crashes": []})

    def test_one_crash(self):
        import src.watchdog as wd
        state = {"crashes": [time.time()]}
        assert not wd._check_crash_limit(state)

    def test_three_recent_crashes(self):
        import src.watchdog as wd
        now = time.time()
        state = {"crashes": [now - 10, now - 20, now - 30]}
        assert wd._check_crash_limit(state)

    def test_old_crashes_expire(self):
        import src.watchdog as wd
        now = time.time()
        state = {"crashes": [now - 700, now - 650, now - 620]}
        assert not wd._check_crash_limit(state)

    def test_mixed_old_and_recent(self):
        import src.watchdog as wd
        now = time.time()
        state = {"crashes": [now - 700, now - 10, now - 20, now - 30]}
        assert wd._check_crash_limit(state)  # 3 recent ones


class TestStateFile:
    """状态文件读写测试。"""

    def setup_method(self):
        import src.watchdog as wd
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_state = wd.STATE_FILE
        self._orig_dir = wd.WATCHDOG_DIR
        wd.WATCHDOG_DIR = Path(self._tmpdir.name)
        wd.STATE_FILE = wd.WATCHDOG_DIR / "watchdog_state.json"

    def teardown_method(self):
        import src.watchdog as wd
        wd.WATCHDOG_DIR = self._orig_dir
        wd.STATE_FILE = self._orig_state
        self._tmpdir.cleanup()

    def test_write_and_read_state(self):
        import src.watchdog as wd
        state = {
            "parent_pid": 1234,
            "project_root": "/test/project",
            "crashes": [time.time()],
            "resurrections": 1,
            "started": time.time(),
        }
        wd._write_state(state)
        assert wd.STATE_FILE.exists()

        loaded = wd.get_status()
        assert loaded is not None
        assert loaded["parent_pid"] == 1234
        assert loaded["resurrections"] == 1


class TestConfig:
    """常量测试。"""

    def test_crash_limit_reasonable(self):
        import src.watchdog as wd
        assert wd.CRASH_LIMIT >= 2, "崩溃上限太低无法保护"
        assert wd.CRASH_LIMIT <= 5, "崩溃上限太高等待太久"

    def test_check_interval_reasonable(self):
        import src.watchdog as wd
        assert 1 <= wd.CHECK_INTERVAL <= 30

    def test_heartbeat_timeout_reasonable(self):
        import src.watchdog as wd
        assert wd.HEARTBEAT_TIMEOUT >= 60, "心跳超时太短容易误判"


if __name__ == "__main__":
    tests = []
    for cls in [TestHeartbeatCheck, TestCrashLimit, TestStateFile, TestConfig]:
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
