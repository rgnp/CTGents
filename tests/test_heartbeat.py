"""heartbeat——机械预检/自暂停/每日上限/锁/摘要生命周期/出处闸/隔离契约。

worker 用 fake run_conversation 替身（模块级哨兵确保零真 LLM 调用）；
FRONTIER_FILE/HEARTBEAT_DIR/TASKS_DIR 全部重定向 tmp_path，不碰真 tasks/knowledge。
"""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

import src.heartbeat as hb
import src.llm as llm_mod
import src.work_receipts as work_receipts
from src.params import HEARTBEAT


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """哨兵：漏网的真 LLM 调用直接炸；路径全指 tmp。"""
    import src.paths as paths

    def _boom(*_a, **_k):
        raise AssertionError("测试不允许真 LLM 调用")
    monkeypatch.setattr(llm_mod, "_invoke_llm_eager", _boom)
    monkeypatch.setattr(hb, "FRONTIER_FILE", tmp_path / "tasks" / "frontier.md")
    monkeypatch.setattr(hb, "HEARTBEAT_DIR", tmp_path / "tasks" / "heartbeat")
    monkeypatch.setattr(hb, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(work_receipts, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        work_receipts,
        "WORK_RECEIPTS_FILE",
        tmp_path / "tasks" / "work-receipts.jsonl",
    )
    (tmp_path / "tasks").mkdir()
    llm_mod.clear_interrupt()
    yield
    llm_mod.clear_interrupt()
    # worker 的 inject_psyche 会写全局 system_context 注册表，测试间清干净
    from src import system_context
    system_context.reset()


_URL = "https://arxiv.org/abs/2401.12345"
_CARD_REL = "knowledge/test/card.md"
_CARD_TEXT = ("方法与证据。" * 40) + f"\n[已核] 论文提出闭环评测协议（{_URL}）"

_ACTIVE_FRONTIER = (
    "# 探索前沿\n\n# 目标锚点\n摸清世界模型闭环评测\n\n## 方向\n\n"
    "- [ ] 扫近一月论文筛 3 篇精读\n\n## 候选方向（心跳发现的线索，等你转正）\n"
)


def _seed_frontier(text: str = _ACTIVE_FRONTIER) -> None:
    hb.FRONTIER_FILE.parent.mkdir(parents=True, exist_ok=True)
    hb.FRONTIER_FILE.write_text(text, encoding="utf-8")


def _make_fake_worker(tmp_path, *, card_text=_CARD_TEXT, advance_frontier=True,
                      report=f"精读一篇，卡片已存。下一步：读第二篇。（{_URL}）"):
    """Fake run_conversation：模拟 worker 读页+写卡片+更新 frontier。记录每次调用。"""
    calls = []

    def fake_run(ctx, prompt, **kwargs):
        calls.append({"ctx": ctx, "prompt": prompt, **kwargs})
        on_tool = kwargs["on_tool"]
        ctx.log.append({"role": "user", "content": prompt})
        ctx.log.append({"role": "assistant", "tool_calls": [{
            "function": {"name": "read_page", "arguments": json.dumps({"url": _URL})},
        }]})
        ctx.log.append({"role": "tool", "content": f"论文正文…… {_URL}"})
        if card_text is not None:
            on_tool("write_file", {"path": _CARD_REL, "content": card_text})
            card = tmp_path / _CARD_REL
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_text(card_text, encoding="utf-8")
        if advance_frontier:
            on_tool("write_file", {"path": "tasks/frontier.md", "content": "updated"})
            hb.FRONTIER_FILE.write_text(
                _ACTIVE_FRONTIER.replace("- [ ]", "- [o]"), encoding="utf-8")
        ctx.log.append({"role": "assistant", "content": report})
        return report

    return fake_run, calls


# ── 机械预检：没活干零 LLM（哨兵在，走到 LLM 会炸）──

class TestPrecheck:
    def test_silent_without_frontier(self):
        assert "静默" in hb.run_once()

    def test_silent_without_active_markers(self):
        _seed_frontier("# 探索前沿\n\n## 方向\n\n- [x] 已完成\n- [r] 停牌\n")
        assert "静默" in hb.run_once()

    def test_disabled_by_env_knob(self, monkeypatch):
        _seed_frontier()
        monkeypatch.setattr(hb, "HEARTBEAT", dataclasses.replace(HEARTBEAT, enabled=False))
        assert "禁用" in hb.run_once()

    def test_has_active_work_markers(self):
        assert not hb.has_active_work("")
        assert not hb.has_active_work("- [x] done")
        assert hb.has_active_work("- [ ] todo")
        assert hb.has_active_work("- [o] doing")


# ── happy path：干活落盘 + 过闸 + 摘要 ──

class TestHappyPath:
    def test_run_writes_digest_and_state(self, monkeypatch, tmp_path):
        _seed_frontier()
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        outcome = hb.run_once()
        assert "完成一跳" in outcome
        assert len(calls) == 1, "过闸即不重试"
        digest = hb.pending_digest()
        assert digest and "✅ 出处闸: 通过" in digest
        assert _CARD_REL in digest
        assert "下一步" in digest              # worker 小结进摘要
        state = json.loads((hb.HEARTBEAT_DIR / "state.json").read_text(encoding="utf-8"))
        assert state["runs_today"] == 1
        assert state["stalls"] == 0            # frontier 变了 → 不算空转
        receipt = work_receipts._read_receipts()[-1]
        assert receipt.stage == "completed"
        assert receipt.source == "heartbeat"
        assert receipt.artifacts[0].path == _CARD_REL

    def test_isolation_contract(self, monkeypatch, tmp_path):
        _seed_frontier()
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        hb.run_once()
        kw = calls[0]
        assert kw["session_id"] == ""
        assert kw["track_stats"] is False
        assert kw["max_requests"] == HEARTBEAT.worker_max_requests
        assert kw["ctx"].prefix, "worker 前缀必须非空（角色）"
        assert "frontier.md" in kw["prompt"]

    def test_psyche_hard_load_is_event_based(self, monkeypatch, tmp_path):
        """Psyche 走 _psyche_event 事件注入（activate_skill 的 owner 检查才可过），非拼原文。"""
        _seed_frontier()
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        hb.run_once()
        worker_ctx = calls[0]["ctx"]
        loaded = {m["_psyche_event"]["id"] for m in worker_ctx.log if m.get("_psyche_event")}
        assert HEARTBEAT.psyche in loaded, "worker 必须带事件化激活的领域 psyche"

    def test_psyche_load_failure_fails_closed(self, monkeypatch):
        """Psyche 加载不上 → 本跳中止记失败，不带病干活（防垃圾产出的牙）。"""
        _seed_frontier()
        monkeypatch.setattr(hb, "HEARTBEAT",
                            dataclasses.replace(HEARTBEAT, psyche="不存在的psyche"))
        outcome = hb.run_once()
        assert "失败" in outcome and "psyche 硬加载失败" in outcome
        assert "异常中止" in (hb.pending_digest() or "")


# ── 出处闸：编造 URL 打回重试，仍不过如实进摘要 ──

class TestGate:
    def test_fabricated_url_fails_gate_after_retry(self, monkeypatch, tmp_path):
        _seed_frontier()
        bad_card = ("方法与证据。" * 40) + "\n[已核] 某论文说 X（https://example.com/fake-paper）"
        fake, calls = _make_fake_worker(tmp_path, card_text=bad_card)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        hb.run_once()
        assert len(calls) == 2, "闸未过应打回重试一次"
        assert calls[1]["max_requests"] == HEARTBEAT.retry_max_requests
        assert calls[1]["prompt"].startswith("⛔ 出处闸未通过"), "反馈前缀须与闸的自洗白剔除约定一致"
        digest = hb.pending_digest()
        assert digest and "⛔ 出处闸: 未通过" in digest, "仍不过要如实进摘要，不静默放行"
        assert work_receipts._read_receipts()[-1].stage == "failed"

    def test_gate_skips_non_knowledge_files(self, monkeypatch, tmp_path):
        """Frontier 等非 knowledge/ 写入不过闸（闸只管调研产出）。"""
        _seed_frontier()
        fake, calls = _make_fake_worker(tmp_path, card_text=None)  # 只更新 frontier
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        hb.run_once()
        assert len(calls) == 1
        assert "✅ 出处闸: 通过" in (hb.pending_digest() or "")


# ── 防空转：连续无推进自暂停，frontier 改动自动恢复 ──

class TestStallAndPause:
    def test_pause_after_stalls_then_resume_on_edit(self, monkeypatch, tmp_path):
        _seed_frontier()
        fake, calls = _make_fake_worker(tmp_path, advance_frontier=False)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        hb.run_once()
        assert not hb._is_paused(hb._load_state())
        assert work_receipts._read_receipts()[-1].stage == "failed"
        outcome = hb.run_once()
        assert "自暂停" in outcome
        assert "自暂停" in (hb.pending_digest() or "")
        # 暂停期：静默不跑
        assert "静默" in hb.run_once()
        assert len(calls) == 2
        # 用户改动 frontier（mtime 晚于 paused_at）→ 自动恢复
        state = hb._load_state()
        state["paused_at"] = time.time() - 10
        hb._save_state(state)
        _seed_frontier(_ACTIVE_FRONTIER + "\n- [ ] 新种一项\n")
        assert "完成一跳" in hb.run_once()
        assert len(calls) == 3

    def test_worker_exception_recorded_not_silent(self, monkeypatch):
        """无人值守的异常必须落摘要+状态，且锁要释放。"""
        _seed_frontier()
        def _explode(*_a, **_k):
            raise RuntimeError("网炸了")
        monkeypatch.setattr(llm_mod, "run_conversation", _explode)
        outcome = hb.run_once()
        assert "失败" in outcome and "网炸了" in outcome
        assert "异常中止" in (hb.pending_digest() or "")
        assert not (hb.HEARTBEAT_DIR / "lock").exists(), "异常后锁必须释放"
        assert hb._load_state()["runs_today"] == 1, "失败也计入每日次数，防热崩循环烧钱"
        assert work_receipts._read_receipts()[-1].stage == "failed"


# ── 成本兜底：每日上限 + 防重叠锁 ──

class TestBudgetAndLock:
    def test_daily_cap_and_force_bypass(self, monkeypatch, tmp_path):
        _seed_frontier()
        hb._save_state({"date": time.strftime("%Y-%m-%d"),
                        "runs_today": HEARTBEAT.max_runs_per_day})
        assert "上限" in hb.run_once()
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        assert "完成一跳" in hb.run_once(force=True)

    def test_fresh_lock_blocks_stale_lock_yields(self, monkeypatch, tmp_path):
        import os
        _seed_frontier()
        hb.HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        lock = hb.HEARTBEAT_DIR / "lock"
        lock.write_text("123", encoding="utf-8")
        assert "锁" in hb.run_once()
        # 陈旧锁（mtime 拨回 3 小时）→ 可抢占
        old = time.time() - 3 * 3600
        os.utime(lock, (old, old))
        fake, _calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        assert "完成一跳" in hb.run_once()


# ── 摘要生命周期 ──

class TestDigest:
    def test_append_pending_consume_archive(self):
        hb._append_digest("第一跳小结")
        hb._append_digest("第二跳小结")
        text = hb.pending_digest()
        assert text and "第一跳小结" in text and "第二跳小结" in text
        consumed = hb.consume_digest()
        assert consumed == text
        assert hb.pending_digest() is None, "消费后 pending 清空"
        assert hb.consume_digest() is None, "二次消费返回 None"
        archive = (hb.HEARTBEAT_DIR / "digest-archive.md").read_text(encoding="utf-8")
        assert "第一跳小结" in archive
        delivery = work_receipts.latest_pending_delivery()
        assert delivery is not None and delivery.stage == "delivered"
        assert work_receipts.resolve_latest_delivery("accept", "内容有效").startswith("✅")
        assert work_receipts.latest_pending_delivery() is None


# ── 无人期白名单：不许有动系统状态的工具 ──

class TestWhitelist:
    def test_worker_tools_exclude_side_effects(self):
        names = {t["function"]["name"] for t in hb._worker_tools()}
        forbidden = {"run_command", "run_python", "git_commit", "git_push",
                     "delete_file", "move_file", "replace_in_file", "delegate",
                     "remember", "forget", "run_async"}
        assert not names & forbidden, f"无人期白名单混入危险工具: {names & forbidden}"
        assert {"search_web", "read_page", "write_file", "scan_papers", "read_paper"} <= names

    def test_worker_tools_include_skill_runtime_and_pipeline_tools(self):
        """paper-pipeline 无人跑通所需：skill 运行时 + 下载/转写窄工具。"""
        names = {t["function"]["name"] for t in hb._worker_tools()}
        assert {"psyche_catalog", "load_psyche", "activate_skill",
                "fetch_paper", "transcribe_paper", "learn"} <= names


# ── 状态一览 ──

class TestStatus:
    def test_status_text_reports_frontier_and_digest(self):
        _seed_frontier()
        hb._append_digest("一条摘要")
        text = hb.status_text()
        assert "1 个活跃项" in text
        assert "待送摘要: 有" in text
