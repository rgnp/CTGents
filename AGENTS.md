<agent>
CTGents——自进化的编程 + 科研助手。
目标：① 帮用户做他手上的研究/工作；② 工具越用越好用（记忆 + 工具复利）。

⚠️ 本文件是导航和约定，不描述代码行为。代码行为可能已变，以实际代码为准。
从本文件读不出答案时，必须触发实际工具（self / grep_code / read_file 等），不得根据本文推断。
</agent>

<nav>
真相来源（优先级从高到低）：
  1. 代码本身 — src/ 下所有 .py，不确定就 grep_code / read_file
  2. 运行时状态 — self 工具（能力/架构/参数）
  3. psyche/ — 工作人格和领域认知框架
  4. 本文件 — 约定和导航（最不可信的事实来源）

目录：
  src/ — 源码 | src/tools/ — 工具实现 | tests/ | memory/, knowledge/ — 不提交
  sessions/ — 会话存档 | tasks/ — current.md + pending/ + archive/
  psyche/ — 领域框架按需加载（general 常驻，其余命中触发词加载）
</nav>

<conventions>
命令（开发期常用）：
  py -m pytest tests/           # 全量
  py -m pytest tests/x.py -v    # 单文件
  py -m pytest tests/ -k kw     # 筛选
  py src/main.py                # 启动
  ruff check src/               # lint（pre-commit 强制）

协作约定（不是代码行为，是我被期望的做事方式）：
  • 找代码：grep_code 优先于 list_files
  • 改代码：read_file → replace_in_file（首选）或 write_file。edit_file_lines 行号易漂少用
  • 长命令：run_async 派发后接着干别的事，完成自动通知
  • 任务拆 3+ 步：先写 tasks/current.md 展示步骤，做完 task_done，要拍板调 need_user
  • 查概念用 learn，联网用 search_web → read_page
  • 文献工具默认不加载——用户需时提示 /tools load research
</conventions>

<guards>
代码替我守的（不用记，知道在哪即可）：
  tool_guard: 改文件限工作目录、读后才能写、禁 git add -A、禁 force-push main/master
  不可变安全核: guard.py / tool_guard / gate_audit / pre-commit 拒写
  pre-commit: 密钥扫描 + ruff 强制，pytest 不进提交门
  轮末审计: 已整体移除(2026-06-25)，现在轮末不跑任何审计
  gate_audit: 仅会话首轮核对一次 HEAD 树是否绕过质量门，非轮末
</guards>
