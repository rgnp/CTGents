# 任务闭环原型:目标-标准-评分-修订(outcome loop)

设计判断(与用户对齐):上限在 harness 形状不在模型——CTGents 是"带工具的聊天 REPL",
强 harness 是"任务环":完成标准开场写定,干净上下文的评分步对照标准打分,不达标带差距
清单再来一轮。把"反向验证"从 disposition 变成控制流;评分者隔离(看不到工作者的对话
log,只看目标/标准/交付物)= citation_audit"不收 assistant 自己的话"同一洞察的任务级放大。

分工边界:可机械判定的标准(测试退出码/lint)归机械层(completion_audit/pre-commit),
评分环只管语义标准——判断题给 LLM 评分者,不机械化。

- [ ] F1: src/outcome.py(OutcomeSpec/grade/run_outcome)+ params.OUTCOME 旋钮 + 单测
      (评分者隔离不变量:worker log 决不入评分 payload / 达标即停 / 到轮数上限停 / 差距回灌)
- [ ] F2: /goal 指令接线(commands.py 加字段,main 驱动,与 r.retry 同模式)+ 交互网测试
- [ ] F3: 真实任务 headless 跑通一轮,验证效果;归档本计划
