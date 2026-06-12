"""session_pins 单元测试:写入限短、封顶整条淘汰、去重、render、清空。

核心保证:封顶绝不把某条 pin 切成半句——每条写入侧限短(整条 ≤ 上限),
总量超限踢"最旧整条",所以钉板上每条要么完整在、要么整条不在。
"""
from __future__ import annotations

import src.session_pins as sp
from src.params import PINBOARD


def setup_function(_fn):
    sp.clear_pins()


def teardown_function(_fn):
    sp.clear_pins()


def test_add_and_render():
    sp.add_pin("决定A")
    sp.add_pin("决定B")
    out = sp.render_tail()
    assert sp.PINBOARD_MARKER in out
    assert "决定A" in out and "决定B" in out


def test_empty_render_is_none():
    assert sp.render_tail() is None


def test_dedup_same_text():
    sp.add_pin("同一条")
    sp.add_pin("同一条")
    assert len(sp.list_pins()) == 1


def test_truncate_long_pin_never_exceeds_cap():
    sp.add_pin("字" * (PINBOARD.max_chars + 50))
    (p,) = sp.list_pins()
    assert len(p["text"]) <= PINBOARD.max_chars  # 整条限短,绝不超过上限


def test_cap_evicts_oldest_whole_pin():
    for i in range(PINBOARD.max_items + 3):
        sp.add_pin(f"决定{i}")
    pins = sp.list_pins()
    assert len(pins) == PINBOARD.max_items            # 总量封顶
    texts = [p["text"] for p in pins]
    assert "决定0" not in texts                        # 最旧整条被踢
    assert f"决定{PINBOARD.max_items + 2}" in texts    # 最新仍在
    assert all(t.startswith("决定") for t in texts)    # 无一条被切成半句


def test_unpin_removes_by_substring():
    sp.add_pin("走内存易失版")
    assert "已取下" in sp.remove_pin("易失")
    assert sp.list_pins() == []


def test_unpin_miss_returns_notice():
    assert "未找到" in sp.remove_pin("不存在的")


def test_clear_pins():
    sp.add_pin("x")
    sp.clear_pins()
    assert sp.render_tail() is None


def test_durable_flag_persists_on_redundant_add():
    sp.add_pin("耐久决定", durable=False)
    sp.add_pin("耐久决定", durable=True)  # 重复钉但升级 durable
    (p,) = sp.list_pins()
    assert p["durable"] is True


def test_is_pinboard_msg():
    sp.add_pin("y")
    assert sp.is_pinboard_msg({"role": "system", "content": sp.render_tail()})
    assert not sp.is_pinboard_msg({"role": "system", "content": "别的系统消息"})
    assert not sp.is_pinboard_msg({"role": "user", "content": sp.render_tail()})


def test_promote_only_durable(monkeypatch):
    """只有 durable 的 pin 转存进 memory，type=strategy，附 fingerprint 合并。"""
    import src.tools.memory as mem
    calls: list[tuple] = []
    monkeypatch.setattr(
        mem, "_remember",
        lambda name, content, mtype, fingerprint=None: calls.append(
            (name, content, mtype, fingerprint)) or "ok",
    )
    sp.add_pin("耐久决定X", durable=True)
    sp.add_pin("普通决定Y", durable=False)
    assert sp.promote_durable() == 1
    assert len(calls) == 1
    name, content, mtype, fp = calls[0]
    assert content == "耐久决定X"
    assert mtype == "strategy"
    assert isinstance(fp, str)
    assert len(fp) == 12
    assert name.startswith("pin-")


def test_promote_name_deterministic_for_dedup():
    """同文本 → 同 name(跨会话转存覆盖去重,不累积重复记忆)。"""
    assert sp._promote_name("同样的决定") == sp._promote_name("同样的决定")
    assert sp._promote_name("决定A") != sp._promote_name("决定B")
