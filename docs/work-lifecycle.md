# 统一工作生命周期回执

> 状态：现役
> 目标：让 Task、Heartbeat、认知资产和 Gap 共享工作身份、验收证据与产物版本，
> 但不制造一个接管所有业务状态的中央状态机。

## 一、所有权

| 子系统 | 仍然唯一拥有 | 共享回执只引用 |
|---|---|---|
| Task | 目标、步骤、停止条件、验收与归档 | task work_id、验收结果、归档与声明产物版本 |
| Heartbeat | frontier、预算、锁、自暂停、出处闸与 digest | 每跳结果、知识产物版本、交付与用户处置 |
| Gap | discovered→verified 生命周期和原信号复核 | Task 文本中的 `gap <12位ID>` |
| 认知资产 | retrieved→feedback 事件 | 与 Task 相同的 task_key/work_id |

共享层位于 `src/work_receipts.py`，台账位于被 Git 忽略的
`tasks/work-receipts.jsonl`。它是 append-only 事实索引，不是新的任务队列。

## 二、统一回执

每条回执包含：

- `work_id`：所属系统生成的稳定工作身份；
- `source`：`task` 或 `heartbeat`；
- `stage`：完成、失败、放弃、送达或用户处置；
- `evidence_sha256`：完整证据的内容身份，正文只保留尾部用于展示；
- `workspace_fingerprint`：Task 完成/失败时的完整工作区指纹；
- `artifacts[]`：项目内产物的相对路径、SHA-256 和字节数；
- `links[]`：只引用 Gap、认知资产或 Heartbeat run receipt，不复制对方状态；
- `idempotency_key`：同一运行的竞争写入合并，不同真实运行保持独立。

回执文件最多保留最近 1000 条；坏行跳过，写入失败不阻塞原业务流程。

## 三、两条工作流

### Task

```text
current.md
  → AcceptanceSpec 机械验收
  → failed：保留任务 + 写失败回执
  → completed：写验收证据 + 归档 + 版本化归档/声明产物 + 写完成回执
  → clear：写 abandoned 回执
```

`/task archive` 也必须通过 `archive_current_if_accepted`，不能绕开验收。
Task 回执复用认知资产的 task_key；若任务文本包含 `gap <12位ID>`，只增加 `gap:<id>`
链接，Gap 状态仍以自己的 ledger 为准。

### Heartbeat

```text
frontier 第一活跃项
  → 隔离 worker
  → 出处闸 + frontier 是否真推进
  → completed / failed run receipt + knowledge 文件版本
  → digest delivered receipt
  → 用户 /heartbeat accept | revise | reject
```

“写了文件”不等于 Heartbeat 完成：出处闸必须通过且 frontier 必须实际变化。
多个 run 可以合并成一个 delivery；delivery 通过 links 指向自上次交付后的 run receipts。
用户处置只评价交还，不自动修改 frontier、Knowledge 或 Memory。

## 四、版本口径

这里的“版本”不是复制 Git：

- 代码型 Task 使用现有 `workspace_fingerprint`，绑定 HEAD、tracked diff 和未跟踪文件内容；
- 声明产物和任务归档使用文件 SHA-256；
- Heartbeat 使用本跳写入的 Knowledge 文件 SHA-256；
- 验收命令的运行环境和退出码仍由 `verification_receipts.py` 拥有。

因此可以回答“这个结果基于哪份工作区、交付的是哪一版文件”，又不重复保存文件内容。

## 五、观测与降级

`/organs` 追加共享工作回执摘要：

- 回执总数；
- 版本化产物数；
- 待用户处置的 Heartbeat delivery；
- 通过相同 work_id 关联的认知资产数；
- Task 文本显式引用的 Gap 数。
- 每个产物以最新回执为准的内容漂移和文件缺失数。

共享层写入或解析失败时，Task/Heartbeat 原有完成、归档、摘要和恢复逻辑继续工作。
这层可以缺席，但不能成为主循环的新单点故障。
