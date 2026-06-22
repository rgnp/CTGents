# 目标锚点：TUI 聊天舒适度 + 精致细节改进

基于 aesthetic-design Psyche v0.4 审查，按优先级分两组实施：

## 第一组 · 聊天舒适度（高优）

### [x] 1. 消除「按回车到状态栏闪」之间的空档
- `_run_turn()` 设 `_busy=True` 后立即调 `_refresh_status()`
- 文件: `src/tui.py` +L823

### [x] 2. 用户消息同分钟时间戳降噪
- `action_submit_prompt` 中先比较 `_last_user_stamp`，同分钟不显示时间
- `_echo_conversation` 中回放用户消息同理
- 文件: `src/tui.py` ~L731 和 ~L1070

### [x] 3. 全宽分隔线 + 淡入动效
- `╌╌╌` → `╌` × 40（全宽线）+ 从 opacity 0→1 的 0.3s 淡入
- 文件: `src/tui.py` ~L917

## 第二组 · 精致细节（中优）

### [x] 4. SaveSelect 视觉层次提升
- 标题 `$accent` → `$primary`
- 列表斑马纹（交替 `$surface` / `$panel`，用 Textual `:even` / `:odd` 伪类）
- 最新存档加 `●` 高亮指示
- 文件: `src/tui.py` CSS 区 + SaveSelectScreen.compose

### [x] 5. 思考折叠区新内容脉冲动效
- `_flush_reasoning` 更新内容时做 0.05s→0.08s opacity 脉冲
- 文件: `src/tui.py` ~L976

### [x] 6. 输入框内容非空时上边框变绿
- 监听 `TextArea.Changed`，内容非空时加 `has-text` 类
- CSS: `#prompt.has-text { border-top: solid $success; }`
- 文件: `src/tui.py` CSS + ChatScreen 新增 handler

### [x] 7. 质量自检
- `ruff check src/tui.py` → ✅ 通过
- `py -m pytest tests/test_tui.py -v` → ✅ 21/21 通过
- 改动最小：每项只改了目标行，未顺手重构
