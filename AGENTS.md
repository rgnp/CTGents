<agent>
CTGents——自进化的编程 + 科研助手。单 DeepSeek Pro，append-only 三段式上下文（前缀冻结 + 日志纯追加）。
目标：① 帮用户做他手上的研究/工作（选题/文献/分析/写作/改代码，领域由真实任务带出，不写死）；② 工具越用越好用（记忆 + 工具复利）。
本文件是能力地图 + 软偏置，不是给人看的文档，无需在意格式。
</agent>

<commands>
py -m pytest tests/        # 全量（开发期一般只跑相关子集）
py -m pytest tests/x.py -v # 单文件
py -m pytest tests/ -k kw  # 筛选
py src/main.py             # 启动
ruff check src/            # lint（提交前 pre-commit 已强制）
</commands>

<map>
src/ 源码 | src/tools/ 工具 | tests/ | docs/ | memory/(不提交) | knowledge/(不提交) | sessions/ 会话存档 | tasks/(current.md + pending/ + archive/) | psyche/ 领域框架(按需加载)
工具数/架构会变——用 self 工具看实时状态，别信这里的描述假设。
</map>

<tools desc="你的主要能力，怎么协同">
找: grep_code(内容/正则) | find_files(文件名/glob) | rag_query(语义搜代码) | list_files(浏览)。找主体代码优先 grep_code，别用 list_files 表层猜。
改: 先 read_file(可带 start_line/end_line 取段) → replace_in_file(字符串精确匹配，首选，免行号漂) 或 write_file(整文件重写)。edit_file_lines 行号易漂少用。还有 move_file/make_dir/delete_file/count_lines。
跑: run_command(短，有超时) | run_python | run_async(长命令如全量测试，完成自动通知你，派发后接着干别 poll)。
任务(3+文件): 先写 tasks/current.md(含目标锚点)展示步骤，按步标 [x]/[o]/[ ]，做完 task_done，要拍板调 need_user(别只在回复里说"先停下"——会被自动续跑覆盖)。
记忆/研究: remember/recall 跨会话；learn 查概念；search_web → read_page 联网深读。
文献工具(scan_papers/scan_conf/read_papers/read_paper/rag_index_research)默认不加载、省常驻 token——要做文献研究时提示用户输入 /tools load research 挂上。
</tools>

<psyche>
你的工作人格（认知姿态 / 答案分层 / 出活准则 / 语气）不在本文件里——它是常驻注入的「general」psyche（每会话开局自动加载，见 psyche/general/核心/）。本文件只剩能力地图 + 事实，行为人格是第一人称的 psyche 而非这里的规则散文。
领域认知框架在 psyche/，命中触发词自动加载（领域经验叠加在 general 之上）。冲突时本文件的可信/安全规则永远高于 psyche 判断准则。
父 Psyche 不自动带子；加载子前先确认父已加载；子准则优先于父通用准则。
</psyche>

<guards desc="代码替你守的，不用记，知道在哪即可">
tool_guard: 改文件限工作目录、读后才能写、文件放对目录、禁 git add -A、禁 force-push main/master。
file.py: 不可变安全核(guard/tool_guard/gate_audit/pre-commit)拒写；核心业务文件可改但走 import 冒烟安全带，改坏自动回滚。
pre-commit: 密钥扫描 + ruff(提交即强制)。pytest 不进提交门(太重/开发期快提交)——提交 ≠ 测试通过，质量靠改后即测/评审。
轮末审计: completion(谎报完成)/gate(绕提交门)/quality(没做质量自检) 给事实提示，判断仍归你。
</guards>
