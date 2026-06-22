# 自动驾驶 Psyche ↔ 知识库链接

> 每条 psyche 中的判断，标注其知识库来源
> 格式: psyche 判断 → knowledge/ 具体文件
> 版本: 0.2

---

## 来自轨迹预测子领域的支撑

| Psyche 判断 | 来源 |
|------------|------|
| 轨迹预测benchmark已近饱和但部署不可靠 | knowledge/trajectory-prediction/domain-map.md → 八个"假" |
| 模式坍塌导致多模态预测不真实 | knowledge/trajectory-prediction/notes/ → Mode Collapse + WiTA |
| 交互模型不稳定，有些agent有害 | knowledge/water-ideas-v2.md → 2604.03463 |
| 评估/诊断/审计是单人最佳切入点 | knowledge/trajectory-prediction/domain-map.md → 路线竞争强度 |

## 来自世界模型调研的支撑

| Psyche 判断 | 来源 |
|------------|------|
| 世界模型2026最热但资源需求大 | knowledge/search/autonomous-driving-world-model-survey-2026.md |
| 视频生成世界模型 vs 3D occupancy路线 | knowledge/search/autonomous-driving-world-model-survey-2026.md → 技术路线聚类 |

## 来自全景测绘的支撑

| Psyche 判断 | 来源 |
|------------|------|
| 自动驾驶领域3种范式分类 | psyche/autonomous-driving/阅读索引/reading-log.md → 2306.16927 |
| LLM延迟 vs 实时控制矛盾 | psyche/autonomous-driving/阅读索引/reading-log.md → 2603.11093 |
| 开环-闭环gap是核心问题 | psyche/autonomous-driving/阅读索引/reading-log.md → 2306.16927 |

## 来自本轮进化的支撑

| Psyche 判断 | 来源 |
|------------|------|
| 证据等级标的是"来源"不是"理解深度" | 2026-06-21对话: 用户批评浅推荐 → 暴露conviction gap |
| 五筛子+证据等级标注有效 | 2026-06-21对话: 加载psyche后推荐质量明显改善 |
| 构建流程（四阶段+关口）可跑通 | 2026-06-21: 自动驾驶psyche首轮构建实践 |
