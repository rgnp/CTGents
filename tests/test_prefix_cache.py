"""前缀缓存深度测试：字节级稳定性分析。

验证 DeepSeek 前缀缓存（从 payload 开头做字节匹配）的实际表现：
  - 多轮对话中前缀是否稳定
  - 压缩是否破坏缓存前缀
  - 记忆/安全模式变更是否扰动
  - 会话保存/加载是否引入冗余
"""
import hashlib
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 模拟环境 ──
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("TAVILY_API_KEY", "tvly-test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

from src.cache_context import CacheContext, _compute_msg_hash


def api_bytes(messages: list[dict]) -> str:
    """模拟 OpenAI SDK 序列化 messages 为 JSON 字节串。
    这是 API 实际发送的内容，DeepSeek 在此层面做前缀匹配。
    """
    payload = json.dumps(messages, ensure_ascii=False)
    return payload


def cache_prefix_messages(prev: list[dict], curr: list[dict]) -> int:
    """计算两次 payload 在消息层面的共同前缀长度（消息数）。"""
    n = 0
    for a, b in zip(prev, curr):
        if a != b:
            break
        n += 1
    return n


def cacheable_prefix_bytes(prev_payload: str, curr_payload: str) -> int:
    """计算实际可缓存的字节前缀长度。

    JSON 数组增长时，`]` 变成 `,`，这不代表缓存流失。
    实际可缓存字节 = 去掉尾部空白和 `]` 后的共同前缀。
    """
    # 去掉末尾空白和 ]
    prev_trimmed = prev_payload.rstrip().rstrip("]")
    curr_trimmed = curr_payload.rstrip().rstrip("]")

    i = 0
    for a, b in zip(prev_trimmed, curr_trimmed):
        if a != b:
            break
        i += 1
    return i


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════
# 模拟最小依赖（不触发网络/文件 IO）
# ═══════════════════════════════════════════════════════════════

def mock_env_msg():
    return {
        "role": "system",
        "content": (
            "当前环境：\n"
            "- 操作系统: Windows 10.0.19045\n"
            "\n以上为运行环境信息，不需要在回复中复述或罗列。\n\n"
            "你拥有长期记忆，需要时用 recall 搜索相关记忆。"
        ),
        "_volatile": True,
    }


def mock_project_msg():
    return {
        "role": "system",
        "content": "当前项目: test-project | 语言: Python",
        "_volatile": True,
    }


def mock_rag_msg():
    return {
        "role": "system",
        "content": "📚 RAG 代码索引已就绪，可用 rag_query 进行语义搜索。",
        "_volatile": True,
    }


def mock_safety_msg():
    return {"role": "system", "content": "安全模式: MANUAL", "_volatile": True}


def mock_memory_msg(idx: int = 0):
    return {
        "role": "system",
        "content": f"你拥有以下记忆（需要时用 recall 搜索详情）：\n  记忆-{idx}: 用户偏好 Python",
        "_volatile": True,
    }


# ═══════════════════════════════════════════════════════════════
# 测试套件
# ═══════════════════════════════════════════════════════════════

results: list[dict] = []


def test(name: str, fn):
    """运行一个测试并记录结果。"""
    try:
        fn()
        results.append({"name": name, "status": "PASS"})
    except AssertionError as e:
        results.append({"name": name, "status": "FAIL", "error": str(e)})
    except Exception as e:
        results.append({"name": name, "status": "ERROR", "error": str(e)})


# ── 测试 1: 空会话前缀稳定性 ──

def test_empty_startup():
    """新会话启动后，prefix 哈希应该只取决于 prefix 消息。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    # 未加任何对话时
    p1 = api_bytes(ctx.send(validate=False))
    # 再 send 一次
    p2 = api_bytes(ctx.send(validate=False))

    assert p1 == p2, "未加对话时两次 send() 字节应完全一致"


# ── 测试 2: 追加对话后，前缀部分不变 ──

def test_conversation_growth():
    """每追加一轮对话，之前的所有消息在字节层面应保持不变。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    prev_msgs = ctx.send(validate=False)
    prev_payload = api_bytes(prev_msgs)

    for turn in range(5):
        ctx.log.append({"role": "user", "content": f"问题 {turn}"})
        ctx.log.append({"role": "assistant", "content": f"回答 {turn}"})

        curr_msgs = ctx.send(validate=False)
        curr_payload = api_bytes(curr_msgs)

        # 消息级：旧消息全部按序出现在新 payload 开头
        common_msgs = cache_prefix_messages(prev_msgs, curr_msgs)
        assert common_msgs == len(prev_msgs), (
            f"第 {turn} 轮: 旧 {len(prev_msgs)} 条消息中 {common_msgs} 条匹配"
        )

        # 字节级：去掉 JSON 数组尾部后全部匹配
        cached = cacheable_prefix_bytes(prev_payload, curr_payload)
        prev_trimmed = prev_payload.rstrip().rstrip("]")
        assert cached == len(prev_trimmed), (
            f"第 {turn} 轮: 可缓存 {len(prev_trimmed)} 字节, 实际命中 {cached} 字节"
        )

        prev_msgs = curr_msgs
        prev_payload = curr_payload


# ── 测试 3: log system 消息放末尾，不影响前缀 ──

def test_log_system_at_end():
    """安全模式、记忆索引等 log system 消息应在末尾，不扰动前缀。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    # 加对话 + 加安全模式（在 log 里）
    ctx.log.append({"role": "user", "content": "hello"})
    ctx.log.append({"role": "assistant", "content": "hi"})
    ctx.log.append(mock_safety_msg())
    ctx.log.append(mock_memory_msg())

    payload = api_bytes(ctx.send(validate=False))
    msgs = json.loads(payload)

    # 验证顺序：prefix → 对话 → log system（末尾）
    assert msgs[0]["content"] == mock_env_msg()["content"], "第1条应为 env"
    assert msgs[1]["content"] == mock_project_msg()["content"], "第2条应为 project"
    assert msgs[2]["content"] == mock_rag_msg()["content"], "第3条应为 RAG"
    assert msgs[3]["role"] == "user", "第4条应为 user"
    assert msgs[4]["role"] == "assistant", "第5条应为 assistant"
    # log system 在末尾
    assert msgs[5]["role"] == "system", "第6条应为 system（安全模式）"
    assert msgs[6]["role"] == "system", "第7条应为 system（记忆）"


# ── 测试 4: 压缩后的前缀稳定性 ──

def test_compaction_prefix():
    """Append-only 压缩：旧消息全保留，摘要追加到末尾，前缀不变。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    # 模拟多轮对话
    for i in range(10):
        ctx.log.append({"role": "user", "content": f"问题 {i}"})
        ctx.log.append({"role": "assistant", "content": f"回答 {i} 非常长的内容 " * 5})

    before_msgs = ctx.send(validate=False)
    before_payload = api_bytes(before_msgs)

    # ── 模拟 append-only 压缩：追加摘要到末尾，不删旧消息 ──
    brief = "早期讨论了问题0~6，涉及文件读写和 git 操作。"
    ctx.log.append({
        "role": "system",
        "content": f"⏪ 对话摘要：{brief}",
    })

    after_msgs = ctx.send(validate=False)
    after_payload = api_bytes(after_msgs)

    # 所有旧消息完整保留
    common_msgs = cache_prefix_messages(before_msgs, after_msgs)
    assert common_msgs == len(before_msgs), (
        f"旧 {len(before_msgs)} 条消息应全部保留，实际匹配 {common_msgs}"
    )

    # 字节级：旧 payload 的全部可缓存字节应在新的里
    cached = cacheable_prefix_bytes(before_payload, after_payload)
    before_trimmed = before_payload.rstrip().rstrip("]")
    assert cached == len(before_trimmed), (
        f"旧 {len(before_trimmed)} 可缓存字节，实际命中 {cached}"
    )

    # 验证摘要追加在末尾
    msgs = json.loads(after_payload)
    last = msgs[-1]
    assert last["role"] == "system"
    assert "对话摘要" in last["content"]
    assert brief in last["content"]

    # 验证历史消息数 = 原始 + 1（仅多了一条摘要）
    assert len(msgs) == len(json.loads(before_payload)) + 1


# ── 测试 5: 记忆变更后原地替换 ──

def test_memory_update():
    """记忆变更时原地替换 log 中的 system 消息，因为是末尾所以不影响前缀。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    ctx.log.append({"role": "user", "content": "hello"})
    ctx.log.append({"role": "assistant", "content": "hi"})
    ctx.log.append(mock_memory_msg(0))

    before = api_bytes(ctx.send(validate=False))

    # 模拟记忆变更：原地替换
    old_idx = next((i for i, m in enumerate(ctx.log)
                    if m.get("role") == "system" and "你拥有以下记忆" in m.get("content", "")), -1)
    assert old_idx >= 0, "应该找到旧的记忆消息"
    ctx.log[old_idx] = mock_memory_msg(1)  # 新版本记忆

    after = api_bytes(ctx.send(validate=False))

    # 记忆消息在 log system 区，send() 放末尾
    # 前缀（prefix + 对话）不应该变
    msgs_before = json.loads(before)
    msgs_after = json.loads(after)

    # 前 5 条（prefix 3 + user 1 + assistant 1）应该一致
    for i in range(5):
        assert msgs_before[i] == msgs_after[i], f"第 {i} 条消息发生了改变"


# ── 测试 6: 会话保存/加载周期 ──

def test_save_load_cycle():
    """保存再加载后，prefix 应该干净（不含旧 env/project）。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])
    ctx.log.append(mock_safety_msg())
    ctx.log.append({"role": "user", "content": "hello"})
    ctx.log.append({"role": "assistant", "content": "hi"})

    # 模拟 save_session 过滤
    persist = [m for m in ctx.all if not m.get("_volatile")]
    # 验证：env/project/rag/safety 都是 volatile，不应出现在持久化数据中
    volatile_roles = [m["content"][:20] for m in persist]
    assert not any("当前环境" in c for c in volatile_roles), "env 不应被持久化"
    assert not any("当前项目" in c for c in volatile_roles), "project 不应被持久化"
    assert not any("RAG" in c for c in volatile_roles), "RAG 不应被持久化"
    assert not any("安全模式" in c for c in volatile_roles), "safety 不应被持久化"

    # 模拟加载：log_msgs=persist
    ctx2 = CacheContext(log_msgs=persist)
    ctx2.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])
    ctx2.log.append(mock_safety_msg())

    payload = api_bytes(ctx2.send(validate=False))
    msgs = json.loads(payload)

    # 应该只有 1 份 env、1 份 project、1 份 rag（都在 prefix）
    env_count = sum(1 for m in msgs if "当前环境" in m.get("content", ""))
    project_count = sum(1 for m in msgs if "当前项目" in m.get("content", ""))
    rag_count = sum(1 for m in msgs if "RAG" in m.get("content", ""))
    assert env_count == 1, f"env 消息应只有 1 份，实际 {env_count}"
    assert project_count == 1, f"project 消息应只有 1 份，实际 {project_count}"
    assert rag_count == 1, f"RAG 消息应只有 1 份，实际 {rag_count}"


# ── 测试 7: 跨会话前缀哈希一致性 ──

def test_cross_session_hash():
    """相同 prefix 内容的两个 CacheContext 应该有相同的 prefix_hash。"""
    prefix = [mock_env_msg(), mock_project_msg(), mock_rag_msg()]

    ctx1 = CacheContext()
    ctx1.rebuild_prefix(prefix)

    ctx2 = CacheContext()
    ctx2.rebuild_prefix(prefix)

    assert ctx1.prefix_hash == ctx2.prefix_hash, (
        f"相同 prefix 内容应该产生相同哈希: {ctx1.prefix_hash} vs {ctx2.prefix_hash}"
    )

    # 加不同的对话不应影响 prefix hash
    ctx1.log.append({"role": "user", "content": "A"})
    ctx2.log.append({"role": "user", "content": "B"})
    assert ctx1.prefix_hash == ctx2.prefix_hash, "不同对话不应改变 prefix hash"


# ── 测试 8: 工具调用后前缀不变 ──

def test_tool_cycle():
    """一轮完整的工具调用循环后，前缀不受影响。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    ctx.log.append({"role": "user", "content": "读文件"})

    before_prefix = api_bytes(ctx.send(validate=False))
    # 截到 prefix 消息数
    prefix_msg_count = len(ctx.prefix)
    before_prefix_bytes = json.dumps(
        json.loads(before_prefix)[:prefix_msg_count], ensure_ascii=False
    )

    # 模拟 assistant 带 tool_calls
    ctx.log.append({
        "role": "assistant",
        "content": "我来读",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "test.py"}'}}]
    })
    ctx.log.append({
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "print('hello')",
    })

    after = api_bytes(ctx.send(validate=False))
    after_prefix_bytes = json.dumps(
        json.loads(after)[:prefix_msg_count], ensure_ascii=False
    )

    assert before_prefix_bytes == after_prefix_bytes, "工具调用不应改变 prefix 字节"


# ── 测试 9: 优化建议 - 计算可缓存前缀占比 ──

def test_cache_efficiency():
    """分析缓存效率：多轮对话中缓存前缀占总 payload 的比例。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([mock_env_msg(), mock_project_msg(), mock_rag_msg()])

    print("\n  ── 缓存效率分析 ──")
    prev_payload = ""
    stats = []

    for turn in range(10):
        ctx.log.append({"role": "user", "content": f"问题 {turn}: 帮我修改 src/main.py 的第 {turn*10} 行"})
        ctx.log.append({"role": "assistant",
                        "content": f"好的，我来修改第 {turn*10} 行，这里需要改掉旧的逻辑。"})
        if turn % 3 == 0:
            ctx.log.append(mock_safety_msg())

        curr_msgs = ctx.send(validate=False)
        curr_payload = api_bytes(curr_msgs)

        if prev_payload:
            cached = cacheable_prefix_bytes(prev_payload, curr_payload)
            ratio = cached / len(curr_payload) * 100
            stats.append((turn, len(prev_payload), cached, len(curr_payload), ratio))

        prev_payload = curr_payload

    print(f"  {'轮次':<6} {'旧payload':>10} {'可缓存':>10} {'新payload':>10} {'命中率':>8}")
    for turn, old_len, cached, new_len, ratio in stats:
        bar = "█" * int(ratio / 5) + "░" * (20 - int(ratio / 5))
        print(f"  {turn:<6} {old_len:>10,} {cached:>10,} {new_len:>10,} {ratio:>7.1f}% {bar}")

    if stats:
        avg_ratio = sum(s[3] for s in stats) / len(stats)
        print(f"\n  平均缓存命中率: {avg_ratio:.1f}%")
        # 缓存命中率应该稳定在高位
        for turn, _, _, _, ratio in stats:
            assert ratio >= 70.0, f"第 {turn} 轮缓存命中率过低: {ratio:.1f}%"


# ═══════════════════════════════════════════════════════════════
# 运行所有测试
# ═══════════════════════════════════════════════════════════════

TESTS = [
    ("空会话 prefix 稳定", test_empty_startup),
    ("多轮对话前缀不退化", test_conversation_growth),
    ("log system 放末尾", test_log_system_at_end),
    ("压缩后前缀不变", test_compaction_prefix),
    ("记忆变更不扰动前缀", test_memory_update),
    ("保存/加载无冗余", test_save_load_cycle),
    ("跨会话哈希一致", test_cross_session_hash),
    ("工具调用后前缀不变", test_tool_cycle),
    ("缓存效率分析", test_cache_efficiency),
]

for name, fn in TESTS:
    test(name, fn)

# ── 报告 ──
print("\n" + "═" * 60)
print(f"  前缀缓存测试结果: {sum(1 for r in results if r['status']=='PASS')}/{len(results)} 通过")
print("═" * 60)

for r in results:
    status = "✅" if r["status"] == "PASS" else "❌"
    print(f"  {status} {r['name']}")
    if r["status"] != "PASS":
        print(f"     → {r['error']}")

failed = [r for r in results if r["status"] != "PASS"]
if failed:
    print(f"\n⚠️  {len(failed)} 项未通过，需要修复。")
    sys.exit(1)
else:
    print("\n✅ 全部通过")
