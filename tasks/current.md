# 目标锚点：从"旗"到"绳"

# 目标锚点
让 current.md 从被动计划变成每步对照方向的回拉绳。

- [x] 加 `_extract_anchor()` + `_ANCHOR_HEADING` 常量
- [x] `create_task` 拒绝无锚点写入
- [x] `make_task_context_message` 注入锚点对照提示
- [x] 测试：32/32 通过
- [x] 全量测试：670/670 通过
- [x] 提交
- [ ] 归档 current.md → tasks/archive/

## 完成总结
- 两个改动：create_task 关门（拒绝无锚点）+ make_task_context_message 回拉（每轮注入对照提示）
- 教训：好的约束不是写在文档里的提醒，是代码级拒绝——create_task 没锚点直接不写文件，比任何"记得加锚点"的叮嘱都有力。
