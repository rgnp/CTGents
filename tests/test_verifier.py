"""Verifier 模块测试。"""

from types import SimpleNamespace

import src.llm as llm
from src.cache_context import CacheContext
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

    # ── 弱信号（仅反引号引用、无动作动词）只 warn，绝不 fail 卡 loop ──
    # 失败类：评审/讲解轮反引号引用已有文件 → 曾被硬 fail 困住（实测复现）。

    def test_backtick_only_missing_file_warns_not_fails(self, dummy_ctx, monkeypatch):
        """无动词、仅反引号引用一个不存在的文件 → warn（不是 fail）。"""
        monkeypatch.setattr("src.verifier._get_git_changes", lambda: (set(), set(), True))
        result = check_claim_evidence(
            "建议参考 `src/tools/nonexistent_abc_xyz.py` 的做法。",
            dummy_ctx,
        )
        assert result.severity == "warn"

    def test_backtick_only_unchanged_file_warns_not_fails(self, dummy_ctx, monkeypatch):
        """无动词、反引号引用一个存在但本轮没改的文件（工作区别处脏）→ warn。"""
        monkeypatch.setattr(
            "src.verifier._get_git_changes",
            lambda: ({"other.py"}, set(), True),
        )
        result = check_claim_evidence(
            "它依赖 `AGENTS.md` 里的约定。",  # 引用，非完成声明
            dummy_ctx,
        )
        assert result.severity == "warn"

    def test_verb_bound_still_fails_even_with_backtick_ref(self, dummy_ctx, monkeypatch):
        """强信号（动词绑定）缺证据仍判 fail，不被弱信号规则放水。"""
        monkeypatch.setattr("src.verifier._get_git_changes", lambda: (set(), set(), True))
        result = check_claim_evidence(
            "我创建了 `nonexistent_abc_xyz.py`，可参考 `AGENTS.md`。",
            dummy_ctx,
        )
        assert result.severity == "fail"
        assert "nonexistent_abc_xyz.py" in result.message


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


class TestTerminationGateLoopIntegration:
    """producer/consumer 接缝：fail 必须带反馈重跑，不能静默放行。"""

    def test_fail_feedback_reaches_retry_request(self, monkeypatch):
        backend = SimpleNamespace(info=SimpleNamespace(name="Fake", supports_tools=True))
        responses = iter([
            ("我创建了 missing.py", [], {}),
            ("更正：没有创建该文件。", [], {}),
        ])
        payloads: list[list[dict]] = []
        gate_actions = iter(["fail:missing.py 不存在", "pass"])

        def fake_eager(_backend, messages, *_args, **_kwargs):
            payloads.append(messages)
            return next(responses)

        monkeypatch.setattr(llm, "auto_select_model", lambda _q: backend)
        monkeypatch.setattr(llm, "_reconcile_system_context", lambda _ctx: None)
        monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
        monkeypatch.setattr(llm, "_run_termination_gate", lambda *_a: next(gate_actions))

        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "p"}])
        result = llm.run_conversation(
            ctx,
            "完成任务",
            on_token=lambda _t: None,
            on_tool=lambda *_a: None,
            max_requests=3,
        )

        assert result == "更正：没有创建该文件。"
        assert len(payloads) == 2
        assert any(
            m.get("role") == "system"
            and "missing.py 不存在" in (m.get("content") or "")
            for m in payloads[1]
        ), "终止门失败原因必须进入重试请求"


