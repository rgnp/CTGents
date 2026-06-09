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
- [o] Step 7: 全绿(495 passed)+ ruff 通过,按特定文件 commit。

### Phase 2 — 转存钩子
- [ ] Step 8: `session_pins.py` 加 `promote_durable()`:把标记耐久/会话结束仍在的 pin → 调 memory `_remember`。
- [ ] Step 9: 接线会话结束路径(session 保存/退出处)调用 promote。
- [ ] Step 10: 测试(转存后 memory 出现对应条目)+ commit。

### Phase 3 — 可选,默认不做
- [ ] Step 11(可选): 扩 `detect_signal` 加"决定点提示该 pin"。**先搁置,噪音风险大,确有需要再开。**

## 验证
每步:import 检查 / `ruff check src/` / 相关测试。Phase 内全绿才 commit。
