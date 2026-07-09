"""Verifier 模块测试。"""

from src.verifier import (
    GateResult,
    TerminationGate,
    _extract_claimed_files,
    _looks_like_filepath,
    check_claim_evidence,
    check_modified_tests,
    check_task_consistency,
    run_termination_gate,
)

# ── _looks_like_filepath ──

class TestLooksLikeFilepath:
    def test_py_file(self):
        assert _looks_like_filepath("src/main.py") is True

    def test_md_file(self):
        assert _looks_like_filepath("docs/readme.md") is True

    def test_no_extension(self):
        assert _looks_like_filepath("src") is False

    def test_chinese_text(self):
        assert _looks_like_filepath("创建了文件") is False

    def test_too_short(self):
        assert _looks_like_filepath("a") is False


# ── _extract_claimed_files ──

class TestExtractClaimedFiles:
    def test_backtick_path(self):
        text = "我写入了 `src/verifier.py` 这个文件"
        result = _extract_claimed_files(text)
        assert "src/verifier.py" in result

    def test_no_backtick_but_looks_like_path(self):
        text = "我修改了 src/llm.py 的循环逻辑"
        result = _extract_claimed_files(text)
        assert "src/llm.py" in result

    def test_chinese_sentence_not_claimed(self):
        text = "我更新了代码结构"
        result = _extract_claimed_files(text)
        assert len(result) == 0

    def test_multiple_files(self):
        text = "创建了 `src/verifier.py` 和修改了 `src/llm.py`"
        result = _extract_claimed_files(text)
        assert "src/verifier.py" in result
        assert "src/llm.py" in result


# ── check_claim_evidence ──

class TestCheckClaimEvidence:
    def test_no_claims_passes(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr("src.verifier._get_git_changes", lambda: (set(), set(), True))
        result = check_claim_evidence("今天天气不错。", dummy_ctx)
        assert result.severity == "pass"

    def test_missing_file_fails(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr("src.verifier._get_git_changes", lambda: (set(), set(), True))
        result = check_claim_evidence(
            "我创建了 `nonexistent_abc_xyz.py` 文件",
            dummy_ctx,
        )
        assert result.severity == "fail"
        assert "nonexistent_abc_xyz.py" in result.message

    def test_file_in_diff_passes(self, dummy_ctx, monkeypatch):
        """声称修改的文件在 git diff 中 → 有改动证据 → pass。"""
        monkeypatch.setattr(
            "src.verifier._get_git_changes",
            lambda: ({"AGENTS.md"}, set(), True),
        )
        result = check_claim_evidence(
            "我修改了 `AGENTS.md` 文件",
            dummy_ctx,
        )
        assert result.severity == "pass"

    def test_unchanged_file_fails(self, dummy_ctx, monkeypatch):
        """文件存在但不在 git diff 也不在 untracked → 没有改动证据 → fail。"""
        monkeypatch.setattr(
            "src.verifier._get_git_changes",
            lambda: ({"other.py"}, set(), True),
        )
        result = check_claim_evidence(
            "我修改了 `AGENTS.md` 文件",
            dummy_ctx,
        )
        assert result.severity == "fail"
        assert "没有实际改动" in result.message
        assert "AGENTS.md" in result.message

    def test_non_git_repo_fallback_passes(self, dummy_ctx, monkeypatch):
        """非 git 仓库 → fallback 到纯文件存在性检查。"""
        monkeypatch.setattr(
            "src.verifier._get_git_changes",
            lambda: (set(), set(), False),
        )
        result = check_claim_evidence(
            "我创建了 `AGENTS.md` 文件",
            dummy_ctx,
        )
        assert result.severity == "pass"

    def test_untracked_file_passes(self, dummy_ctx, monkeypatch):
        """文件在 untracked 中（新创建的文件）→ pass。"""
        monkeypatch.setattr(
            "src.verifier._get_git_changes",
            lambda: (set(), {"AGENTS.md"}, True),
        )
        result = check_claim_evidence(
            "我创建了 `AGENTS.md` 文件",
            dummy_ctx,
        )
        assert result.severity == "pass"


# ── check_task_consistency ──

class TestCheckTaskConsistency:
    def test_no_current_md_passes(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.tasks.read_current",
            lambda: "",
        )
        result = check_task_consistency("", dummy_ctx)
        assert result.severity == "pass"

    def test_all_done_passes(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.tasks.read_current",
            lambda: "- [x] 步骤1\n- [x] 步骤2\n",
        )
        result = check_task_consistency("", dummy_ctx)
        assert result.severity == "pass"

    def test_has_unfinished_no_control_warns(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.tasks.read_current",
            lambda: "- [ ] 步骤1\n- [x] 步骤2\n",
        )
        dummy_ctx.control_signal = None
        result = check_task_consistency("", dummy_ctx)
        assert result.severity == "warn"

    def test_has_unfinished_but_task_done_passes(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.tasks.read_current",
            lambda: "- [ ] 步骤1\n- [x] 步骤2\n",
        )
        dummy_ctx.control_signal = "task_done"
        result = check_task_consistency("", dummy_ctx)
        assert result.severity == "pass"


# ── check_modified_tests ──

class TestCheckModifiedTests:
    def test_no_py_modified_passes(self, dummy_ctx, monkeypatch):
        dummy_ctx.log = []
        result = check_modified_tests("", dummy_ctx)
        assert result.severity == "pass"

    def test_py_modified_warns(self, dummy_ctx):
        import json
        dummy_ctx.log = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "src/verifier.py"}),
                        }
                    }
                ],
            }
        ]
        result = check_modified_tests("", dummy_ctx)
        assert result.severity == "warn"


# ── GateResult ──

class TestGateResult:
    def test_defaults(self):
        g = GateResult("test", True, "pass")
        assert g.check_name == "test"
        assert g.passed is True
        assert g.message == ""


# ── TerminationGate ──

class TestTerminationGate:
    def test_register_and_run(self, dummy_ctx):
        gate = TerminationGate()
        called = []

        def my_check(content, ctx):
            called.append(1)
            return GateResult("my", True, "pass")

        gate.register(my_check)
        results = gate.run("test", dummy_ctx)
        assert len(called) == 1
        assert len(results) == 1

    def test_check_exception_silently_swallowed(self, dummy_ctx):
        gate = TerminationGate()

        def always_crash(content, ctx):
            raise RuntimeError("oops")

        gate.register(always_crash)
        results = gate.run("test", dummy_ctx)
        assert len(results) == 0  # exception swallowed


# ── run_termination_gate ──

class TestRunTerminationGate:
    def test_all_pass(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.verifier._default_gate._checks",
            [lambda c, ctx: GateResult("a", True, "pass")],
        )
        result = run_termination_gate("test", dummy_ctx)
        assert result == "pass"

    def test_warn_returns_warn(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.verifier._default_gate._checks",
            [lambda c, ctx: GateResult("a", False, "warn", "小心")],
        )
        result = run_termination_gate("test", dummy_ctx)
        assert result.startswith("warn:")

    def test_fail_returns_fail(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.verifier._default_gate._checks",
            [lambda c, ctx: GateResult("a", False, "fail", "错了")],
        )
        result = run_termination_gate("test", dummy_ctx)
        assert result.startswith("fail:")

    def test_fail_beats_warn(self, dummy_ctx, monkeypatch):
        monkeypatch.setattr(
            "src.verifier._default_gate._checks",
            [
                lambda c, ctx: GateResult("a", False, "warn", "小心"),
                lambda c, ctx: GateResult("b", False, "fail", "错了"),
            ],
        )
        result = run_termination_gate("test", dummy_ctx)
        assert result.startswith("fail:")


# ── _fold_stale_tool_results_in_log ──


class TestFoldStaleToolResults:
    """写时折叠：保留最近 N 条 tool 结果，更早的折叠。"""

    def test_under_limit_no_fold(self, dummy_ctx):
        from src.llm import _fold_stale_tool_results_in_log

        dummy_ctx.log = [
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 500, "_tool_name": "read_file"},
            {"role": "tool", "tool_call_id": "t2", "content": "y" * 500, "_tool_name": "write_file"},
        ]
        _fold_stale_tool_results_in_log(dummy_ctx)
        # 2 条 ≤ 8 条保留上限 → 不折叠
        assert "x" * 500 in dummy_ctx.log[0]["content"]
        assert "y" * 500 in dummy_ctx.log[1]["content"]

    def test_over_limit_folds_old(self, dummy_ctx):
        from src.llm import _fold_stale_tool_results_in_log

        # 10 条 tool 结果，保留最近 8 条
        dummy_ctx.log = [
            {"role": "tool", "tool_call_id": f"t{i}", "content": "a" * 600, "_tool_name": "read_file"}
            for i in range(10)
        ]
        _fold_stale_tool_results_in_log(dummy_ctx)
        # 最近 8 条（索引 2-9）不折叠
        for i in range(2, 10):
            assert dummy_ctx.log[i]["content"] == "a" * 600
        # 最早 2 条（索引 0-1）折叠
        for i in range(2):
            assert "旧工具结果已清除" in dummy_ctx.log[i]["content"]

    def test_short_result_not_folded(self, dummy_ctx):
        from src.llm import _fold_stale_tool_results_in_log

        dummy_ctx.log = [
            {"role": "tool", "tool_call_id": f"t{i}", "content": "ok", "_tool_name": "self"}
            for i in range(12)  # 全部 ≤ 300 字符，不过阈值
        ]
        _fold_stale_tool_results_in_log(dummy_ctx)
        for i in range(12):
            assert dummy_ctx.log[i]["content"] == "ok"

    def test_mixed_roles_not_touched(self, dummy_ctx):
        from src.llm import _fold_stale_tool_results_in_log

        dummy_ctx.log = [
            {"role": "user", "content": "做某事"},
            {"role": "assistant", "content": "好的", "tool_calls": []},
            *[
                {"role": "tool", "tool_call_id": f"t{i}", "content": "b" * 600, "_tool_name": "read_file"}
                for i in range(9)
            ],
        ]
        _fold_stale_tool_results_in_log(dummy_ctx)
        # user/assistant 不动
        assert dummy_ctx.log[0]["content"] == "做某事"
        assert dummy_ctx.log[1]["content"] == "好的"
        # 最早一条 tool 折叠
        assert "旧工具结果已清除" in dummy_ctx.log[2]["content"]
        # 最近 8 条 tool 不折叠
        for i in range(3, 11):
            assert dummy_ctx.log[i]["content"] == "b" * 600
