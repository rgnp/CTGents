"""缓存命中率深度分析：模拟真实对话场景，计算每轮缓存命中率。

场景：
  A. 日常编码对话（20 轮，每轮 ~2K token）
  B. 长会话（50 轮，LLM 频繁调用工具）
  C. 大文件读取场景（read_file 返回大内容 → 工具结果压缩）
  D. 压缩对比：旧删除式 vs 新 append-only
"""
import hashlib, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("TAVILY_API_KEY", "tvly-test")

from src.cache_context import CacheContext

# ── 工具函数 ──

def api_payload(msgs: list[dict]) -> str:
    return json.dumps(msgs, ensure_ascii=False)

def cache_bytes(prev: str, curr: str) -> int:
    """计算 DeepSeek 实际可缓存的字节前缀长度（去掉 JSON 尾部 ]）"""
    p = prev.rstrip().rstrip("]")
    c = curr.rstrip().rstrip("]")
    i = 0
    for a, b in zip(p, c):
        if a != b:
            break
        i += 1
    return i

def est_tokens(json_bytes: str) -> int:
    return len(json_bytes.encode()) // 4

# ── 模拟消息 ──

def make_prefix():
    return [
        {"role": "system", "content": "当前环境：\n- 操作系统: Windows 10.0.19045\n\n以上为运行环境信息，不需要在回复中复述或罗列。\n\n你拥有长期记忆，需要时用 recall 搜索相关记忆。", "_volatile": True},
        {"role": "system", "content": "当前项目: my-agent | 语言: Python 3.12 | 包管理: pip | 源码目录: src/\n主要模块: main.py(入口), llm.py(LLM后端), commands.py(指令系统), cache_context.py(上下文管理), tools/*(工具集)", "_volatile": True},
        {"role": "system", "content": "📚 RAG 代码索引已就绪，可用 rag_query 进行语义搜索。", "_volatile": True},
    ]

USER_MSGS = [
    "帮我读一下 src/main.py 文件，看看入口逻辑",
    "这个 logger 为什么没定义？帮我看看",
    "帮我修改 run_conversation 函数，加一个重试机制",
    "不对，重试应该放在 _invoke_llm 里",
    "git status 看一下改了什么",
    "帮我提交这些改动",
    "等等，先跑一下测试",
    "测试失败了，看看错误日志",
    "问题是 session.save 那里传了 ctx.all 而不是 ctx.log",
    "修好了，现在帮我推送到远程",
    "推送失败了，说什么 force push 不行",
    "那用 revert 吧，别 reset 了",
    "帮我看看最近 5 个 commit 分别改了什么",
    "等等，这个 commit message 写得不好，改一下",
    "现在在 main 分支上对吧？切一个新分支",
    "帮我把这个函数重构一下，拆成两个",
    "还有个循环依赖的问题，看看能不能解",
    "import 顺序也有问题，按 isort 的规矩来",
    "现在再跑一次测试，看看还有没有问题",
    "全绿了，合并到 main",
]

ASST_MSGS = [
    "好的，让我读取 src/main.py 看看入口逻辑。",
    "让我搜索 logger 的定义和使用位置。",
    "我来修改 run_conversation，添加指数退避重试。",
    "你说得对，重试逻辑应该放在 _invoke_llm 内部，这样更内聚。让我改。",
    "当前 git 状态如下：修改了 src/llm.py 和 src/main.py。",
    "好的，我来提交：fix: add retry logic to _invoke_llm + fix logger NameError",
    "先跑测试。" + " 测试输出内容 " * 20,
    "测试报错了：AssertionError: expected ctx.log but got ctx.all。问题在 session.py。",
    "修好了，把 ctx.all 改成 ctx.log。",
    "推送到 origin/main..." + " 推送结果 " * 10,
    "force push 被拒绝了，main 分支受保护。用 revert 吧。",
    "执行 git revert HEAD~1..." + " revert 输出 " * 10,
    "最近 5 个 commit：1. fix logger 2. add retry 3. refactor compaction 4. ...",
    "amend 了 commit message，现在是 'fix: session.save uses ctx.log instead of ctx.all'。",
    "当前在 main 分支。切到新分支 fix/import-cycle。",
    "好的，拆成 _handle_tool_calls 和 _execute_approved 两个函数。",
    "循环依赖是 tools/__init__.py 引用了 llm.py 的 _TOOL_LABEL_MAP，我移到一个单独的 labels.py。",
    "import 顺序按 isort 整理好了。",
    "测试全绿：48 passed, 0 failed。" + " 测试详情 " * 5,
    "合并到 main，没有冲突。",
]

TOOL_RESULTS = [
    '文件内容：\n     1 | """LLM 后端抽象"""\n     2 | import json\n     3 | import logging\n' + '     N | ... 代码内容 ...\n' * 30,
    '搜索到 3 处引用：\nmain.py:68: logger = logging.getLogger(__name__)\nllm.py:66: logger = logging.getLogger(__name__)\ncommands.py:21: logger = logging.getLogger(__name__)',
    '写入成功：src/llm.py（共 245 行）',
    '修改成功：src/llm.py 第 560-590 行',
    'On branch main\nChanges not staged for commit:\n  modified: src/llm.py\n  modified: src/main.py',
    '[main abc1234] fix: add retry logic to _invoke_llm\n 2 files changed, 15 insertions(+), 3 deletions(-)',
    'collected 48 items\nsrc/tests/test_llm.py ...FF..\n' + '  test output ' * 10,
    'AssertionError: 详细错误...',
    '写入成功：src/main.py（共 478 行）',
    'To github.com:user/my-agent.git\n ! [remote rejected] main -> main (pre-receive hook declined)',
    'Revert "add retry logic" - This reverts commit abc1234.',
    'abc1234 fix: add retry logic\n def4567 refactor: append-only compaction\n ...',
    'commit message 已更新',
    'Switched to a new branch fix/import-cycle',
    'src/llm.py 已修改，_handle_tool_calls 和 _execute_approved 拆分完毕。',
    '已创建 src/tools/labels.py，循环依赖解除。',
    'import 顺序已整理：标准库 → 第三方 → 本地。',
    '48 passed, 0 failed in 2.34s',
    'Fast-forward merge: main ← fix/import-cycle',
]

# 每 3 轮出现一次 tool call（读文件、搜索、git 等）
TOOL_NAMES = ["read_file", "grep_code", "write_file", "edit_file_lines",
              "git_status", "git_commit", "run_command", "run_command",
              "write_file", "git_push", "git_log", "git_log",
              "git_branch", "write_file", "grep_code", "list_files",
              "run_command", "run_command", "write_file",
              "count_lines", "write_file", "run_command", "write_file"]


# ═══════════════════════════════════════════════════════════════
# 场景 A：日常编码对话（20 轮）
# ═══════════════════════════════════════════════════════════════

print("═" * 70)
print("  场景 A：日常编码对话（20 轮，含工具调用）")
print("═" * 70)

ctx = CacheContext()
ctx.rebuild_prefix(make_prefix())
# 模拟启动时注入的动态上下文
ctx.log.append({"role": "system", "content": "安全模式: MANUAL", "_volatile": True})

prev_payload = ""
prev_tokens = 0
turn_data = []

for turn in range(20):
    ctx.log.append({"role": "user", "content": USER_MSGS[turn]})
    ctx.log.append({"role": "assistant", "content": ASST_MSGS[turn]})

    # 每 3 轮模拟工具调用
    if turn % 3 == 0 and turn < len(TOOL_NAMES):
        tool_name = TOOL_NAMES[turn]
        ctx.log.append({
            "role": "assistant",
            "content": "让我来..." if turn % 6 != 0 else None,
            "tool_calls": [{"id": f"call_{turn}", "type": "function",
                           "function": {"name": tool_name, "arguments": '{"path": "test.py"}'}}]
        })
        ctx.log.append({
            "role": "tool", "tool_call_id": f"call_{turn}",
            "content": TOOL_RESULTS[turn % len(TOOL_RESULTS)],
            "_tool_name": tool_name,
            "_tool_result_compressed": True,
        })
        ctx.log.append({"role": "assistant", "content": ASST_MSGS[turn] + " 结果已处理。"})

    curr_msgs = ctx.send(validate=False)
    curr_payload = api_payload(curr_msgs)
    curr_tokens = est_tokens(curr_payload)

    if prev_payload:
        cached = cache_bytes(prev_payload, curr_payload)
        hit_rate = cached / len(curr_payload) * 100
        turn_data.append((turn + 1, prev_tokens, cached // 4, curr_tokens, hit_rate))

    prev_payload = curr_payload
    prev_tokens = curr_tokens

# 打印
print(f"  {'轮次':<6} {'累积tok':>10} {'缓存命中':>8} {'本轮总计':>10} {'命中率':>8}")
for turn, prev_tok, cached_tok, curr_tok, rate in turn_data:
    bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
    print(f"  {turn:<6} {prev_tok:>8,}tok {cached_tok:>6,}tok {curr_tok:>8,}tok {rate:>7.1f}% {bar}")

if turn_data:
    avg = sum(d[4] for d in turn_data) / len(turn_data)
    print(f"\n  平均缓存命中率: {avg:.1f}%")
    print(f"  第 20 轮时上下文: {turn_data[-1][3]:,} token（{turn_data[-1][3]/9600:.1f}% of 960K）")
    print(f"  等效输入成本: 全价 100% → 缓存折扣后 ~{100 - avg * 0.9:.0f}%")


# ═══════════════════════════════════════════════════════════════
# 场景 B：长会话（50 轮，每轮简短）
# ═══════════════════════════════════════════════════════════════

print("\n" + "═" * 70)
print("  场景 B：长会话（50 轮简短对话）")
print("═" * 70)

ctx2 = CacheContext()
ctx2.rebuild_prefix(make_prefix())
ctx2.log.append({"role": "system", "content": "安全模式: MANUAL", "_volatile": True})

prev_payload = ""
prev_tokens = 0
turn_data2 = []

for turn in range(50):
    q = f"问题 {turn}: 这段代码 {turn} 有什么问题？"
    a = f"回答 {turn}: 问题在于第 {turn} 行的变量作用域。"
    ctx2.log.append({"role": "user", "content": q})
    ctx2.log.append({"role": "assistant", "content": a})

    curr_payload = api_payload(ctx2.send(validate=False))
    curr_tokens = est_tokens(curr_payload)

    if prev_payload:
        cached = cache_bytes(prev_payload, curr_payload)
        hit_rate = cached / len(curr_payload) * 100
        if turn % 10 == 0 or turn == 49:
            turn_data2.append((turn + 1, prev_tokens, cached // 4, curr_tokens, hit_rate))

    prev_payload = curr_payload
    prev_tokens = curr_tokens

for turn, prev_tok, cached_tok, curr_tok, rate in turn_data2:
    bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
    print(f"  {turn:<6} {prev_tok:>8,}tok {cached_tok:>6,}tok {curr_tok:>8,}tok {rate:>7.1f}% {bar}")

last = turn_data2[-1] if turn_data2 else (0, 0, 0, 0, 0)
print(f"\n  第 50 轮命中率: {last[4]:.1f}%")
print(f"  第 50 轮上下文: {last[3]:,} token（{last[3]/9600:.1f}% of 960K）")


# ═══════════════════════════════════════════════════════════════
# 场景 C：大文件读取 — 工具结果压缩效果
# ═══════════════════════════════════════════════════════════════

print("\n" + "═" * 70)
print("  场景 C：大文件读取 — 压缩 vs 不压缩")
print("═" * 70)

LARGE_FILE = "def foo():\n" + "    pass\n" * 500  # ~5000 chars

ctx3 = CacheContext()
ctx3.rebuild_prefix(make_prefix())
ctx3.log.append({"role": "user", "content": "读取大文件"})
ctx3.log.append({"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "big.py"}'}}
]})

# 不压缩
ctx3.log.append({"role": "tool", "tool_call_id": "c1", "content": LARGE_FILE})
ctx3.log.append({"role": "assistant", "content": "文件已读取。"})

no_compress = api_payload(ctx3.send(validate=False))

# 清除重试
ctx3.log = ctx3.log[:2]  # 重置到 tool call 之前

# 压缩（模拟 _compress_tool_result）
from src.llm import _compress_tool_result
ctx3.log.append({"role": "tool", "tool_call_id": "c1",
                  "content": _compress_tool_result("read_file", LARGE_FILE),
                  "_tool_name": "read_file", "_tool_result_compressed": True})
ctx3.log.append({"role": "assistant", "content": "文件已读取。"})

compressed = api_payload(ctx3.send(validate=False))

print(f"  不压缩: {est_tokens(no_compress):,} token")
print(f"  压缩后: {est_tokens(compressed):,} token")
print(f"  节省:   {est_tokens(no_compress) - est_tokens(compressed):,} token ({(1 - est_tokens(compressed)/est_tokens(no_compress))*100:.0f}%)")


# ═══════════════════════════════════════════════════════════════
# 场景 D：旧删除式压缩 vs 新 append-only
# ═══════════════════════════════════════════════════════════════

print("\n" + "═" * 70)
print("  场景 D：旧删除式 vs 新 append-only 压缩对缓存的影响")
print("═" * 70)

ctx4 = CacheContext()
ctx4.rebuild_prefix(make_prefix())

for i in range(15):
    ctx4.log.append({"role": "user", "content": f"问题 {i}"})
    ctx4.log.append({"role": "assistant", "content": f"回答 {i} 的详细内容 " * 3})

before = api_payload(ctx4.send(validate=False))

# ── 旧方式：删除前 10 轮，插入摘要 ──
old_log = list(ctx4.log)
retain_start = 20  # 保留后 5 轮
old_log = [{"role": "system", "content": "⏪ 摘要: 讨论了1-10"}] + old_log[retain_start:]
ctx_old = CacheContext(prefix_msgs=make_prefix(), log_msgs=old_log)
after_old = api_payload(ctx_old.send(validate=False))

# ── 新方式：追加摘要 ──
ctx4.log.append({"role": "system", "content": "⏪ 摘要: 讨论了1-10"})
after_new = api_payload(ctx4.send(validate=False))

old_cache = cache_bytes(before, after_old)
new_cache = cache_bytes(before, after_new)

print(f"  压缩前 payload:          {est_tokens(before):,} token")
print(f"  旧删除式 后 payload:     {est_tokens(after_old):,} token")
print(f"  新追加式 后 payload:     {est_tokens(after_new):,} token")
print(f"")
print(f"  旧删除式 可缓存字节:     {old_cache // 4:,} token / {est_tokens(before):,} → 命中率 {old_cache/len(after_old)*100:.1f}%")
print(f"  新追加式 可缓存字节:     {new_cache // 4:,} token / {est_tokens(before):,} → 命中率 {new_cache/len(after_new)*100:.1f}%")
print(f"")
print(f"  改进: {new_cache - old_cache:,} 额外缓存字节 (+{(new_cache - old_cache) / max(old_cache, 1) * 100:.0f}%)")


# ═══════════════════════════════════════════════════════════════
# 综合评分
# ═══════════════════════════════════════════════════════════════

print("\n" + "═" * 70)
print("  综合评估")
print("═" * 70)

avg_a = sum(d[4] for d in turn_data) / len(turn_data) if turn_data else 0
avg_b = sum(d[4] for d in turn_data2) / len(turn_data2) if turn_data2 else 0

print(f"  场景 A（20轮日常编码）:  平均缓存命中率 {avg_a:.1f}%")
print(f"  场景 B（50轮简短对话）:  最终缓存命中率 {last[4]:.1f}%")
print(f"  场景 C（大文件压缩）:     节省 ~{(1 - est_tokens(compressed)/est_tokens(no_compress))*100:.0f}% token")
print(f"  场景 D（缓存保持）:       新追加式比旧删除式多保留 {new_cache - old_cache:,} 可缓存字节")

if avg_a >= 85:
    print(f"\n  ✅ 日常使用场景缓存命中率已达标 (>85%)")
else:
    print(f"\n  ⚠️  日常使用场景缓存命中率 {avg_a:.1f}%，目标 85%+")

# 估算成本节省
# DeepSeek 缓存命中 token 价格是未命中的 ~10%
effective_cost_pct = 100 - avg_a * 0.9
print(f"  等效输入成本: 全价的 {effective_cost_pct:.0f}%（缓存命中 token 按 10% 计价）")
print(f"  对比无缓存: 节省 ~{avg_a * 0.9:.0f}% 输入 token 费用")
