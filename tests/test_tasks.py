"""长任务状态测试：current.md 判活/注入/归档/清空 + /task 命令 + 自动归档 + 目标锚点。

全程把 tasks 路径指向 tmp_path，绝不触碰真实 tasks/current.md。
"""

from datetime import UTC, datetime, timedelta

import pytest

import src.asset_usage as asset_usage
import src.tasks as tasks
import src.work_receipts as work_receipts
from src.cache_context import CacheContext

pytestmark = pytest.mark.slow

@pytest.fixture(autouse=True)
def _isolate_tasks(tmp_path, monkeypatch):
    current = tmp_path / "current.md"
    archive = tmp_path / "archive"
    monkeypatch.setattr(tasks, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", current)
    ambitions = tmp_path / "ambitions.md"
    monkeypatch.setattr(tasks, "AMBITIONS_FILE", ambitions)
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(asset_usage, "USAGE_FILE", tmp_path / "asset-usage.jsonl")
    monkeypatch.setattr(work_receipts, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        work_receipts,
        "WORK_RECEIPTS_FILE",
        tmp_path / "work-receipts.jsonl",
    )
    return current, archive

_UNFINISHED = (
    "# 长任务：抓论文\n\n"
    "# 目标锚点\n找到最新的轨迹预测论文并分析其方法论。\n\n"
    "- [o] Step 1: 搜索 47/250\n- [ ] Step 2: 去重\n"
)
_DONE = "# 长任务：抓论文\n\n# 目标锚点\n找论文。\n\n- [x] Step 1\n- [x] Step 2\n"
_HAS_RETRY = "# 出问题了\n\n# 目标锚点\n修复。\n\n- [r] Step 1: 验证失败\n"
_HAS_BLOCKED = "# 等确认\n\n# 目标锚点\n等。\n\n- [!] Step 1: 等用户确认\n"
_ANCHORED_UNFINISHED = _UNFINISHED

def test_has_unfinished_true(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    assert tasks.has_unfinished() is True


@pytest.mark.parametrize("content,desc", [
    (_DONE, "全 [x]"),
    ("__absent__", "文件不存在"),
    (_HAS_RETRY, "全 [r]"),
    (_HAS_BLOCKED, "全 [!]"),
], ids=["done", "missing", "retry", "blocked"])
def test_has_unfinished_false(content, desc, _isolate_tasks):
    if content != "__absent__":
        _isolate_tasks[0].write_text(content, encoding="utf-8")
    assert tasks.has_unfinished() is False
    assert tasks.has_unfinished() is False

class TestCreateTask:
    def test_appends_archive_step(self, _isolate_tasks):
        current, _ = _isolate_tasks
        result = tasks.create_task("# 测试\n\n# 目标锚点\n做某事。\n\n- [ ] Step 1\n")
        assert "已写入" in result
        content = current.read_text(encoding="utf-8")
        assert "归档 current.md" in content

    def test_does_not_double_append(self, _isolate_tasks):
        current, _ = _isolate_tasks
        result = tasks.create_task(
            "# 测试\n\n# 目标锚点\n做某事。\n\n"
            "- [ ] Step 1\n- [ ] 归档 current.md → tasks/archive/\n"
        )
        assert "已写入" in result
        content = current.read_text(encoding="utf-8")
        assert content.count("归档 current.md") == 1

    def test_rejects_without_anchor(self, _isolate_tasks):
        """没有 # 目标锚点 → 拒绝写入，文件不被创建。"""
        result = tasks.create_task("# 测试\n\n- [ ] Step 1\n")
        assert "拒绝" in result
        assert "# 目标锚点" in result
        assert not _isolate_tasks[0].exists()

class TestExtractAnchor:
    def test_simple_anchor(self):
        anchor = tasks._extract_anchor("# 目标锚点\n一句话目标。\n\n正文")
        assert anchor == "一句话目标。"

    def test_multiline_anchor(self):
        """锚点只取第一行非空文本（不包含后续行）。"""
        anchor = tasks._extract_anchor("# 目标锚点\n第一行。\n第二行。\n\n正文")
        assert anchor == "第一行。", "锚点只取目标锚点之后第一行非空文本"

    def test_no_anchor(self):
        assert tasks._extract_anchor("无锚点内容") == ""

    def test_anchor_stops_at_next_heading(self):
        anchor = tasks._extract_anchor("# 目标锚点\n某目标。\n## 步骤\n- [ ] 1")
        assert anchor == "某目标。"


class TestAcceptanceContract:
    def test_legacy_task_has_no_acceptance_gate(self):
        result = tasks.evaluate_acceptance(_DONE)
        assert result.configured is False
        assert result.passed is True

    def test_parse_structured_acceptance_rules(self):
        text = (
            "# 目标锚点\n交付结果。\n\n- [x] 实现\n\n## 验收\n\n"
            "- `steps`\n"
            "- `file: docs/report.md`\n"
            "- `command: py -m pytest tests/test_tasks.py -q`\n"
        )
        spec = tasks.parse_task_spec(text)
        assert spec.goal == "交付结果。"
        assert [(item.kind, item.value) for item in spec.acceptance] == [
            ("steps", ""),
            ("file", "docs/report.md"),
            ("command", "py -m pytest tests/test_tasks.py -q"),
        ]

    def test_declared_but_unstructured_acceptance_fails(self):
        text = f"{_DONE}\n## 验收\n\n- 看起来没问题\n"
        result = tasks.evaluate_acceptance(text)
        assert result.configured is True
        assert result.passed is False
        assert "没有结构化规则" in result.render()

    def test_steps_rule_detects_unfinished_body(self):
        text = f"{_UNFINISHED}\n## 验收\n\n- `steps`\n"
        result = tasks.evaluate_acceptance(text)
        assert result.passed is False
        assert "未完成或阻塞" in result.render()

    def test_file_rule_is_workspace_bounded(self, _isolate_tasks):
        current, _ = _isolate_tasks
        report = current.parent / "report.md"
        report.write_text("ok", encoding="utf-8")
        passed = tasks.evaluate_acceptance(
            f"{_DONE}\n## 验收\n\n- `file: report.md`\n"
        )
        escaped = tasks.evaluate_acceptance(
            f"{_DONE}\n## 验收\n\n- `file: ../outside.md`\n"
        )
        assert passed.passed is True
        assert escaped.passed is False
        assert "越出项目目录" in escaped.render()

    def test_command_rule_records_success_and_failure(self, monkeypatch):
        outcomes = iter([(True, "退出码 0"), (False, "退出码 1")])
        monkeypatch.setattr(tasks, "_run_acceptance_command", lambda _cmd: next(outcomes))
        text = f"{_DONE}\n## 验收\n\n- `command: py -m pytest -q`\n"
        assert tasks.evaluate_acceptance(text).passed is True
        assert tasks.evaluate_acceptance(text).passed is False

    def test_command_rule_reuses_valid_receipt(self, monkeypatch):
        import src.verification_receipts as receipts

        receipt = receipts.VerificationReceipt(
            command="[]",
            workdir=str(tasks.PROJECT_ROOT),
            workspace_fingerprint="state",
            runtime="runtime",
            passed=True,
            exit_code=0,
            timestamp="2026-07-27T10:00:00+00:00",
            output_tail="86 passed",
        )
        monkeypatch.setattr(receipts, "find_valid_receipt", lambda *_a, **_k: receipt)
        monkeypatch.setattr(
            tasks.subprocess,
            "run",
            lambda *_a, **_k: pytest.fail("有效回执命中时不应重复执行命令"),
        )
        passed, evidence = tasks._run_acceptance_command("py -m pytest -q")
        assert passed is True
        assert "复用验证回执" in evidence
        assert "86 passed" in evidence

    def test_command_rule_records_fresh_execution(self, monkeypatch):
        import src.verification_receipts as receipts

        class Result:
            returncode = 0
            stdout = "5 passed"
            stderr = ""

        recorded = []
        monkeypatch.setattr(receipts, "find_valid_receipt", lambda *_a, **_k: None)
        monkeypatch.setattr(
            receipts,
            "record_verification",
            lambda *args: recorded.append(args),
        )
        monkeypatch.setattr(tasks.subprocess, "run", lambda *_a, **_k: Result())
        passed, evidence = tasks._run_acceptance_command("py -m pytest -q")
        assert passed is True
        assert "新执行" in evidence
        assert recorded and recorded[0][2] == 0

    def test_unsafe_acceptance_command_is_rejected(self):
        passed, evidence = tasks._run_acceptance_command("python -c \"print('x')\"")
        assert passed is False
        assert "只允许" in evidence

    def test_archive_blocked_on_failed_acceptance(self, _isolate_tasks):
        current, archive = _isolate_tasks
        current.write_text(
            f"{_DONE}\n## 验收\n\n- `file: missing.md`\n",
            encoding="utf-8",
        )
        result = tasks.archive_current_if_accepted()
        assert result.startswith("❌")
        assert current.read_text(encoding="utf-8").strip()
        assert not archive.exists()
        receipt = work_receipts._read_receipts()[-1]
        assert receipt.stage == "failed"
        assert receipt.workspace_fingerprint

    def test_task_done_is_rejected_when_contract_fails(self, _isolate_tasks):
        from src.tools.control import execute

        current, _ = _isolate_tasks
        current.write_text(
            f"{_DONE}\n## 验收\n\n- `file: missing.md`\n",
            encoding="utf-8",
        )
        result = execute("task_done", {"summary": "完成"})
        assert result.startswith("❌")
        assert "任务完成信号" not in result
        assert current.read_text(encoding="utf-8").strip()

    def test_successful_acceptance_receipt_is_archived(
        self,
        _isolate_tasks,
        monkeypatch,
    ):
        current, archive = _isolate_tasks
        current.write_text(
            f"{_DONE}\n## 验收\n\n- `steps`\n"
            "- `command: py -m pytest tests/test_tasks.py -q`\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            tasks,
            "_run_acceptance_command",
            lambda _cmd: (True, "退出码 0；12 passed"),
        )
        result = tasks.archive_current_if_accepted()
        assert "验收通过" in result
        archived = list(archive.glob("*.md"))
        assert len(archived) == 1
        archived_text = archived[0].read_text(encoding="utf-8")
        assert "## 验收结果" in archived_text
        assert "12 passed" in archived_text
        assert current.read_text(encoding="utf-8") == ""
        receipt = work_receipts._read_receipts()[-1]
        assert receipt.stage == "completed"
        assert any(item.path.startswith("archive/") for item in receipt.artifacts)


class TestStopContract:
    def test_legacy_task_uses_runtime_defaults(self):
        policy = tasks.resolve_stop_policy(_UNFINISHED)
        assert policy.budget is None
        assert policy.stall_limit is None
        assert policy.deadline is None
        assert policy.errors == ()

    def test_parse_valid_stop_policy(self):
        text = (
            f"{_UNFINISHED}\n## 停止条件\n\n"
            "- `budget: 8`\n"
            "- `stall: 2`\n"
            "- `deadline: 2026-07-28T18:00:00+08:00`\n"
        )
        policy = tasks.resolve_stop_policy(text)
        assert policy.budget == 8
        assert policy.stall_limit == 2
        assert policy.deadline is not None
        assert policy.deadline.isoformat() == "2026-07-28T18:00:00+08:00"
        assert policy.errors == ()

    @pytest.mark.parametrize(
        ("rules", "expected"),
        [
            ("- `budget: 0`", "budget 必须在"),
            ("- `stall: 11`", "stall 必须在"),
            ("- `deadline: someday`", "ISO 8601"),
            ("- `deadline: 2026-07-28T18:00:00`", "必须带时区"),
            ("- `unknown: 1`", "未知停止条件"),
            ("- `budget: 2`\n- `budget: 3`", "停止条件重复"),
            ("这里以后再写", "没有结构化规则"),
        ],
    )
    def test_invalid_stop_policy_fails_closed(self, rules, expected):
        policy = tasks.resolve_stop_policy(f"{_UNFINISHED}\n## 停止条件\n\n{rules}\n")
        assert policy.errors
        assert expected in "\n".join(policy.errors)

    def test_deadline_reached_is_deterministic(self):
        deadline = datetime.now(UTC)
        policy = tasks.StopPolicy(deadline=deadline)
        assert tasks.deadline_reached(policy, deadline - timedelta(seconds=1)) is False
        assert tasks.deadline_reached(policy, deadline) is True

# make_task_context_message 已删除（dormant 孤儿，2026-06-24）——锚点注入/自动归档/未完成提醒
# 的活路径分别由 _extract_anchor 单测、task_loop、resume_reminder(test_main) 覆盖。

def test_archive_moves_and_clears(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    result = tasks.archive_current("ad-papers")
    assert "已归档" in result
    assert (archive).exists()
    archived = list(archive.glob("*-ad-papers.md"))
    assert len(archived) == 1
    assert "Step 1" in archived[0].read_text(encoding="utf-8")
    assert current.read_text(encoding="utf-8") == ""

def test_archive_derives_slug_from_title(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.archive_current()
    assert len(list(archive.glob("*.md"))) == 1

def test_clear_empties_without_archive(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.clear_current()
    assert current.read_text(encoding="utf-8") == ""
    assert not archive.exists()
    assert work_receipts._read_receipts()[-1].stage == "abandoned"


def test_completed_task_receipt_links_gap_without_copying_gap_state(
    _isolate_tasks,
    monkeypatch,
):
    current, _ = _isolate_tasks
    current.write_text(
        f"{_DONE}\n来源：gap abc123def456\n\n## 验收\n\n- `steps`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "_run_acceptance_command", lambda _cmd: (True, "unused"))

    tasks.archive_current_if_accepted()

    receipt = work_receipts._read_receipts()[-1]
    assert receipt.links == ("gap:abc123def456",)


def test_failed_acceptance_records_asset_outcome(_isolate_tasks, monkeypatch):
    current, _ = _isolate_tasks
    current.write_text(f"{_DONE}\n## 验收\n\n- `file: missing.md`\n", encoding="utf-8")
    recorded = []
    monkeypatch.setattr(asset_usage, "record_task_outcome", lambda *args, **kwargs: recorded.append((args, kwargs)))

    tasks.archive_current_if_accepted()

    assert recorded and recorded[0][0][0] == "failed"


def test_clear_records_abandoned_asset_outcome(_isolate_tasks, monkeypatch):
    current, _ = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    recorded = []
    monkeypatch.setattr(asset_usage, "record_task_outcome", lambda *args, **kwargs: recorded.append((args, kwargs)))

    tasks.clear_current()

    assert recorded and recorded[0][0][0] == "abandoned"

class TestTaskCommand:
    def test_view_empty(self, _isolate_tasks):
        import src.commands as cmds
        r = cmds.dispatch("/task", CacheContext(), None)
        assert "无长任务" in r.message

    def test_view_shows_content(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task", CacheContext(), None)
        assert "抓论文" in r.message

    def test_clear_subcommand(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task clear", CacheContext(), None)
        assert "已清空" in r.message
        assert _isolate_tasks[0].read_text(encoding="utf-8") == ""

    def test_archive_subcommand(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task archive ad-papers", CacheContext(), None)
        assert "已归档" in r.message
