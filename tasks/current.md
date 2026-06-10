# 审查修复:断线 / 熔断 / 压缩配对 / token 估算

外部审查(Claude)发现的问题,按 feature 分别提交:

- [ ] F1: `_task_ctx` 标记接通(tasks.py 补标记)+「strip 标记必有生产者」不变量测试
- [ ] F2: 被动进化反思接线(session.py 删 return 后死代码,main 收尾时 reflect)+「return/raise 后无死代码」不变量测试
- [ ] F3: 工具循环请求数熔断(params 旋钮 + llm.py 实现 + 修正 docstring 虚假声明)
- [ ] F4: 滑窗压缩:驱逐边界对齐 user 消息(保 tool 配对)+ keep_ratio 语义对齐 + 删话题切换关键词机制
- [ ] F5: token 估算分字符类:中文 0.6 / 其他 0.3(params 两旋钮)
- [ ] F6: /load 后重注 volatile 上下文 + 清理 detect_signal 死链(连 _inject_memory_signal、测试文件)
