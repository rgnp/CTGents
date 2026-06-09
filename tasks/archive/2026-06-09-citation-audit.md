# 引用即取证（④可信 · 治"编造"②的可检查切片）— 已完成

落地:`src/citation_audit.py`(纯函数 `audit_citations`)+ `src/main.py`
`_inject_citation_audit` 与 completion 并列、主路径返回后调用 +
`tests/test_citation_audit.py`(10 单测)。528 全绿,ruff 净,commit 见下。

## 机制
收尾扫**最终 assistant 回复**的 `path:line` 引用 → basename → 查本会话工具活动
(tool_calls 参数 + tool 结果拼 haystack)出现过没。grounded=basename∈haystack
(read/preread/write/edit 都让路径进 haystack);ungrounded→挂 volatile 提示。
只扫最终回复 → 每轮刷新(不像 completion 持续到补跑)。

## 交底
- 牙窄:只罩带行号的代码文件引用;裸文件名不抓(precision 边界);
  罩不住"编了个 pandas 参数"(判断,留三态纪律)。
- 耦合松:靠 basename∈haystack 不依赖精确 marker → 无需 C16 契约测试,
  保守(子串命中即 grounded,宁漏判不误报,nudge 最怕狼来了)。
