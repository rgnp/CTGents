# DeepSeek 缓存命中突刺取证手册

目标**不是优化**，是**取证归因**：某次请求命中率突然塌（如 96% → 50%），到底是
**客户端 payload 变了**（我们的锅，可改代码），还是**DeepSeek 服务端缓存未命中**
（best-effort 淘汰/节点路由，客户端无能为力）。靠落盘真实请求 + 相邻字节级对账定责，不靠猜。

## 一、采证

```powershell
$env:CTG_DUMP_PAYLOADS = "1"     # 落盘每次真实 wire 请求
python run.py                     # 跑一段带工具的对话，制造突刺
python scripts/payload_diff.py    # 相邻对账（自动挑最新会话）
```

落盘内容（`stats/payloads/<session>/req_NNNN.json`）每条含：

| 字段 | 含义 |
|---|---|
| `canonical_request` | 真实 wire 请求字段：model/messages/tools/max_tokens/stream/stream_options/extra_body |
| `usage` | 真实 prompt/cache_hit/cache_miss/completion tokens |
| `system_fingerprint` | 后端节点指纹（DeepSeek 可能不返回 → null） |
| `messages_hash` / `tools_hash` / `request_hash` | 三个层面的内容哈希，一眼看出哪部分变了 |

**注意**：采证默认关（`CTG_DUMP_PAYLOADS` 未设时零落盘零开销）。tools 全量落盘，
每文件偏大（含 ~1 万 token 工具表），只在取证时开。

## 二、归因决策树

`payload_diff.py` 每对相邻请求输出三行：**判决行**（真公共前缀 + 命中 + 判决）、
**取证行**（full_lcp_ratio + 各哈希/指纹/尾部 delta）、**first_diff**（分叉点路径 + 前后内容）。

按以下顺序看，第一个命中的就是责任归属：

### ① `tools_hash ⚠`（变了）
工具表变了。tools 在 DeepSeek 前缀里排在 messages **之前**，它一变、后面 messages 再相同也
**整体作废**。→ **锅在客户端的 tools 构造**（get_tools 应字节确定，若变了说明有非确定性，往那查）。

### ② `sysfp ⚠`（system_fingerprint 变了）
请求被路由到**另一个后端节点**，那节点没有这段前缀的 KV 缓存。→ **服务端未命中、客户端无责**。
这是把"服务端淘汰"精确成"节点路由"的关键信号。（DeepSeek 不返回此字段时这条线断，看 ③。）

### ③ `full_lcp` 高（如 >0.9）但命中率低
客户端发的前缀**字节相同**（full_lcp 高 = payload 没变），服务端却没命中。
→ **服务端 best-effort 淘汰，客户端无责**。佐证：若伴随大 `g`（间隔）疑容量/TTL；
**`g=0.0s` 背靠背仍塌 = 纯容量/LRU**，连 TTL 都排除。

### ④ `❌真改历史 @ 对话第 k 条`
`first_diff` 落在对话中段（不是追加尾、不是尾部牙边界）= **某条旧消息被原地改/删**。
→ **客户端 bug**。看 first_diff 的"旧/新"内容，定位那条消息的生产路径去修。

### ⑤ `✅尾部浮动`
对话部分逐字节相同，只是尾部 system 牙每轮飘到末尾、重发 ~N est token。
→ **设计成本，非 bug**。开 `CTG_NO_VOLATILE_TAIL=1` 可消除（但会丢行为牙的 recency 摆位）。

## 三、已知结论（2026-06，本仓实测）

- 用 `CTG_NO_VOLATILE_TAIL=1` 跑过对照：尾部一关全变纯追加，但**纯追加照样塌**——
  某次逐字节加 2 条新消息，服务端把 44 秒前刚缓存的 1 万 token 扔了；另一次 `g=0.0s`
  背靠背照吃 2747 token。→ **命中塌陷 = DeepSeek 服务端 LRU 淘汰，客户端修不了**。
- "经常性 miss 增高"拆两层：**基线 ~900/轮 = 工具循环每轮追加新工具结果**（真新内容、
  必 miss、正常）；**尖刺 = 服务端淘汰**（非客户端）。
- 唯一能动的客户端杠杆 = **工具输出体积**（压瘦 → 基线 miss 更低 + 被淘汰时损失更小）。
  已落地 `read_file` 整文件读封顶（`CTG_READ_FILE_MAX_CHARS`）。

## 四、KVCache 隔离实验（实验性，premise 未证）

```powershell
$env:CTG_KVCACHE_USER_ID = "1"   # 给每次请求加 extra_body={"user_id":"ctgents-<session>"}
```

premise：DeepSeek 缓存可能被多进程共用 key 互相挤占。给每进程不同 user_id **可能**隔离 KVCache。
**但 DeepSeek 文档上前缀缓存按 prefix 内容、未说按 user_id 分区**——此旋钮仅用于测"多进程互挤"
假设，**别当答案**。若 DeepSeek 不接受 extra_body 可能 400，故默认关。
