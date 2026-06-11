# 主动进化接线：L1+L2 入启动流

## 计划
- [x] Step 1: `make_task_context_message` 接入 `detect_all_gaps` → agent 启动即见方向发现报告（每会话缓存，~5s 只跑一次）
- [ ] Step 2: "/fix #N" 指令 — 搜索方案→诊断→执行闭环
- [ ] Step 3: 全量测试 + commit

## 完成总结（Step 1）
- `tasks.py`: make_task_context_message 最前面加入方向发现（每会话只跑一次），`_gaps_reported` 标志 + `reset_gaps_cache()` 供会话切换使用
- 未修改 main.py（受保护），走 tasks.py 接线同样位置正确——`_append_volatile_context` 调用链单点
