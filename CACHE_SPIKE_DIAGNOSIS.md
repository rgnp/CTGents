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

`payload_diff.py` 每对相邻请求输出：**判决行**（`client_prefix_status` + 命中 + 判词）、
**取证行**（full_lcp_ratio + 各哈希/指纹/尾部 delta）、**first_diff**（分叉点路径 + 前后内容）。

**核心纪律：先看 `client_prefix_status`（纯结构、可证伪），再谈命中。** 这个分类**只看字节/哈希、
不掺任何 token 数**，所以可信；命中的「缺口 token」是次级估算、**不精确**（见 §三补充）。

### client_prefix_status —— 客户端这一轮对前缀做了什么（按优先级，前者盖后者）

| status | 判据（纯字节/哈希） | 责任 |
|---|---|---|
| `tools_changed` | `tools_hash` 变了 | **客户端**。tools 在前缀里排 messages 前，一变整段前缀作废，命中掉属预期。查 get_tools 非确定性。 |
| `history_mutated` | `first_diff` 落在对话中段 messages[0..N-1] | **客户端 bug**。某条旧消息被原地改/删。看 first_diff 旧/新，定位那条的生产路径。 |
| `tail_only_changed` | 对话部分逐字节相同，只末尾浮动 system 牙不同 | **设计成本**，非 bug。命中异常**不在此判服务端**（浮动尾部本身造成部分 miss、归因不干净）。`CTG_NO_VOLATILE_TAIL=1` 可消除浮动但丢行为牙 recency。 |
| `pure_append` | 上一轮整个 payload 是这一轮的逐字节前缀 | 只追加、没动旧内容。**唯一能干净判服务端的场景**，见下。 |

### 命中异常的归因（**只在 `pure_append` 下**判服务端）

`pure_append` 且 `hit` 远低于上轮可复用前缀 → **服务端 best-effort 缓存未命中，客户端无责**。
- **判定可信**：纯追加时任何超出「追加尾」的 miss 都没有客户端解释。
- **缺口 token 数不精确**：基准「上轮 prompt」本身会随 cache hit/miss 漂移（见报告第四节），
  故判词写「按上轮prompt估算可复用≈X，实命中Y，缺口≈Z（估算）」，**不写「服务端吃掉=Z」这种硬数字**。
- 佐证：`sysfp ⚠`（路由到别的节点、那节点没缓存）；`g=0.0s` 背靠背仍塌 = 纯容量/LRU（连 TTL 都排除）。
  （DeepSeek 常不返回 system_fingerprint，此佐证线可能为空。）

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
