# DeepSeek 缓存命中突刺：调查报告

> 调查时间：2026-06，CTGents 自身会话实测数据。
> 配套文档：[`cache-design.md`](cache-design.md)（缓存友好架构设计） ·
> [`../CACHE_SPIKE_DIAGNOSIS.md`](../CACHE_SPIKE_DIAGNOSIS.md)（取证工具与归因决策树）。

## 一句话结论

三段式缓存架构已生效（命中常态 90%+），但偶发的命中**突刺**（如 96% → 42%）经字节级取证证明
是 **DeepSeek 服务端 KV 缓存的 best-effort 容量/LRU 淘汰**——**客户端、工具表、节点路由全部排除**，
客户端无法消除，只能通过控制 payload 体积减小被淘汰时的损失。

---

## 一、问题

`docs/cache-design.md` 的三段式上下文（不可变前缀 + append-only 日志）把命中率从 ~0% 拉到 90%+。
但 `/context` 里持续观察到**单次请求命中率突然塌陷**：前一条 96–100%，某一条骤降到 50% 甚至 42%，
下一条又立刻恢复。问题：这是**客户端 payload 变了**（我们的 bug，可修），还是 **DeepSeek 服务端
没命中**（基建行为，修不了）？

不靠猜，靠取证。

## 二、方法：字节级地面真相

构建了两件取证工具（默认关，`CTG_DUMP_PAYLOADS=1` 开启，零常态开销）：

1. **落盘真实请求**：每次发给 DeepSeek 的完整 `canonical_request`（model/messages/tools/max_tokens/
   stream/...）+ 返回的 `usage` + `system_fingerprint` + `messages_hash`/`tools_hash`/`request_hash`，
   写入 `stats/payloads/<session>/req_NNNN.json`。
2. **相邻对账** `scripts/payload_diff.py`：逐消息比对相邻两次请求，输出 `full_lcp_ratio`（字符级最长
   公共前缀占比）、各哈希/指纹是否变化、`first_diff` 分叉点，并与服务端报的命中对账。

核心判据——**纯追加时「本该命中」不靠估算**：若本次 payload 的前 N 条与上次逐字节相同且 N == 上次
全部条数（纯追加），那本该命中 ≈ 上次的真实 prompt_tokens（那整段之前发过、应已缓存）。命中远低于
此值 = 服务端把缓存过的前缀扔了。

## 三、证据

### 3.1 突刺时，客户端 payload 几乎逐字节相同

| 请求 | full_lcp | 命中 | tools_hash | system_fingerprint |
|---|---|---|---|---|
| #36 | 0.998 | 82,816 / 82,890（**100%**） | 不变 | `fp_9954b31ca7_…_kvcache_20260402` |
| **#37** | **0.999** | 32,896 / 79,197（**42%**） | **不变** | **不变** |
| #38 | 0.992 | 79,744 / 80,141（**100%**） | 不变 | 不变 |

`#37` 的 `first_diff` 落在追加尾（messages[93]，新追加的 assistant + user 两条）——前 93 条与
`#36` **逐字节相同**。同一节点、同一份工具、99.9% 相同的前缀，服务端却只命中 42%、把刚缓存的
**约 5 万 token 前缀扔了**，且 `#38` 立刻恢复 100%。

### 3.2 两个备选假设被当场证伪

- **节点路由**：`system_fingerprint` 全程一个字符没变（始终 `fp_9954b31ca7_…_kvcache_20260402`）。
  → 不是请求被路由到了别的后端节点，**自始至终同一 KV 缓存节点**。排除。
- **工具表变化**：`tools_hash` 全程不变（`c1215abd2e625b53`）。tools 在 DeepSeek 前缀里排在
  messages 之前，若变会令整个前缀作废——但它没变。**字节哈希实证了 tools 恒定**。排除。

### 3.3 `g=0.0s` 背靠背仍淘汰 → 排除 TTL

另一会话中观察到间隔 `g=0.0s`（背靠背、零时间间隔）的请求仍被吃掉数千 token。
→ 不是缓存过期（TTL），是**容量/LRU 淘汰**，连"隔太久"都排除。

### 3.4 淘汰量随 payload 体积增大

`#16` 在 ~33k token 量级吃掉约 12k；`#37` 在 ~82k 量级吃掉约 50k。
→ 对话越大，被一次性淘汰的前缀越多。

## 四、一个反常细节：DeepSeek 自报的 prompt_tokens 会抖

突刺时 DeepSeek 报告的输入 token 数本身不稳定。从落盘文件实测：

| 请求 | DeepSeek 报 prompt_tokens | 我方落盘 messages 字符数 | 条数 |
|---|---|---|---|
| #36 | 82,890 | 255,547 | 93 |
| #37 | **79,197**（↓ 3,693） | **255,804**（↑ 257） | 95 |

`#37` 的 messages 是 `#36` 的**逐字节纯超集**（前 93 条相同 + 追加两条，共 +257 字符）——
**真实输入是涨的**。但 DeepSeek 给这个更大的输入报了**更小**的 prompt_tokens。

含义：**DeepSeek 的 prompt_tokens 不是输入字节的稳定函数**——同一段相同前缀，在「几乎全命中」与
「被淘汰一半」两种状态下，它报出的总 token 数不同（其内部 hit+miss 自洽，但总数随缓存状态漂移）。
这是服务端会计口径问题，与客户端统计无关；反过来也佐证了那段前缀确实在服务端被**重新处理**了
（从命中态掉回重算态），正是淘汰发生的指纹。

## 五、责任归属与可行动项

**归属：DeepSeek 服务端 best-effort 缓存淘汰。** 客户端、工具、节点路由经字节/指纹级证据全部排除。
DeepSeek 官方明确缓存为「尽力而为、不保证命中」，与本结论一致。

**"经常性 miss 增高" 拆两层**：
- **基线（~900 token/轮）**：工具循环每轮追加新工具结果，是真新内容、必然 miss、**正常**，
  不是可优化项（这是工具型 agent 的固有形状）。
- **尖刺（数千~数万）**：服务端淘汰，**非客户端可控**。

**唯一能动的客户端杠杆 = 控制 payload 体积**（不是为了"防淘汰"——防不住，而是为了"被淘汰时少丢、
重新 miss 更便宜、推迟 65% 压缩"）：
- 已落地 `read_file` 整文件读封顶（`CTG_READ_FILE_MAX_CHARS`，默认 24000）。
- 工具结果按信号密度压缩（见 `cache-design.md`）。

**不建议做的**：
- `CTG_KVCACHE_USER_ID`（extra_body user_id 隔离实验）：premise 是"多进程互挤"，但本案 sysfp 稳定 +
  淘汰随自身 payload 增大 + 立刻恢复，画像是自身大前缀的容量压力 / 共享节点别家流量，**非 user_id
  可隔离**。保留为实验旋钮但默认关，不期待有效。

## 六、量级精度声明（诚实边界）

"服务端吃掉 ~N token" 的**方向 100% 可信**（byte 相同前缀只命中 42% = 淘汰铁定发生），但**精确量级
不可信**：它以「上次 prompt_tokens」为基准，而第四节证明该基准本身会随缓存状态漂移。结论只应在
**"几万量级、随 payload 增大"** 的粒度上使用，不应钉到具体 token 数。

## 七、复现

```powershell
$env:CTG_DUMP_PAYLOADS = "1"   # 开启落盘（默认关）
python run.py                   # 跑一段带工具的对话，制造突刺
python scripts/payload_diff.py  # 相邻对账，看 full_lcp / sysfp / tools_hash
```

归因决策树见 [`../CACHE_SPIKE_DIAGNOSIS.md`](../CACHE_SPIKE_DIAGNOSIS.md)。
