# 织交互测试网 + 修 citation_audit preread 假阳性 — 已完成

治"积木一碰就散":bug 不在单元、在缝里(preread×长度、volatile 互挤、前缀被碰)。

## Part 1（commit fedd293）
映射真实数据流时发现 citation_audit grounding 漏扫 user 消息 → preread/用户粘贴
的文件被误报"没读过"(单测把 preread 错建模成 tool 消息,绿着测假场景)。修:
`_tool_activity_text`→`_context_text`,haystack 纳入 role==user 内容(不含 assistant)。

## Part 2（本提交）
`tests/test_integration_turn.py` 5 条 L2 网,只 mock `llm._invoke_llm`、真实 AGENTS
前缀、镜像 main 每轮管线(mem 信号→preread→run_conversation→两审计):
- 皇冠 `test_prefix_survives_multifeature_turn`:多 feature 同轮后 send() 不抛、
  prefix hash/len 不变(谁碰坏缓存前缀当场红)。
- 回归 `test_preread_citation_not_false_flagged`:钉死 Part1 的 bug 不复发。
- `test_volatile_signals_dont_accumulate`:mem 信号≤1、completion 审计跨轮恒 1。
- `test_features_coexist_at_tail`:mem 信号 + pin 尾部并存、前缀净。
- `test_send_wellformed_no_orphan_tool`:tool 消息必有前序同 id tool_call(防 400)。

## 交底（已记 current/archive）
- 网镜像 main 管线顺序,有 drift 风险 → 注释标 main.py:413-437;权威化正解是抽
  `main.process_turn()` 再让网调它,本期未抽(避免扩面)。这是下一步硬化的自然入口。
- 534 全绿,ruff 净。
