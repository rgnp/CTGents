# 会话钉板(session pinboard)+ 转存钩子

## 目标
治"长会话内决定/约束漂移":一条常驻**尾部**的易失清单,装本次会话 ≤N 条一句话决定/约束,每轮原地刷新。会话结束时把耐久 pin 转存进**现有** memory 系统(不重造 memory)。

## 设计约定(已与用户敲定)
- 钉板 = 内存易失,本场可见、关窗蒸发。`pin` / `unpin` 是 agent 自调工具(形态同 remember)。
- 封顶**整条淘汰,绝不切某条中间**:每条 ≤ 字数上限(写入侧限短),总数 ≤ 条数上限(踢最旧/已失效整条)。
- 数字进 `params.py`(C8 可调旋钮,CTG_* 覆盖),默认 8 条 / 80 字,待校准。
- 缓存安全:只挂 log 尾(send() 自动搬到 API 末尾),**prefix 一律不碰**。
- 转存:会话结束/淘汰时,够耐久的 pin → `remember`(进 memory,下次 recall 可捞)。

## 范围边界(不做)
- 不重造 memory 系统;recall 子串匹配的升级是后续可选项,不进本次。
- 决定点"该 pin 了"轻提示(扩 detect_signal):列为 Phase 3 可选,默认先不做(避免重蹈记忆信号的噪音顾虑)。

## 步骤

### Phase 1 — 钉板主体(MVP)
- [x] Step 1: `params.py` 加钉板旋钮(PINBOARD_MAX_ITEMS=8 / PINBOARD_MAX_CHARS=80,frozen dataclass + CTG_* 覆盖)
- [x] Step 2: 新建 `src/session_pins.py`(核心,顶层 src 模块,禁 `from ..`):内存 store + add/remove/列出/render_tail/封顶淘汰(整条)。写入侧限短。
- [x] Step 3: 新建 `src/tools/pin.py`:`pin`/`unpin` 工具(TOOLS 列表 + `_meta`)+ `execute(name,args)->str|None`。execute 对外来名返回 None(已验证)。`from ..session_pins` 导入(level 2 合法)。
- [x] Step 4: 接线 `main.py::_append_volatile_context`:render_tail 挂 log 尾;/new 时 clear_pins。
- [x] Step 5: 接线 `llm.py`(1030 刷新段):轮内 pin/unpin → 原地替换/追加/移除尾部钉板消息。
- [x] Step 6(C16 测试):
  - 单元 `tests/test_session_pins.py`(10 条):add/限短/封顶整条淘汰/去重/render/clear。
  - L0:`test_invariants.py` 越界导入用例自动覆盖 session_pins/pin。
  - L2 `tests/test_llm.py`(+2):pin → `send()` 尾部出现、不进 prefix;全 unpin → 钉板消失。
  - 计数护栏更新:test_tool_meta(48→50/13→15/49→51)、test_plan_mode(48→50/38→40)。C16 机械捕获生效。
- [x] Step 7: 全绿(495 passed)+ ruff 通过,commit cfdea3d。

### Phase 2 — 转存钩子
- [x] Step 8: `session_pins.py` 加 `promote_durable()`:durable pin → `_remember`;name=可读 slug+内容哈希(同文本覆盖去重)。
- [x] Step 9: 接线 `main.py` 会话结束 finally:promote_durable + 提示转存条数。
- [x] Step 10: 测试(只转存 durable、调用签名、name 确定性)+ commit。

### Phase 3 — 已搁置(非待办)
Step 11(扩 `detect_signal` 加"决定点提示该 pin"):**搁置,噪音/缓存风险大**。
改用更省的替代:宪章(prefix)加 pin 用法(commit 9d8ad0a)。真有"agent 老忘记 pin"
的证据再开 Phase 3。

## 验证
每步:import 检查 / `ruff check src/` / 相关测试。Phase 内全绿才 commit。

## 完成总结
- 计划 10 步(+宪章 1)→ 实际 11 步,0 次回退(计数护栏命中 5 处,按预期更新,非回退)。
- 提交:cfdea3d(P1 主体)、ed85c91(P2 转存)、9d8ad0a(宪章用法)。全程 497 passed + 门禁过。
- 端到端实证:宪章会让 agent 用 pin / 钉板落 API 末尾且 prefix 哈希不变(缓存安全) / 转存命名确定去重。
- 教训:加 2 个工具触发 5 处硬编码计数断言(tool_meta×3 + plan_mode×2)——这正是 C16 机械护栏
  设计意图,改数字即护栏在"该响处响"。pin 归类参照 `think`(非 plan_blocked)而非 `remember`,
  因它只动内存易失上下文、不碰磁盘。
