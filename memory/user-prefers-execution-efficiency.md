---
name: user-prefers-execution-efficiency
description: 用户要求执行效率最大化，严格遵守：
metadata:
  type: user
  updated: 2026-05-31T15:23:59Z
---

用户要求执行效率最大化，严格遵守：

1. 不读已存在的上下文内容（刚读过/说过的不重复读文件）
2. 能并行的工具调用同时调，不串行等
3. 小改动直接 edit_file_lines，不先 read 再分析
4. 输出只给结论，成功/失败几行字
5. 不绕弯子、不铺垫、不解释显而易见的东西
