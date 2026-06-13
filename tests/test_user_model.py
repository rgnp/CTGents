"""用户理解收割测试：digest 去噪/截尾、跳过条件、清洗，以及落盘 type=user 且真被每轮注入。

关键不变量（防"写了没人读的坟场"/防"形状漂亮的未接线机制"）：
  save_user_profile 写出的档案必须出现在 memory._build_context() 的注入文本里。
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib

import pytest

from src import user_model


@pytest.fixture
def iso_mem(monkeypatch, tmp_path):
    """把记忆目录指向 tmp（读写都走 memory.MEMORY_DIR，单点隔离）。

    取 sys.modules 现行 memory 实例来打补丁，不用模块顶层别名——test_tool_meta
    的 reload_tools() 会换掉 src.tools.memory 实例，顶层别名会变陈旧（补丁打不到
    真正被 _remember 使用的模块上）。yield 出该实例供断言用同一对象。
    """
    mem = importlib.import_module("src.tools.memory")
    import src.config as cfg
    d = tmp_path / "memory"
    d.mkdir()
    monkeypatch.setattr(mem, "MEMORY_DIR", str(d))
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(d))
    mem.mark_dirty()
    return d, mem


def _log(*pairs: tuple[str, str]) -> list[dict]:
    return [{"role": r, "content": c} for r, c in pairs]


def _patch_params(monkeypatch, **kw):
    monkeypatch.setattr(user_model, "USER_MODEL",
                        dataclasses.replace(user_model.USER_MODEL, **kw))


# ── digest / 计数 ──

def test_count_user_messages_ignores_blank():
    log = _log(("user", "a"), ("assistant", "b"), ("user", "c"), ("user", "   "))
    assert user_model._count_user_messages(log) == 2


def test_digest_drops_tool_noise():
    log = [
        {"role": "user", "content": "我想学 X"},
        {"role": "assistant", "content": "好的我查一下", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "一大坨工具结果"},
        {"role": "assistant", "content": "X 的原理是这样"},
    ]
    d = user_model._conversation_digest(log, 9999)
    assert "我想学 X" in d
    assert "X 的原理是这样" in d
    assert "工具结果" not in d        # tool 消息被去掉
    assert "好的我查一下" not in d    # 带 tool_calls 的中间 assistant 被去掉


def test_digest_truncates_to_budget_tail():
    log = _log(("user", "早期消息"), ("user", "结尾消息ABC" * 100))
    d = user_model._conversation_digest(log, 50)
    assert len(d) <= 50 + len("…（前略）\n")
    assert "ABC" in d  # 保尾部近因


# ── 清洗 ──

def test_clean_output_strips_markdown_fence():
    out = user_model._clean_output("```markdown\n## 风格\n- 简洁\n```")
    assert out.startswith("## 风格")
    assert "```" not in out
    assert "markdown" not in out.split("\n")[0]


def test_clean_output_hard_caps_length(monkeypatch):
    _patch_params(monkeypatch, max_profile_chars=10)
    out = user_model._clean_output("x" * 200)
    assert len(out) <= 20  # 硬地板 = max_profile_chars * 2


# ── 跳过条件（绝不触发 LLM）──

def test_skip_when_disabled(monkeypatch):
    _patch_params(monkeypatch, enabled=False)
    # backend 未被 mock：若被调用会真发网络 → 测试环境必炸。返回 None 证明根本没调。
    assert user_model.harvest_user_profile(
        _log(("user", "a"), ("user", "b"), ("user", "c"))) is None


def test_skip_when_too_few_user_messages(monkeypatch):
    _patch_params(monkeypatch, min_user_messages=3)
    assert user_model.harvest_user_profile(_log(("user", "a"), ("user", "b"))) is None


# ── 收割（mock 隔离 backend）──

def test_harvest_calls_backend_isolated_and_cleans(monkeypatch, iso_mem):
    from src import llm
    captured: dict = {}

    def fake(messages, _cb, tools=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return ("```\n## 沟通\n- 偏好简洁中文\n```", None)

    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream", fake)
    log = _log(("user", "请简洁点"), ("user", "继续"), ("user", "好"), ("assistant", "好的"))
    body = user_model.harvest_user_profile(log)

    assert body is not None
    assert "偏好简洁中文" in body
    assert "```" not in body
    assert captured["tools"] is None                      # 无工具的纯对话调用
    payload = captured["messages"][1]["content"]
    assert "现有档案" in payload and "本次对话" in payload  # 喂进了档案+对话


def test_harvest_empty_output_returns_none(monkeypatch, iso_mem):
    from src import llm
    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream",
                        lambda *_a, **_k: ("", None))
    log = _log(("user", "a"), ("user", "b"), ("user", "c"), ("assistant", "x"))
    assert user_model.harvest_user_profile(log) is None


def test_harvest_network_failure_returns_none(monkeypatch, iso_mem):
    from src import llm
    monkeypatch.setattr(user_model.time, "sleep", lambda *_a: None)
    _patch_params(monkeypatch, harvest_retries=1)

    def boom(*_a, **_k):
        raise OSError("net down")  # RETRYABLE 含 OSError

    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream", boom)
    log = _log(("user", "a"), ("user", "b"), ("user", "c"), ("assistant", "x"))
    assert user_model.harvest_user_profile(log) is None


# ── 落盘 + 注入（核心不变量：写了真被读）──

def test_save_profile_type_user_no_severity(iso_mem):
    d, _mem = iso_mem
    assert user_model.save_user_profile("## 沟通\n- 偏好简洁中文回复") is True
    f = d / "user-profile.md"
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert "type: user" in text
    assert "severity" not in text  # 无 severity → 不会被 _build_context 当 lesson 跳过


def test_save_empty_is_noop(iso_mem):
    d, _mem = iso_mem
    assert user_model.save_user_profile("   ") is False
    assert not (d / "user-profile.md").exists()


def test_saved_profile_is_injected_into_context(iso_mem):
    """关键不变量：落盘的档案出现在每轮注入文本里（不是只写不读）。"""
    _d, mem = iso_mem
    user_model.save_user_profile("## 沟通\n- 偏好简洁中文回复")
    ctx = mem._build_context()
    assert ctx is not None
    assert "偏好简洁中文回复" in ctx


def test_harvest_and_save_end_to_end(monkeypatch, iso_mem):
    _d, mem = iso_mem
    from src import llm
    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream",
                        lambda *_a, **_k: ("## 目标\n- 想做属于自己的 Jarvis", None))
    log = _log(("user", "我想要属于自己的助手"), ("user", "继续"),
               ("user", "对"), ("assistant", "懂了"))
    note = user_model.harvest_and_save(log)
    assert note is not None
    assert "Jarvis" in mem._build_context()
