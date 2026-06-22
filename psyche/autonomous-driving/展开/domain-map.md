# 自动驾驶领域地图

> 基于 TPAMI 2024 端到端综述(270+论文) + 2026.3 推理综述 + 轨迹预测领域积累(15+篇)
> 构建日期: 2026-06-21 | 状态: 初版，持续进化

---

## 一、领域拓扑

```
自动驾驶系统
│
├── [上游] 感知 (Perception)
│   ├── 3D目标检测 (Detection)
│   ├── 多目标跟踪 (Tracking)
│   ├── 地图构建/车道线 (Mapping)
│   └── 占用网络 (Occupancy) ← 近年兴起
│
├── [中游] 预测 (Prediction)
│   ├── 单智能体轨迹预测
│   ├── 多智能体交互预测
│   ├── 意图/行为预测
│   └── 场景级预测
│
├── [下游] 规划 (Planning)
│   ├── 行为规划 (Behavior Planning)
│   ├── 运动规划 (Motion Planning)
│   └── 安全/风险评估
│
├── [执行] 控制 (Control)
│   └── 横纵向控制
│
├── [支撑] 仿真与数据
│   ├── 交通仿真 (Traffic Simulation)
│   ├── 传感器仿真 (Sensor Simulation)
│   └── 闭环评估 (Closed-loop Eval)
│
└── [跨越/集成] 系统架构
    ├── 模块化 (Modular) — 经典pipeline
    ├── 模块克隆 (Module-cloned E2E) — 可解释中间表示
    ├── 可解释端到端 (Interpretable E2E) — 隐式中间表示
    └── 直接端到端 (Direct E2E) — sensor → action
```

---

## 二、技术演进时间线

```
2016-2018: 深度学习渗透
  ├── LSTM/CNN替代规则方法做感知
  └── 第一次端到端浪潮 (CoRL '16, Nvidia DAVE-2)

2019-2021: 模块化成熟 + E2E复苏
  ├── Perception: CenterPoint (3D检测), BEVFormer (BEV感知)
  ├── Prediction: HiVT (CVPR'22 Vector Transformer), LaneGCN
  ├── Planning: 规则为主，学习型规划器初现
  └── E2E: Neo (CVPR'21), Transfuser (ICCV'21)

2022-2023: E2E爆发
  ├── UniAD (CVPR'23): 首个E2E统一框架
  ├── VAD (ICCV'23): Vectorized AD
  ├── Prediction: QCNet (CVPR'23, Argoverse 1st), DenseTNT
  ├── 关键转折: nuPlan/CARLA闭环benchmark使E2E可评估
  └── 共识: E2E在闭环中优于模块化，但开环指标优势不显著

2024-2025: 基础模型渗透
  ├── LLM/VLM: DriveGPT, LMDrive, LLM-Driver — 但延迟和幻觉问题突出
  ├── 世界模型: GAIA-1/2, DriveDreamer-1/2, WorldLens
  ├── 扩散模型: Diffusion for planning/prediction
  ├── 评估觉醒: Super Agents, Mode Collapse, What Truly Matters
  └── 问题演化: 从"能不能更准"到"部署时到底可不可靠"

2026: 后训练时代（当前）
  ├── 推理成为核心瓶颈: LLM vs 实时控制的根本矛盾
  ├── 闭环节训练: NVIDIA综述确认开环→闭环分布偏移
  ├── 安全性/UQ: 保形预测、Laplace近似、不确定性感知规划
  ├── VLA模型: 视觉-语言-行动统一模型（但资源需求极大）
  └── DriveX 2026: Cooperative + Foundation Models
```

---

## 三、上下游依赖关系

```
传感器 (Camera/LiDAR/Radar)
    ↓
[检测] 位置/类别/朝向/速度
    ↓
[跟踪] 时序关联 + ID维护
    ↓
[地图] 车道拓扑 + 道路边界
    ↓
[预测] 未来轨迹 (多模态: K=1/6)
    ↓
[规划] 自车轨迹 + 行为决策
    ↓
[控制] 油门/刹车/转向

链条中的误差传播:
  检测误差(位置偏10cm) → 预测偏多少？→ 规划偏多少？
  → 这个级联关系至今没有被系统量化 ← 重要gap
```

---

## 四、核心张力（非共识/争议）

| 张力 | 一方 | 另一方 | 我的判断 |
|------|------|--------|---------|
| 模块化 vs E2E | 可解释、易调试、可独立改进 | 联合优化、无信息损失 | 取决于场景——研究趋势在E2E，但部署中模块化仍是主流 |
| 开环 vs 闭环 | 开环评估快、可复现 | 闭环评估更真实、但耦合因果混淆 | 开环指标已饱和，闭环是检验标准 |
| LLM推理 vs 实时控制 | LLM理解力强、可处理长尾 | 延迟高、幻觉不可控 | 目前不适合做实时控制，可做高层决策辅助 |
| 大模型 vs 单人研究 | 能力上限高 | 资源需求极大 | 分析型工作是单人最优切入点 |

---

## 五、单人可做 vs 做不了的

**能做（✅）**：
- 分析/诊断/审计型工作（跨模型对比、校准、鲁棒性）
- 已有模型的test-time评估（不需要训）
- 轻量方法改进（改loss、加后处理）
- 特定场景的深入分析（长尾、分布偏移）

**难做（⚠️）**：
- 新架构设计 + 从头训练（至少4-8卡）
- 扩散模型/世界模型训练（>8卡）
- 闭环训练（需要仿真环境 + 多卡）

**做不了（❌）**：
- 大模型训练/微调（VLM/VLA）
- 大规模数据引擎
- 实车部署
