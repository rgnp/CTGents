# 整套删除 Plan Mode（只读门）— 已完成

只读门=保姆模型(人盯着、人 gate)。agent 自管版无牙=无意义;用户版要人盯着何时
敲 /plan=用户不想盯 → 两版都删。替代品=审计(completion/citation)零监督=信任模型。
顺带清掉 plan_blocked 这层全局状态耦合。全仓扫描确认无进化 runner 等隐藏依赖。

删除面(全删净,src 复扫 0 残留):
- tools/__init__.py: _PLAN_BLOCKED import、_plan_mode/set_plan_mode/is_plan_mode、
  get_tools 的 plan 过滤(恒返回全部)。
- tools/_tool_meta.py: PLAN_BLOCKED 全局 + _derive 收集/返回项(5→4 元组)+ _refresh_globals 解包。
- 10 工具 _meta 去 plan_blocked: file×3 git×3 memory×2 research×2。
- commands.py: /plan 命令 + 诊断"模式"行 + 清理错位 banner(/reload 曾被夹在 Plan Mode 标题下)。
- main.py: is_plan_mode import + 收尾"仍在 Plan Mode"提醒。
- 测试: 删 test_plan_mode.py 整文件;test_integration_turn 去 sticky 测试+import;
  test_tool_meta 去 PLAN_BLOCKED(import/tuple/dead/count + _derive 4元组解包)。

523 绿(535−11−1),ruff 净。工具数仍 50(plan_blocked 只是元数据,不影响工具数)。

## 过程小坑(已记)
第一批 10 个并行 _meta 编辑里 git/research 因"未读"失败,重做时漏补 research:75
"保存论文卡片"——靠提交前 grep 复扫逮到。教训:大批量删除后必做一次全仓复扫,
不能只信"测试绿"(死键 plan_blocked 不被收集,测试照样绿)。
