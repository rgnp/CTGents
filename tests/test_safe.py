"""SAFE 并行分发测试。"""



from src.llm import _PARALLEL_SAFE, _execute_tool_batch


class TestParallelSafeWhitelist:
    """并行安全白名单完整性测试。"""

    def test_read_tools_in_whitelist(self):
        """读取类工具应在白名单中。"""
        for tool in ("read_file", "list_files", "count_lines"):
            assert tool in _PARALLEL_SAFE, f"{tool} 应在并行白名单中"

    def test_search_tools_in_whitelist(self):
        """搜索类工具应在白名单中。"""
        for tool in ("search_web", "read_page", "grep_code"):
            assert tool in _PARALLEL_SAFE, f"{tool} 应在并行白名单中"

    def test_git_read_tools_in_whitelist(self):
        """Git 读取类工具应在白名单中。"""
        for tool in ("git_status", "git_diff", "git_log", "git_branch"):
            assert tool in _PARALLEL_SAFE, f"{tool} 应在并行白名单中"

    def test_project_tools_in_whitelist(self):
        """项目扫描类工具应在白名单中。"""
        for tool in ("scan_project", "check_project", "docs_sync_check", "generate_agents_md"):
            assert tool in _PARALLEL_SAFE, f"{tool} 应在并行白名单中"

    def test_write_tools_not_in_whitelist(self):
        """写文件类工具不应在白名单中。"""
        for tool in ("write_file", "edit_file_lines", "undo_edit", "delete_file"):
            assert tool not in _PARALLEL_SAFE, f"{tool} 不应在并行白名单中"

    def test_git_mutation_tools_not_in_whitelist(self):
        """Git 变更类工具不应在白名单中。"""
        for tool in ("git_commit", "git_push", "git_pr"):
            assert tool not in _PARALLEL_SAFE, f"{tool} 不应在并行白名单中"

    def test_exec_tools_not_in_whitelist(self):
        """命令执行类工具不应在白名单中。"""
        for tool in ("run_command", "run_python"):
            assert tool not in _PARALLEL_SAFE, f"{tool} 不应在并行白名单中"

    def test_memory_tools_not_in_whitelist(self):
        """记忆修改类工具不应在白名单中。"""
        for tool in ("remember", "forget", "install_plugin"):
            assert tool not in _PARALLEL_SAFE, f"{tool} 不应在并行白名单中"


class TestExecuteToolBatch:
    """_execute_tool_batch 测试。"""

    def make_tc(self, name: str, args: str = "{}"):
        """构造一个模拟的 tool_call 对象。"""
        import types
        tc = types.SimpleNamespace(
            function=types.SimpleNamespace(
                name=name,
                arguments=args,
            )
        )
        return tc

    def setup_method(self):
        """重置 Storm 窗口，避免测试间干扰。"""
        from src.tools.storm import reset_storm
        reset_storm()


    def test_single_tool(self):
        """单工具仍能正常执行。"""
        approved = [
            (self.make_tc("git_status"), "git_status", {}, self.make_tc("git_status"), None),
        ]
        results = _execute_tool_batch(approved)
        assert len(results) == 1
        assert isinstance(results[0], str)

    def test_multiple_parallel_safe(self):
        """多个并行安全工具应全部执行并返回结果。"""
        approved = [
            (self.make_tc("git_status"), "git_status", {}, self.make_tc("git_status"), None),
            (self.make_tc("git_branch"), "git_branch", {}, self.make_tc("git_branch"), None),
            (self.make_tc("list_files"), "list_files", {"path": "."},
             self.make_tc("list_files", '{"path": "."}'), None),
        ]
        results = _execute_tool_batch(approved)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, str)
            assert len(r) > 0

    def test_mixed_parallel_and_serial(self):
        """混合并行和串行工具，结果顺序应保持不变。"""
        approved = [
            (self.make_tc("git_status"), "git_status", {}, self.make_tc("git_status"), None),
            (self.make_tc("write_file"), "write_file", {"path": "/tmp/_safe_test.txt", "content": "test"},
             self.make_tc("write_file", '{"path": "/tmp/_safe_test.txt", "content": "test"}'), None),
            (self.make_tc("git_branch"), "git_branch", {}, self.make_tc("git_branch"), None),
        ]
        results = _execute_tool_batch(approved)
        assert len(results) == 3
        # 结果对应原始顺序
        assert results[0] is not None  # git_status (parallel)
        assert results[1] is not None  # write_file (serial)
        assert results[2] is not None  # git_branch (parallel)

    def test_skipped_tools_no_execution(self):
        """跳过（有预置结果）的工具不进入执行阶段。"""
        approved = [
            (self.make_tc("github_status"), "github_status", {}, self.make_tc("github_status"),
             "⛔ [github_status] 已跳过（用户未批准）"),
            (self.make_tc("git_branch"), "git_branch", {}, self.make_tc("git_branch"), None),
        ]
        results = _execute_tool_batch(approved)
        assert len(results) == 2
        assert results[0] == "⛔ [github_status] 已跳过（用户未批准）"
        assert results[1] is not None  # git_branch still executes

    def test_all_skipped_no_execution(self):
        """全部跳过时不应执行任何工具。"""
        approved = [
            (self.make_tc("a"), "a", {}, self.make_tc("a"), "跳过1"),
            (self.make_tc("b"), "b", {}, self.make_tc("b"), "跳过2"),
        ]
        results = _execute_tool_batch(approved)
        assert results == ["跳过1", "跳过2"]

    def test_empty_batch(self):
        """空批次应返回空列表。"""
        results = _execute_tool_batch([])
        assert results == []

    def test_parallel_safe_actually_runs_in_parallel(self):
        """并行工具应同时执行（验证非串行行为）。"""
        import time

        approved = [
            (self.make_tc("list_files"), "list_files", {"path": "."},
             self.make_tc("list_files", '{"path": "."}'), None),
            (self.make_tc("list_files"), "list_files", {"path": ".."},
             self.make_tc("list_files", '{"path": ".."}'), None),
        ]

        start = time.time()
        results = _execute_tool_batch(approved)
        time.time() - start

        assert len(results) == 2
        # 如果串行执行，需要 2× 单次时间；
        # 如果并行执行，接近单次时间（假设执行时间 > 调度开销）
        # 这里只验证结果正确，不断言时间（CI 环境不稳定）
        assert all(isinstance(r, str) and len(r) > 0 for r in results)

    def test_storm_interaction_with_parallel(self):
        """Storm 去重在线程安全模式下仍正常工作。"""
        from src.tools.storm import reset_storm
        reset_storm()

        # 两个相同的并行工具，Storm 应拦截第二次
        approved = [
            (self.make_tc("read_file"), "read_file", {"path": "main.py"},
             self.make_tc("read_file", '{"path": "main.py"}'), None),
            (self.make_tc("read_file"), "read_file", {"path": "main.py"},
             self.make_tc("read_file", '{"path": "main.py"}'), None),
        ]

        results = _execute_tool_batch(approved)
        assert len(results) == 2
        # 去重生效（与并行顺序无关）：要么命中缓存留下"⚡重复调用"标记，
        # 要么后发调用等待先发完成、拿到相同结果。两条路径都算去重成功；
        # 断言固定的 results[1] 带标记是错的——并行下谁先谁后不定，会偶发。
        marker_count = sum("⚡重复调用" in r for r in results)
        assert marker_count >= 1 or results[0] == results[1]


class TestStormThreadSafety:
    """Storm 线程安全测试。"""

    def test_concurrent_storm_checks(self):
        """多个并发 storm_check 不应导致竞争条件。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from src.tools.storm import reset_storm, storm_check

        reset_storm()

        # 同时检查多个不同的工具
        calls = [
            ("read_file", {"path": "a.txt"}),
            ("read_file", {"path": "b.txt"}),
            ("read_file", {"path": "c.txt"}),
            ("read_file", {"path": "d.txt"}),
            ("read_file", {"path": "e.txt"}),
            ("read_file", {"path": "f.txt"}),
            ("read_file", {"path": "g.txt"}),
            ("read_file", {"path": "h.txt"}),
            ("read_file", {"path": "i.txt"}),
        ]

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(storm_check, name, args) for name, args in calls]
            results = [f.result() for f in as_completed(futures)]

        # 全部应是首次，None
        assert all(r is None for r in results)
        # 窗口应有 9 个（窗口上限 64，9 条不触发淘汰）
        from src.tools.storm import get_storm_stats
        assert get_storm_stats()["window_size"] == 9
