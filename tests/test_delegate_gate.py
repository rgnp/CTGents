"""机械出处闸（delegate_gate）——纯函数测试。

闸的三项检查全部键在客观 referent 上（文件存在/URL 子串命中/read_page 调用记录），
这里钉死每项的通过与拒绝两侧，外加压缩场景（URL 只在 arguments 里仍过闸）。
"""

import json

from src.delegate_gate import (
    collect_read_page_urls,
    extract_urls,
    format_gate_feedback,
    gate_check,
)


def _tc(name: str, args: dict) -> dict:
    return {"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _log_with_search(url: str) -> list[dict]:
    """带一次搜索活动的 worker log：URL 出现在 tool 结果 content 里。"""
    return [
        {"role": "assistant", "tool_calls": [_tc("search_web", {"query": "world model"})]},
        {"role": "tool", "content": f"1. Some Paper\n   摘要……\n   [arxiv.org] {url}"},
    ]


def _write_output(tmp_path, text: str):
    p = tmp_path / "report.md"
    p.write_text(text, encoding="utf-8")
    return p


_LONG = "调研正文。" * 60  # 300 字符，够过 min_output_chars=200


class TestUrlGrounding:
    def test_grounded_url_passes(self, tmp_path):
        url = "https://arxiv.org/abs/2401.00001"
        out = _write_output(tmp_path, f"{_LONG}\n来源: {url}")
        problems = gate_check(f"结论，见 {url}", out, _log_with_search(url))
        assert problems == []

    def test_fabricated_url_rejected(self, tmp_path):
        out = _write_output(tmp_path, f"{_LONG}\n来源: https://fake.example.com/made-up")
        problems = gate_check("结论", out, _log_with_search("https://arxiv.org/abs/2401.00001"))
        assert any("疑似编造来源" in p and "fake.example.com" in p for p in problems)

    def test_url_only_in_arguments_still_grounded(self, tmp_path):
        """压缩场景：tool content 被头尾压缩丢了 URL，但 read_page 的 arguments 永不压缩。"""
        url = "https://arxiv.org/abs/2401.00002"
        log = [
            {"role": "assistant", "tool_calls": [_tc("read_page", {"url": url})]},
            {"role": "tool", "content": "…（前1000字）…（中间省略）…（后1200字）…"},
        ]
        out = _write_output(tmp_path, f"{_LONG}\n[已核] 论文结论 X，来源 {url}")
        assert gate_check(f"结论 {url}", out, log) == []

    def test_extra_evidence_grounds_url(self, tmp_path):
        """Delegate 压缩前取证的 evidence 并入 haystack。"""
        url = "https://openreview.net/forum?id=abc123"
        out = _write_output(tmp_path, f"{_LONG}\n来源: {url}")
        problems = gate_check(
            "结论", out, [], extra_evidence=f'search_web {{"query": "x"}}\n结果含 {url}'
        )
        assert problems == []

    def test_gate_feedback_cannot_self_ground(self, tmp_path):
        """失败类钉死：打回反馈点名编造 URL → 反馈进 log 后重扫，不得被自己洗白。"""
        fake_url = "https://fake.example.com/invented"
        out = _write_output(tmp_path, f"{_LONG}\n来源: {fake_url}")
        log = _log_with_search("https://arxiv.org/abs/2401.00001")
        first = gate_check("结论", out, log)
        assert any(fake_url in p for p in first)
        # 模拟重试：反馈作为 user 消息进了 worker log
        log.append({"role": "user", "content": format_gate_feedback(first, out)})
        second = gate_check("结论", out, log)
        assert any(fake_url in p for p in second), "编造 URL 被闸自己的反馈洗白了"

    def test_url_normalization(self, tmp_path):
        """报告里 URL 带尾标点/尾斜杠，haystack 里是裸形态——归一后仍命中。"""
        out = _write_output(
            tmp_path, f"{_LONG}\n来源：https://arxiv.org/abs/2401.00001/。"
        )
        problems = gate_check("", out, _log_with_search("https://arxiv.org/abs/2401.00001"))
        assert problems == []


class TestVerifiedMark:
    def test_verified_with_read_page_passes(self, tmp_path):
        url = "https://arxiv.org/abs/2401.00003"
        log = _log_with_search(url) + [
            {"role": "assistant", "tool_calls": [_tc("read_page", {"url": url})]},
            {"role": "tool", "content": f"正文……（{url}）"},
        ]
        out = _write_output(tmp_path, f"{_LONG}\n[已核] 该文提出 XYZ（{url}）")
        assert gate_check("结论", out, log) == []

    def test_verified_without_read_page_rejected(self, tmp_path):
        url = "https://arxiv.org/abs/2401.00004"
        out = _write_output(tmp_path, f"{_LONG}\n[已核] 该文提出 XYZ（{url}）")
        problems = gate_check("结论", out, _log_with_search(url))  # 只搜过没读过
        assert any("没用 read_page 读过" in p for p in problems)

    def test_verified_without_url_rejected(self, tmp_path):
        out = _write_output(tmp_path, f"{_LONG}\n[已核] 业内公认结论")
        problems = gate_check("", out, [])
        assert any("没给来源 URL" in p for p in problems)

    def test_unverified_mark_always_passes(self, tmp_path):
        url = "https://arxiv.org/abs/2401.00005"
        out = _write_output(tmp_path, f"{_LONG}\n[未核·仅摘要] 该文似乎提出 XYZ（{url}）")
        assert gate_check("", out, _log_with_search(url)) == []

    def test_backtick_mention_is_not_a_claim(self, tmp_path):
        """失败类钉死（2026-07-17/20 实测）：worker 自述「每处 `[已核]` 已同行给 URL」
        是对标记的提及不是核实声称——按声称打回会把 worker 拖进自指重试循环。
        """
        out = _write_output(tmp_path, _LONG)
        report = "修正完成。每处 `[已核]` 现在与来源 URL 严格同行，已删除图例行中的裸 `[已核]`。"
        assert gate_check(report, out, []) == []

    def test_backtick_does_not_shield_real_claim(self, tmp_path):
        """反向钉死：同一行里裸 [已核] 声称不因旁边有反引号片段而被豁免。"""
        out = _write_output(tmp_path, f"{_LONG}\n[已核] 结论 X（见 `代码片段`），无来源")
        problems = gate_check("", out, [])
        assert any("没给来源 URL" in p for p in problems)


class TestDelivery:
    def test_missing_output_rejected(self, tmp_path):
        problems = gate_check("结论", tmp_path / "nope.md", [])
        assert any("不存在" in p for p in problems)

    def test_short_output_rejected(self, tmp_path):
        out = _write_output(tmp_path, "太短")
        problems = gate_check("结论", out, [])
        assert any("过短" in p for p in problems)


class TestHelpers:
    def test_collect_read_page_urls_ignores_other_tools_and_bad_json(self):
        log = [
            {"role": "assistant", "tool_calls": [
                _tc("read_page", {"url": "https://a.com/x/"}),
                _tc("search_web", {"query": "https://b.com/ignored"}),
                {"function": {"name": "read_page", "arguments": "{broken"}},
            ]},
        ]
        assert collect_read_page_urls(log) == {"https://a.com/x"}

    def test_extract_urls_breaks_at_cjk_punctuation(self):
        urls = extract_urls("见 https://a.com/p，以及（https://b.com/q）。")
        assert urls == {"https://a.com/p", "https://b.com/q"}

    def test_feedback_is_teach_the_right_way(self, tmp_path):
        fb = format_gate_feedback(["URL 未在你的工具活动中出现过: https://x.com"], tmp_path / "r.md")
        assert fb.startswith("⛔")
        assert "read_page" in fb and "[未核·仅摘要]" in fb and "write_file" in fb
