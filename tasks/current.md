# Tavily 多 Key 轮换

- [x] Step 1: `.env` — `TAVILY_API_KEY` → `TAVILY_API_KEYS`（逗号分隔两个 key）
- [x] Step 2: `config.py` — 新增 `MultiKeyTavilyClient` wrapper + 更新 `get_tavily_client()`
  - 解析 `TAVILY_API_KEYS`、降级兼容 `TAVILY_API_KEY`
  - wrapper 代理 `search` 等方法，`UsageLimitExceededError` / `InvalidAPIKeyError` 切下一个 key 重试
  - 验证：`MultiKeyTavilyClient` + 两 key 加载成功
- [x] Step 3: 加单测 — key 耗尽 / 切换后重试成功（5 条，全绿）
- [o] Step 4: ruff + 全量测试 → commit
