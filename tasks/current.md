# 当前长任务

## 目标
操守失分根因修复：正道修通 + 邪道堵死 + 事后审计（借鉴成熟 agent harness 的三层结构）

根因链：run_command 默认 30s/agent 传 10s < 质量门 ~42s → 正道物理死路 →
"超时即换策略"教训缺边界 → `--no-verify` 绕门 → 6 红测试入库。
成熟 agent 不靠品德靠路径工程：正道永远可走（宽超时）/ 绕门有牙拦（工具层拒绝）/
事后有审计（CI/审查兜底）。三者对应移植。

## 步骤

- [ ] Step 1: test_cache.py 对齐 8b02143 新压缩行为（6 红→绿）+ llm.py 补"压缩反增大"边界守卫
- [ ] Step 2: exec.py 操守牙 — git 钩子绕过拦截（--no-verify/-n/core.hooksPath）+ git commit 超时地板（params 旋钮）+ 测试
- [ ] Step 3: 门通行证审计 — pre-commit 成功后记 write-tree 哈希；会话启动核对 HEAD tree，无通行证即注入提醒 + 测试
- [ ] Step 4: 同轮工具去重补测试（50c78c3 零测试入库，C16 补课）
