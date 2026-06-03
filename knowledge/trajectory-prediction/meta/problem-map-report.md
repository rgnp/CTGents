# 自动驾驶轨迹规划问题地图（Problem Map）

> 生成日期：2026-06-02 | 来源：系统阅读综述 + 顶会论文 + 交叉分析
> 核心目标：从轨迹**预测**（HiVT）出发，找到通往轨迹**规划**的发文路径

---

## 第一部分：全景概览

### 自动驾驶三大技术路线

```
路线A: 模块化管线（感知→预测→规划→控制）
       ├── 传统方法（主导）— 每个模块独立设计、独立训练
       └── 优点：可解释、易调试、模块可替换
       └── 缺点：信息损失、累积误差、预测和规划目标不一致

路线B: 端到端（传感器→控制指令）
       ├── 代表：UniAD, VAD, InterDrive, etc.
       └── 优点：全局优化、无信息瓶颈
       └── 缺点：可解释性差、难以调试、数据需求大

路线C: 预测-规划联合（你在这里 👈）
       ├── 预测器和规划器可微连接 / 联合训练
       └── 优点：保留模块化优势 + 联合优化
       └── 当前热度：🔥 快速增长中（2024-2026）
```

**你当前的定位**：路线 C 的起点——HiVT 是轨迹预测器，下一步是把预测结果**用起来**做规划。

---

## 第二部分：预测层现状（你所在的层级）

### 轨迹预测方法家族

| 方法族 | 代表工作 | 年份 | 平台 | 特点 | 成熟度 |
|--------|---------|:----:|:----:|------|:------:|
| **Vectorized Transformer** | HiVT | CVPR 2022 | Argoverse | 分层编码+高效推理 | ⭐⭐⭐ |
| **Adaptive Head** | ADAPT | ICCV 2023 | Argoverse | 自适应权重 | ⭐⭐⭐ |
| **Scene-level Transformer** | SceneTransformer, Wayformer | 2022-23 | Waymo | 场景级编码 | ⭐⭐⭐ |
| **Lane Graph + GNN** | LaneGCN, GOPHER | 2021-22 | Argoverse | 车道图结构 | ⭐⭐ |
| **Multi-agent Joint** | M2I, DenseTNT | 2022 | Argoverse | 联合预测 | ⭐⭐ |
| **Diffusion-based** | MotionDiffuser, PredDiff | 2023-24 | Waymo | 扩散生成 | ⭐⭐ |
| **Foundation Model** | GPT-Pred, LLM-based | 2024-25 | 多种 | 大模型 | 🌟 新 |

### 你的基础（HiVT 在预测层的位置）

```
优势：
✅ 轻量高效（0.69M 参数，实时推理）
✅ Argoverse 上 SOTA（2022年）
✅ 分层架构可扩展
✅ 开源代码可复现

局限：
❌ 2022年的方法，已被 ADAPT (2023)、PerReg+ (2025) 超越
❌ 纯预测，不感知规划需求
❌ 无不确定性校准
❌ 无闭环评估
❌ 交互建模有限（只有 self-attention）
```

---

## 第三部分：规划层现状（你的目标层级）

### 轨迹规划方法家族

| 方法族 | 代表工作 | 年份 | 特点 | 成熟度 |
|--------|---------|:----:|------|:------:|
| **凸优化（GCS）** | Marcucci et al., *Science Robotics* | 2023 | ✅ 理论保证 ❌ 实时性 | ⭐⭐⭐ |
| **MPC（模型预测控制）** | acados, CasADi based | 经典 | ✅ 成熟 ❌ 非凸难解 | ⭐⭐⭐⭐ |
| **采样式** | MPPI, RRT* variants | 经典 | ✅ 简单 ❌ 效率低 | ⭐⭐⭐ |
| **学习型（模仿学习）** | IL, BC, GAIL | 2020-24 | ✅ 数据驱动 ❌ 分布偏移 | ⭐⭐⭐ |
| **强化学习** | CarPlanner (CVPR 2025) | 2025 | ✅ 闭环 ❌ 训练难 | ⭐⭐ |
| **扩散模型规划** | Diffusion-Planner (ICLR 2025) | 2025 | ✅ 多模态 ❌ 实时性 | ⭐⭐ |
| **基础模型/VLA** | VLA, LMM-Planner | 2025-26 | ✅ 常识推理 ❌ 可靠性 | 🌟 新 |

### 关键发现：预测和规划的目标根本不匹配

| 维度 | 预测器关心 | 规划器关心 |
|------|-----------|-----------|
| **评价指标** | ADE / FDE / MR | 碰撞率 / 舒适度 / 通行效率 |
| **评估方式** | 开环（固定数据集） | 闭环（交互环境） |
| **输出形式** | K 条多模态轨迹 | 单一轨迹 + 置信度 |
| **时间范围** | 3-8 秒 | 1-10 秒（可变化） |
| **对错误的态度** | 所有错误同等权重 | 碰撞边缘的错误不可接受 |
| **交互意识** | 被动观察交互 | 主动影响交互 |

> 🔑 **这个不匹配就是第一个大 Gap**：预测器优化 ADE/FDE 不等于规划器能用好。

---

## 第四部分：问题地图（核心矩阵）

### 预测→规划 接口的 4 个关键子问题

```
预测输出 → 规划输入
   ↓         ↓
  精度      可用性
  速度      安全性
  多模态    确定性
  置信度    鲁棒性
```

| 子问题 | 现有方法 | 当前瓶颈 | 热度 |
|:-------|:---------|:---------|:----:|
| **S1: 不确定性量化与校准** | CCTR (AAAI 2024) 后处理校准 | 训练期未校准；校准≠规划受益 | 🔥🔥🔥 |
| **S2: 预测-规划联合损失** | DiffTORI (NeurIPS 2024) 可微优化 | 计算量大；只做策略表征 | 🔥🔥🔥🔥 |
| **S3: 交互感知闭环评估** | "What Truly Matters" (NeurIPS 2023) | 仅诊断问题，未给出解法 | 🔥🔥🔥 |
| **S4: 规划感知的特征学习** | PiP (Planning-informed Prediction) | 信息流单向（预测→规划为主） | 🔥🔥 |
| **S5: 安全关键场景处理** | Risk-aware planning (MIT 2023) | 非高斯不确定性难处理 | 🔥🔥🔥 |
| **S6: 预测-规划联合架构** | Plan-MAE (2025), PerReg+ (CVPR 2025) | 预训练为主，非联合训练 | 🔥🔥🔥🔥 |

---

## 第五部分：详细 Gap 分析（从你的HiVT出发可做的）

### Gap A: 规划兼容性辅助训练 ★★★★★（最高推荐）

**问题**：HiVT 的 Loss 只优化 ADE/FDE，不关心规划器好不好用。

**核心主张**：在 HiVT 训练中引入一个**规划兼容性 Loss**，让预测轨迹的分布对齐规划器的"好轨迹"分布。

**技术路线**：

```
方案1: 可微规划器作为 Loss
  HiVT 输出预测 → 轻量可微规划器（如简单MPC）→ 规划成本 → 反向传播到 HiVT
  关键点：规划器必须可微（如 DiffTORI 的思路）
  
方案2: 规划器偏好学习
  用专家轨迹或规则定义"好规划"，训练一个规划偏好判别器
  HiVT 的预测轨迹通过判别器打分 → 分数作为 Loss
  
方案3: 轨迹质量评分
  不依赖规划器，直接定义轨迹质量函数（安全性+舒适度+效率）
  HiVT 输出轨迹 → 质量函数打分 → 作为辅助 Loss
```

**支撑证据**：
- [论文: 2023-what-true-matters.md]：预测精度≠规划性能，存在 dynamics gap
- [论文: 2024-cctr.md]：预测校准后规划受益——说明规划对预测有额外需求
- [论文: diffTori NeurIPS 2024]：可微优化路径可行
- [空白: gaps.md#Gap1] ✅ 确认空白

**可论文化**：✅ 高——故事清晰、方法相对直接、Baseline明确

**风险**：
- 可微规划器的设计比较 tricky
- 需要闭环仿真验证（比开环评估复杂）

---

### Gap B: 训练期不确定性校准 ★★★★★

**问题**：CCTR 在训练**后**做校准，HiVT 训练时完全不知道自己的置信度准不准。

**核心主张**：把校准误差（ECE）作为可微 Loss 项，在 HiVT 训练过程中端到端地优化置信度校准。

**技术路线**：

```
HiVT 标准训练 Loss (minADE/minFDE)
  + λ × Calibration Loss (ECE 或 LogS 等可微校准指标)
  
  校准 Loss 设计：
  - ECE（Expected Calibration Error）可微近似
  - 或：预测分布的负对数似然（NLL）
  - 或：区间覆盖率的对齐损失
```

**支撑证据**：
- [论文: 2024-cctr.md]：CCTR 后处理校准有效，但没有做训练期
- [项目判断：CCTR局限性]：训练期校准是自然延伸

**可论文化**：✅ 高——方向明确，CCTR 给了 Baseline 和对比

**优势**：
- 与 HiVT 架构完全正交，可叠加在其他方法上
- 实验成本低（只需改 Loss，不需改模型）

---

### Gap C: 预测编码（Predictive Coding）辅助任务 ★★★★

**问题**：HiVT 训练时用真值做监督，但模型不知道自己"什么时候会犯错"。

**核心主张**：增加一个辅助头，预测**当前预测的误差分布**——把预测误差本身作为自监督信号。

**技术路线**：

```
HiVT 编码器 → 原预测头（轨迹输出）
           → 辅助头（误差预测）
              输出：当前预测的期望误差（scalar 或 distribution）
              标签：‖预测轨迹 - 真值轨迹‖（作为监督信号）
```

**支撑证据**：
- [跨界: cross-domain-ideas.md#假说1]：认知神经科学已验证预测误差作为学习信号有效
- 轨迹预测领域：0 篇相关论文

**可论文化**：✅ 中高——故事新颖，但需要验证可行性

**优势**：
- 真正的空白（文献搜索0相关）
- 有生物启发依据
- 与现有辅助任务正交

**风险**：
- 实现细节需探索
- Reviewer 可能质疑"预测自己的误差"的合理性

---

### Gap D: 交互感知规划的闭环验证框架 ★★★★

**问题**：现有评估都是开环的（固定数据集），无法衡量预测器在闭环中的表现。

**核心主张**：构建一个轻量级闭环仿真环境，以 HiVT 作为预测器，挂接一个简单规划器，评估**规划层指标**（碰撞率、通行效率等）。

**技术路线**：

```
HiVT 预测 → 轻量规划器（如采样+成本函数）→ 控制执行 → 环境反馈
                           ↓
                    评估规划指标（碰撞率、通行效率、舒适度）
                    同时记录预测指标（ADE/FDE），观察相关性
                           ↓
                    结论："预测指标XX提升 → 规划指标XX变化"
```

**支撑证据**：
- [论文: 2023-what-true-matters.md]：预测精度≠规划性能，但未给出解法
- 这是目前社区公认的缺失环

**可论文化**：✅ 中高——本身不做新方法，但可以产出关键洞察

**注意**：
- 这不是方法论文，是**分析/评估论文**
- 适合发 Workshop 或作为方法论文的配套实验
- 可以成为后续方法论文的实验平台

---

### Gap E: 基于 HiVT 的轻量交互规划器 ★★★★

**问题**：HiVT 能预测所有 agent 的轨迹，但没人用它做**规划**。

**核心主张**：用 HiVT 的预测结果 + 一个轻量轨迹优化器，构建预测-规划端到端管线。HiVT 的预测不仅是"输入"给规划器，而是通过可微接口联合优化。

**技术路线**：

```
输入（历史轨迹 + 地图）
  ↓
HiVT 编码器 (frozen or finetune)
  ↓
交互感知的场景特征
  ↓
轻量轨迹优化器（可微）
  ├── 用预测结果做碰撞约束
  ├── 用场景特征初始化规划 warm-start
  └── 输出 ego 规划轨迹
  ↓
联合 Loss = 规划 Loss + λ × 预测 Loss
```

**支撑证据**：
- [论文: 2022-hivt.md]：HiVT 提供高质量交互场景表征
- [论文: diffTori]：可微优化技术路线已验证可行
- [论文: 2025-plan-mae.md]：预测规划联合预训练

**可论文化**：✅ 高——从预测到规划的完整工作，故事完整

**复杂度**：高（需要实现可微规划器 + 闭环评估）

---

## 第六部分：按推荐排序的行动路线图

### 路线图

```
短期（1-2个月）——快速出成果
  ├── 🔥 [Gap B] 训练期校准 Loss → 在 HiVT 上加一个 Loss 项
  │    └── 产出：一篇短文 / 技术报告 / Workshop
  │
  ├── 🔥 [Gap C] 预测编码辅助任务 → 加辅助头+Loss
  │    └── 产出：一篇短文 / Workshop
  │
  └── 🔥 [Gap D] 闭环评估框架 → 把 HiVT 放进闭环环境
       └── 产出：分析报告 / Benchmark / Workshop

中期（3-6个月）——主力论文
  ├── 🚀 [Gap A] 规划兼容性辅助训练
  │    └── 产出：一篇主会论文（CoRL/ICRA/IROS/ITSC）
  │
  └── 🚀 [Gap E] HiVT 驱动的轻量交互规划器
       └── 产出：一篇主会论文（CVPR/NeurIPS/CoRL）

长期（6-12个月）——系列工作
  └── 🌟 Gap A+B+E 融合：预测→规划 端到端联合框架
       └── 产出：1-2 篇顶会论文 + 可能期刊扩展
```

### 我的推荐

**如果你想要"低风险、稳发文"** → 选 **Gap B**（训练期校准）
- 改动最小（只改 Loss）
- Baseline 明确（对标 CCTR）
- 可以在 HiVT 上直接做

**如果你想要"高创新、讲故事"** → 选 **Gap C**（预测编码）
- 真正的空白
- 生物启发的新颖叙事
- 但风险略高（需验证可行性）

**如果你想走"预测→规划"大方向** → 选 **Gap A → Gap E** 路线
- 这最符合你的长期目标
- 但难度最大，需要更多工程投入

---

## 第七部分：下阶段阅读清单

为了推进上述任何方向，建议优先阅读以下论文：

### 必读（建立基础认知）

| 论文 | 为什么读 | 优先级 |
|------|---------|:------:|
| **"What Truly Matters in Trajectory Prediction"** — NeurIPS 2023 | 理解预测≠规划的根本原因 | ⭐⭐⭐ |
| **CCTR** — AAAI 2024 | Gap B 的直接 Baseline | ⭐⭐⭐ |
| **DiffTORI** — NeurIPS 2024 | 可微规划的技术参考 | ⭐⭐⭐ |
| **UniTraj** — ECCV 2024 | 了解跨域泛化现状 | ⭐⭐⭐ |

### 选读（按方向）

| Gap | 论文 | 
|:---:|------|
| A/E | PiP: Planning-informed prediction; Interactive Joint Planning (NVIDIA) |
| B | Calibration literature (ECE, temperature scaling) |
| C | Predictive coding in neuroscience; Self-supervised learning |
| D | MetaDrive / SMARTS / nuPlan 仿真平台 |

---

## 第八部分：实验资源规划

### 你需要的工具链

```
预测模型: HiVT (已有 ✅)
预测数据集: Argoverse 1.1 (已有 ✅)
闭环仿真: MetaDrive / HighwayEnv / SMARTS（需要搭建）
规划基线: 简单采样式规划器 / MPC
评估指标: 
  - 预测层: minADE, minFDE, MR, ECE（校准）
  - 规划层: 碰撞率, 通行时间, 舒适度(Jerk)
GPU: 你目前跑的 HiVT 是 RTX 2080 Ti 级别即可
```

---

## 附录：关键综述参考文献

1. **A Survey of Trajectory Planning Methods for Autonomous Driving—Part I: Unstructured Scenarios** — Guo et al., 2023
2. **Knowledge Integration Strategies in Autonomous Vehicle Prediction and Planning: A Comprehensive Survey** — arXiv 2502.10477, 2025
3. **Foundation Models for Trajectory Planning in Autonomous Driving: A Review of Progress and Open Challenges** — Oksuz et al., TMLR 2026
4. **A Survey of Autonomous Vehicle Behaviors: Trajectory Planning Algorithms, Sensed Collision Risks** — Xia et al., Sensors 2024
5. **End-to-End Autonomous Driving: Challenges and Frontiers** — TPAMI 2024
