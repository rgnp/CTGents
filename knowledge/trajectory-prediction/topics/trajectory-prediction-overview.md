# 轨迹预测领域全景（2022-2026）

## 基准数据集

| 数据集 | 场景数 | 特征 | 主要使用 |
|:------|:------|:-----|:--------|
| Argoverse 1 | 323K | HD map + 轨迹 | HiVT, LaneGCN, DenseTNT |
| Argoverse 2 | 250K | HD map + 轨迹 | ADAPT, Forecast-MAE, UniTraj |
| nuScenes | 40K | 3D点云+轨迹 | PreTraM, Trajectron++ |
| Waymo Open | 104K | 3D点云+轨迹 | SceneTransformer, Wayformer |
| Interaction | 40K | HD map+轨迹 | ADAPT |

## 方法家族

### 1. 向量化 Transformer（你在这）
- HiVT (CVPR 2022) → ADAPT (ICCV 2023) → LAformer (CVPRW 2024)
- 特点：agent-centric，向量化表示，计算效率高
- 平台：Argoverse

### 2. 场景级 Transformer
- SceneTransformer (NeurIPS 2021) → Wayformer (ICLR 2023)
- 特点：scene-centric，全场景joint attention
- 平台：Waymo，计算量大

### 3. 车道图 GNN
- LaneGCN (ECCV 2020) → GOPHER (CoRL 2022)
- 特点：车道图结构 + GNN 消息传递
- 平台：Argoverse

### 4. 目标驱动
- TNT (2020) → DenseTNT (CVPR 2022) → Goal-SAR (2022)
- 特点：先预测目标位置，再生成轨迹
- 优势：天然多模态

### 5. 生成式（GAN/CVAE/扩散）
- SocialGAN (CVPR 2018) → Trajectron++ (CoRL 2020) → 扩散方法 (2023-)
- 特点：生成式建模多模态分布
- 趋势：扩散方法正在快速增长

### 6. 联合/交互预测
- M2I (CVPR 2022) → 各类joint prediction
- 特点：显式建模agent间交互依赖

## 关键指标

| 指标 | 含义 | 盲点 |
|:----|:----|:----|
| ADE/FDE | 平均/最终位移误差 | 不考虑规划影响 |
| minADE/minFDE | 多模态最佳匹配 | 忽略模式覆盖 |
| off-road rate | 是否冲出道路 | 不区分严重程度 |
| MR | miss rate (>2m阈值) | 二值化丢失信息 |
| Planning cost | 下游规划器表现 | 计算复杂 |

## 重要认知

### "What Truly Matters" 的核心发现
- ADE/FDE 提升 ≠ 规划性能提升 （dynamics gap）
- 原因：固定数据集丢失了"预测影响规划→规划影响他人"的闭环
- 启示：辅助任务不仅要提升预测指标，更要考虑**规划可用性**
