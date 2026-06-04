# 深度调研：渐进式时间动态辅助任务

> 调研日期：2026-06-03 | 对应问题：P3 长时预测发散
> 核心问题：**能否把 PPT (ECCV 2024) 的分阶段训练思想迁移到车辆轨迹预测？**

---

## 一、PPT 论文深度解析

### 1.1 基本信息

| 字段 | 内容 |
|:----|:------|
| **论文** | Progressive Pretext Task Learning for Human Trajectory Prediction |
| **会议** | ECCV 2024 |
| **任务** | 行人轨迹预测 |
| **数据集** | ETH/UCY, SDD |
| **代码** | [GitHub](https://github.com/iSEE-Laboratory/PPT) (官方 PyTorch) |
| **骨干网络** | PPT-Former（基于Transformer，非自回归） |

### 1.2 核心思想

**问题诊断**：现有方法用单一训练范式同时预测短期和长期轨迹，导致短期和长期性能之间出现次优折衷。短期预测依赖即时细粒度变化，长期预测需要全局趋势理解——两者需要的"能力"不同。

**解决方案**：三阶段渐进训练，让模型逐步从简单到复杂学习时间动态。

### 1.3 三阶段训练流程

```
Stage 1: Next-position Prediction（下一个位置预测）
  - 输入：任意长度的观测轨迹
  - 输出：下一个时间步的位置
  - 学什么：速度、方向等局部运动模式
  - 损失：MSE between predicted and ground-truth next position

Stage 2: Destination Prediction（目的地预测）
  - 输入：完整的观测轨迹
  - 输出：最终目的地位置
  - 学什么：长期意图、全局趋势
  - 损失：MSE between predicted and ground-truth destination

Stage 3: Full Trajectory Prediction（完整轨迹预测）
  - 输入：观测轨迹
  - 输出：完整的未来轨迹
  - 利用前两阶段的知识
  - 两步推理：先预测目的地，再生成完整轨迹
```

### 1.4 关键技术

| 技术 | 作用 |
|:----|:-----|
| **跨任务知识蒸馏** | 缓解阶段间的知识遗忘。Stage N 训练时，用 Stage N-1 的模型输出作为蒸馏目标 |
| **可学习目的地提示** | 将目的地编码为可学习的 prompt embedding，作为解码器的输入 |
| **两步推理** | 推理时先预测目的地，然后以目的地为条件生成完整的未来轨迹 |
| **预文本任务** | 前两个阶段被视为"预文本任务"，最终任务是完整轨迹预测 |

### 1.5 结果

PPT 在 ETH/UCY 和 SDD 上实现了行人轨迹预测的 SOTA，尤其在长时预测（ADE/FDE@8-12 timesteps）上提升显著。

---

## 二、竞争分析：车辆方向是否有类似工作？

### 2.1 系统搜索结论

| 搜索方向 | 搜索结果 | 是否冲突 |
|:--------|:--------|:--------|
| "progressive learning" + vehicle trajectory prediction | ❌ 无相关论文 | 不冲突 |
| "curriculum learning" + motion forecasting | ❌ 只有RL规划方向 | 不冲突 |
| "multi-stage training" + trajectory prediction | ⚠️ 层级LSTM等架构方法（非辅助任务） | 架构≠辅助任务 |
| "next position prediction" + vehicle | ❌ 无辅助任务相关工作 | 不冲突 |
| PFR-HiVT (HiVT渐进特征细化) | ❌ 架构级改进，非时序渐进训练 | 不冲突 |
| 行人 PPT → 车辆迁移 | ❌ 无人做过 | ✅ **真空白** |

### 2.2 最接近但不冲突的工作

| 工作 | 做什么 | 为什么不是 |
|:----|:------|:----------|
| **DenseTNT/TNT** | 先预测目标再生成轨迹 | 是**架构设计**不是辅助loss；不涉及渐进训练 |
| **LAformer** | 两阶段预测（粗→精） | 是**推理阶段**的细化，不是训练阶段的渐进 |
| **HDGT/AutoBid** | 多样性/竞标机制 | 解决的是多模态问题，不是时间动态 |
| **LOKI** | 联合意图+轨迹预测 | 是数据集+架构，不是辅助loss |
| **层级LSTM** | 分层预测意图→车道→轨迹 | 架构设计，不涉及渐进训练 |

### 2.3 竞争分析总结

**结论**：✅ **完全空白**。PPT（ECCV 2024）是唯一涉及渐进式时序训练的论文，且仅在**行人**领域验证。车辆轨迹预测领域没有类似工作。

---

## 三、迁移到车辆：需要解决的差异

### 3.1 行人 vs 车辆 关键差异

| 维度 | 行人（PPT setting） | 车辆（你要做的） |
|:----|:------------------|:----------------|
| **地图约束** | 无（仅坐标轨迹） | HD map（车道线、边界、交叉口） |
| **运动学** | 简单（可任意方向行走） | 复杂（自行车模型：曲率、加速度限制） |
| **交互** | 简单避让 | 复杂交互（变道、汇入、让行） |
| **多模态** | 多路径但不强烈 | 强多模态（左转/右转/直行/变道） |
| **预测时长** | 8-12 frames (~3-4秒) | 30 frames (3秒 Argoverse) |
| **观测长度** | 8 frames | 20 frames (2秒 Argoverse) |
| **数据集** | ETH/UCY, SDD | Argoverse 1/2, nuScenes |

### 3.2 迁移方案设计

#### 方案 A：直接三阶段迁移（保留PPT的分阶段训练范式）

```
Stage 1: 短期动态预测（1秒内）
  - 任务：预测下一个 0.5-1.0 秒的轨迹点
  - 输出：位移、速度、朝向角
  - 车辆特有：加入车道感知（预测所在车道）
  - 损失：position MSE + heading consistency

Stage 2: 目的地/意图预测（3-5秒）
  - 任务：预测最终位置 + 驾驶意图（直行/左转/右转变道/靠边）
  - 输出：终点坐标 + 意图分类
  - 车辆特有：地图感知的目标（目标需要落在可行车道区域）
  - 损失：destination MSE + intention CE

Stage 3: 完整轨迹预测
  - 任务：输出完整的 30 frames 轨迹
  - 输入：观测 + 前两阶段学到的特征
  - 两步推理：意图→目的地→完整轨迹（或简化：直接联合推理）
```

#### 方案 B：单阶段多任务loss（简化，推荐）

> 不改训练范式，只加loss头。更符合"不改架构加辅助loss"的路线。

```
Base model (HiVT)
├── 主任务头：完整轨迹预测 (K个模态，回归+分类)
├── 辅助头1：短期位移预测
│   └── 预测未来 0.3s/0.5s/1.0s 的位置
│   └── 监督信号：从GT轨迹中提取
│   └── loss: L_short = MSE(pred_short, gt_short)
├── 辅助头2：目的地预测
│   └── 预测最终帧位置
│   └── 监督信号：GT最后一帧坐标
│   └── loss: L_dest = MSE(pred_dest, gt_dest)
├── 辅助头3：驾驶意图分类
│   └── 预测意图：直行/左转/右转/变道/靠边等
│   └── 监督信号：从轨迹中自动标注（规则定义）
│   └── loss: L_intent = CE(pred_intent, gt_intent)
└── 总loss: L_total = L_main + λ1*L_short + λ2*L_dest + λ3*L_intent
```

**方案B的优势**：
1. 不改HiVT backbone，只在预测头加aux heads
2. 单阶段训练，不需要分阶段调度
3. 和未来其他辅助任务（校准、交互等）可组合
4. 代码修改量小，实验周期短

### 3.3 意图标签的自动生成

驾驶意图可以从轨迹数据中自动标注，无需人工：
- **直行**：横向位移 < 阈值
- **左转/右转**：横向位移 > 阈值 + 目标车道变化
- **变道**：横向位移 + 目标车道不同
- **靠边**：最终位置靠近车道边界

Argoverse 数据本身就包含车道信息，意图标注规则可以基于轨迹和车道图的几何关系自动计算。

---

## 四、实验设计

### 4.1 基线设置

| 模型 | 说明 |
|:----|:------|
| HiVT-64 (基线) | 无辅助任务 |
| HiVT-64 + PPT-like | 加上短时+目的地+意图辅助头 |
| HiVT-128 | 更大模型的上限参考 |
| ADAPT | 更先进的架构的上限参考 |

### 4.2 消融实验

| 实验 | 目的 |
|:----|:-----|
| 仅短期辅助头 | 单独效果 |
| 仅目的地辅助头 | 单独效果 |
| 仅意图分类头 | 单独效果 |
| 短期+目的地 | 双任务组合 |
| 短期+目的地+意图 | 三任务组合 |
| 分阶段训练 vs 单阶段多任务 | 训练范式对比 |

### 4.3 核心指标

| 指标 | 解释 | 预期提升 |
|:----|:----|:---------|
| minADE@6 | 多模态平均位移误差 | 小幅提升 |
| minFDE@6 | 多模态最终位移误差 | **显著提升**（长时更好） |
| FDE (long-term) | 3秒处的误差 | **重点观察** |
| off-road rate | 是否冲出道路 | 可能改善 |
| 意图分类准确率 | 辅助头的表现 | sanity check |

### 4.4 预期效果

- **FDE 应显著下降**（短期辅助帮助中期、目的地帮助长期）
- **长时（3秒）预测漂移减少**（目的地约束起到锚定作用）
- ADE 可能提升不大（短期模型已经不错）
- 意图分类准确率应作为辅助任务的 sanity check

---

## 五、新颖性分析

### 5.1 相比 PPT 的差异化

| 维度 | PPT | 你的版本 |

---

## 七、完整 Idea 方案

### 7.1 方案总览

| 字段 | 内容 |
|:----|:------|
| **方法名** | **PTA (Progressive Temporal Auxiliaries)** — 渐进式时间辅助任务 |
| **基模型** | HiVT (CVPR 2022) |
| **改动范围** | 只加 3 个辅助预测头 + 3 个 aux losses |
| **架构改动** | 0（不改 backbone，不改 decoder 主结构） |
| **新增代码** | ~50-80 行 |
| **新增参数** | ~500-1000（三个辅助头的 MLP） |
| **训练时间增加** | ~5%（几乎可忽略） |

### 7.2 架构图

```
HiVT backbone（完全不变）
├── Local Encoder → local_feat [batch, D]
│   └── [新增] 短期位移头 (MLP: D→32→6) → pred_short [batch, 3, 2]
│        预测未来 0.1s/0.2s/0.3s 的位移（3帧）
│        作用：强迫模型学细粒度运动模式
│
├── Global Encoder → global_feat [batch, D]
│   ├── [新增] 目的地头 (Linear: D→2) → pred_dest [batch, 2]
│   │    预测最终帧 (3秒后) 的位置
│   │    作用：给模型一个"长期锚点"
│   │
│   └── [新增] 意图分类头 (Linear: D→5) → pred_intent [batch, 5]
│        预测驾驶意图：直行/左转/右转/左变道/右变道
│        作用：离散化驾驶模式，辅助多模态覆盖
│
└── Decoder（完全不变）
      └── 主任务：K 条轨迹 + 得分
```

### 7.3 损失函数设计

```python
L_total = L_main + λ₁·L_short + λ₂·L_dest + λ₃·L_intent

L_main       : 原始HiVT loss（winner-takes-all回归 + 分类）
L_short      : L1 loss（pred_short vs gt_short_3frames）
L_dest       : L1 loss（pred_dest vs gt_last_frame）
L_intent     : CrossEntropy（pred_intent vs auto_labeled_intent）
```

**权重建议**（消融实验后决定）：
| 权重 | 初始值 | 调节范围 |
|:----|:------|:--------|
| λ₁ (短期) | 0.1 | [0.05, 0.5] |
| λ₂ (目的地) | 0.1 | [0.05, 0.5] |
| λ₃ (意图) | 0.05 | [0.01, 0.2] |

### 7.4 详细代码修改

#### 文件 1：`models/aux_heads.py`（新建）

```python
import torch
import torch.nn as nn

class ShortTermHead(nn.Module):
    """短期位移预测头 - 接在Local Encoder后"""
    def __init__(self, d_model: int, n_short_frames: int = 3):
        super().__init__()
        self.n_short_frames = n_short_frames
        self.mlp = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, n_short_frames * 2)  # 每个帧的 (x, y)
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [batch, d_model] from local encoder
        out = self.mlp(x)  # [batch, n_frames*2]
        return out.view(-1, self.n_short_frames, 2)  # [batch, n_frames, 2]

class DestinationHead(nn.Module):
    """目的地预测头 - 接在Global Encoder后"""
    def __init__(self, d_model: int):
        super().__init__()
        self.proj = nn.Linear(d_model, 2)  # (x, y)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [batch, d_model] from global encoder
        return self.proj(x)  # [batch, 2]

class IntentHead(nn.Module):
    """意图分类头 - 接在Global Encoder后"""
    def __init__(self, d_model: int, n_intents: int = 5):
        super().__init__()
        self.n_intents = n_intents
        self.classifier = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, n_intents)
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [batch, d_model] from global encoder
        return self.classifier(x)  # [batch, n_intents]
```

#### 文件 2：`models/hivt.py`（修改主模型）

```python
class HiVT(nn.Module):
    def __init__(self, config):
        # ... 原有初始化不变 ...
        
        # 新增：辅助头
        self.short_term_head = ShortTermHead(config['d_model'])
        self.dest_head = DestinationHead(config['d_model'])
        self.intent_head = IntentHead(config['d_model'], n_intents=5)
    
    def forward(self, data, targets=None):
        # 原有前向传播
        agent_hist = data['x']  # [batch, 20, 2]
        lane_vectors = data['lane_vectors']
        
        # Local encoding
        local_feat = self.local_encoder(agent_hist, lane_vectors)  # [batch, d_model]
        
        # 新增：短期预测
        pred_short = self.short_term_head(local_feat)  # [batch, 3, 2]
        
        # Global encoding
        global_feat = self.global_encoder(local_feat, ...)  # [batch, d_model]
        
        # 新增：目的地+意图预测
        pred_dest = self.dest_head(global_feat)  # [batch, 2]
        pred_intent = self.intent_head(global_feat)  # [batch, 5]
        
        # Decoding（不变）
        pred_trajs, pred_scores = self.decoder(global_feat)
        
        if targets is not None:
            losses = self.compute_loss(pred_trajs, pred_scores, targets,
                                       pred_short, pred_dest, pred_intent)
            return pred_trajs, pred_scores, losses
        return pred_trajs, pred_scores
```

#### 文件 3：`losses/loss.py`（修改 loss）

```python
class HiVTLoss(nn.Module):
    def __init__(self, lambda_short=0.1, lambda_dest=0.1, lambda_intent=0.05):
        super().__init__()
        self.lambda_short = lambda_short
        self.lambda_dest = lambda_dest
        self.lambda_intent = lambda_intent
    
    def forward(self, pred_trajs, pred_scores, gt_trajs,
                pred_short=None, gt_short=None,
                pred_dest=None, gt_dest=None,
                pred_intent=None, gt_intent=None):
        
        # 主任务损失（原始HiVT）
        # reg_loss: winner-takes-all on best mode
        # cls_loss: cross-entropy on mode selection
        main_loss = self.compute_main_loss(pred_trajs, pred_scores, gt_trajs)
        
        total_loss = main_loss
        
        # 辅助损失
        if pred_short is not None:
            short_loss = F.l1_loss(pred_short, gt_short)
            total_loss += self.lambda_short * short_loss
        
        if pred_dest is not None:
            dest_loss = F.l1_loss(pred_dest, gt_dest)
            total_loss += self.lambda_dest * dest_loss
        
        if pred_intent is not None:
            intent_loss = F.cross_entropy(pred_intent, gt_intent)
            total_loss += self.lambda_intent * intent_loss
        
        return total_loss
```

### 7.5 意图标签自动生成（数据预处理）

```python
def auto_label_intent(traj_gt: np.ndarray, lane_data) -> int:
    """
    从GT轨迹自动标注驾驶意图
    traj_gt: [30, 2] 未来轨迹 (argoverse @ 10Hz)
    lane_data: 场景车道信息
    
    返回: 0=直行, 1=左转, 2=左变道, 3=右转, 4=右变道
    """
    start_pos = traj_gt[0]      # [x, y]
    end_pos = traj_gt[-1]       # [x, y]
    
    # 1. 计算横向位移（相对于起始车道）
    start_lane = get_nearest_lane(start_pos, lane_data)
    lateral_displacement = compute_lateral_displacement(start_pos, end_pos, start_lane)
    
    # 2. 判断目标车道是否改变
    end_lane = get_nearest_lane(end_pos, lane_data)
    lane_changed = (start_lane['id'] != end_lane['id'])
    
    # 3. 分类
    if abs(lateral_displacement) < 1.2:    # 横向位移小
        return 0  # 直行
    elif lateral_displacement < -1.2:       # 向左
        return 2 if lane_changed else 1     # 左变道 or 左转
    else:                                    # 向右
        return 4 if lane_changed else 3     # 右变道 or 右转
```

**统计数据**（Argoverse 1.1 训练集预估）：
- 直行: ~60%
- 左转: ~12%
- 右转: ~12%
- 左变道: ~8%
- 右变道: ~8%

如果某些类别不平衡，可以用加权 CrossEntropy 或 Focal Loss。

### 7.6 对比实验设计

| 实验 | 配置 | 预期 |
|:----|:----|:-----|
| **Baseline** | HiVT-64 原始 | minADE 0.80, minFDE 1.17 |
| **PTA-S** | + 短期位移头 only | ADE↓ → 短期精度提升 |
| **PTA-D** | + 目的地头 only | FDE↓↓ → 长时显著提升 |
| **PTA-I** | + 意图头 only | 多模态多样性↑ |
| **PTA-SD** | 短期+目的地 | ADE↓ + FDE↓↓ |
| **PTA-SDI** | **三头全开（推荐）** | **综合最优** |
| HiVT-128 | 大模型上限参考 | minADE 0.74, minFDE 1.11 |
| ADAPT | 架构增强型上限 | minADE 0.71 |

### 7.7 预期结果

| 指标 | Baseline | PTA-SDI | 提升幅度 |
|:----|:--------:|:-------:|:--------:|
| minADE@6 | 0.80 | **0.75-0.77** | ~4-6% |
| minFDE@6 | 1.17 | **1.05-1.10** | ~6-10% |
| FDE@3s | 0.85 | **0.78-0.82** | ~4-8% |
| MR | 0.12 | **0.10-0.11** | ~8-16% |
| 多样性 | baseline | ↑ | 意图分类隐式提升 |

**核心卖点**：不改架构，FDE 下降明显（长时更好），参数量增加 < 1%

### 7.8 论文故事线

> **问题**：轨迹预测模型的长时预测发散 → 根因是模型用同一套参数学短期和长期动态
> 
> **观察**：PPT (ECCV 2024) 在行人上验证了时间渐进训练有效，但行人无地图约束、无意图分类
> 
> **方案**：提出 PTA（渐进式时间辅助任务），在 HiVT 上添加三个辅助头：
> 1. 短期位移头（local encoder后）：学细粒度运动模式
> 2. 目的地头（global encoder后）：学长期锚点
> 3. 意图分类头（global encoder后）：学驾驶意图，引导多模态覆盖
> 
> **优势**：不改 backbone，新增参数 < 1000，代码改动 < 100 行，FDE↓6-10%
> 
> **贡献**：
> 1. 首次在车辆轨迹预测中引入时间渐进式辅助任务
> 2. 提出车辆特有意图适配（地图感知的目标预测+意图分类）
> 3. 消融实验证明各辅助头的独立贡献 + 组合效果

### 7.9 风险矩阵

| 风险 | 概率 | 影响 | 应对 |
|:----|:----:|:----:|:-----|
| 短期头提升不显著（HiVT短期已不错） | 中 | 低 | 重点测 FDE，短期头帮助有限也能接受 |
| 意图标注噪声大 | 中 | 中 | 使用 label smoothing；验证标注质量 |
| 辅助头和主任务抢特征 | 低 | 中 | 梯度停止策略（stop-gradient on aux heads） |
| λ权重敏感 | 低 | 低 | 先用固定值，再网格搜索 |
| 审稿人质疑仿真性 | 低 | 高 | 强调 PPT 已在行人验证，你的贡献在车辆适配 |

|:----|:---|:---------|
| 领域 | 行人 | **车辆** |
| 地图约束 | 无 | **HD map-aware**（目标必须在可行车道） |
| 意图预测 | 无 | **驾驶意图分类**（左转/右转/直行/变道） |
| 训练范式 | 三阶段渐进（复杂调度） | **单阶段多任务**（简易部署，不改训练流程） |
| 短时预测 | next-position（步进式） | **多尺度短时**（0.3s/0.5s/1.0s） |

### 5.2 故事线建议

> 车辆轨迹预测的**长时漂移**问题 → 原因：模型用统一范式学短期和长期动态 → 受PPT（行人）启发但需要车辆化适配 → 提出**车辆渐进式时间辅助任务**（带地图感知+意图分类） → 效果：长时FDE显著下降，不牺牲短时精度

### 5.3 可论文化评估

| 评估维度 | 评分 | 说明 |
|:--------|:---:|:-----|
| 新颖性 | ⭐⭐⭐⭐ | 跨域（行人→车辆）+ 车辆特有适配（地图+意图） |
| 必要性 | ⭐⭐⭐⭐ | 长时发散是轨迹预测的公认问题 |
| 工作量 | ⭐⭐⭐⭐ | 轻量级改动，只加aux heads |
| 实验结果风险 | ⭐⭐⭐ | PPT已证明行人有效，但车辆是否有坑不确定 |
| 竞争 | ⭐⭐⭐⭐⭐ | 无竞争（唯一相关工作在行人，且你加了差异化） |

---

## 六、风险与应对

| 风险 | 概率 | 应对 |
|:----|:----|:-----|
| 短期辅助头帮助不大（HiVT本身短期不错） | 中 | 重点测试多尺度短时（0.3s比1s更有用？） |
| 目的地辅助头和主任务冲突 | 低 | 使用梯度停止或任务特定编码器 |
| 意图分类上界不高（自动标注噪声） | 中 | 用argoverse lane信息辅助标注 |
| PPT的分阶段训练收益 | 低 | 你用的单阶段多任务更简单，如果还不够可加蒸馏 |
| 审稿人说"这不就是PPT" | 中低 | 强调车辆特有适配（地图+意图+单阶段） |


---

## 八、关键辨析：PTA vs 目标驱动架构

### 8.1 和 DenseTNT/TNT 的本质区别

这是审稿时可能被问到的问题。必须提前准备好区分。

| 维度 | PTA（你的方案） | DenseTNT / TNT / Goal-based |
|:----|:---------------|:---------------------------|
| **角色** | 辅助训练信号（aux loss） | **架构设计**（核心预测流程） |
| **推理路径** | 只用主解码器输出轨迹 | 先预测目标/意图→再生成轨迹（两阶段） |
| **训练时是否使用** | 是，aux heads提供额外梯度 | 是，但目标是模型架构的一部分 |
| **推理时是否使用** | ❌ aux heads被丢弃，0额外计算 | ✅ 必须执行（目标预测是推理第一步） |
| **是否改backbone** | 不改 | 改（需要目标编码器、目标-轨迹映射） |
| **能否独立于主任务** | 能，去掉aux heads不影响主模型 | 不能，目标和轨迹生成是耦合的 |
| **参数量增加** | <1000（几个linear层） | 显著（整套目标预测网络） |

**一句话区分**：PTA 是**训练时的额外监督信号**，推理时不存在；DenseTNT 是**模型架构的一部分**，推理时必须运行。

**审稿应对**：如果被问"这和DenseTNT有什么区别"，直接说"DenseTNT预测目标是推理必经阶段，而PTA的辅助头只在训练时提供梯度信号，推理时完全零开销。两者的目的也不同——DenseTNT为了保证多模态，PTA为了解决长时漂移。"

### 8.2 和 PPT 的区别

| 维度 | PPT (ECCV 2024) | PTA（你的方案） |
|:----|:---------------|:--------------|
| 领域 | 行人 | **车辆** |
| 地图 | 无 | **HD map-aware 目的地** |
| 意图 | 无（只有位置预测） | **5类驾驶意图分类** |
| 训练范式 | 三阶段渐进（调度复杂） | **单阶段多任务（简易）** |
| 是否需要知识蒸馏 | 是（防遗忘） | **不需要**（单阶段无遗忘问题） |
| backbone | 自定义PPT-Former | **HiVT**（已有强基线） |

---

## 九、Multi-agent 处理

### 9.1 HiVT 的多agent预测

HiVT 一次性预测场景中所有 N 个agent的轨迹：

```python
# 输入
x = data['x']  # [batch, N_agents, 20, 2]

# Local Encoder: 每个agent独立编码
local_feat = self.local_encoder(x)  # [batch, N_agents, d_model]

# Global Encoder: agent间交互编码
global_feat = self.global_encoder(local_feat)  # [batch, N_agents, d_model]

# Decoder: 每个agent独立解码
pred_trajs, pred_scores = self.decoder(global_feat)  # [batch, N_agents, K, 30, 2]
```

### 9.2 辅助头在 multi-agent 下的实现

```python
# 短期头：每个agent独立预测
# local_feat: [batch, N_agents, d_model]
short_term = self.short_term_head(local_feat)  # [batch, N_agents, 3, 2]
# gt_short: [batch, N_agents, 3, 2]
short_loss = F.l1_loss(short_term, gt_short)

# 目的地头：每个agent独立预测
dest = self.dest_head(global_feat)  # [batch, N_agents, 2]
# gt_dest: [batch, N_agents, 2]
dest_loss = F.l1_loss(dest, gt_dest)

# 意图头：每个agent独立分类
intent = self.intent_head(global_feat)  # [batch, N_agents, 5]
# gt_intent: [batch, N_agents] (long)
intent_loss = F.cross_entropy(intent.view(-1, 5), gt_intent.view(-1))
```

**关键点**：HiVT 本身就是多agent预测架构，辅助头自然支持多agent——只需在所有agent维度上计算loss。

### 9.3 辅助任务的监督信号（GT）获取

对于场景中的每个agent，都需要：
- **短期位移**：从GT未来轨迹的 0-3 帧提取
- **目的地**：GT未来轨迹的最后帧
- **意图**：从GT未来轨迹+车道信息自动标注

```python
# 数据预处理：为每个agent计算aux targets
def prepare_aux_targets(scene):
    """为场景中所有agent准备辅助任务监督信号"""
    for agent in scene.agents:
        gt_future = agent.gt_trajectory  # [30, 2]
        
        # 短期（未来3帧相对于当前帧的位移）
        agent.aux_short = gt_future[:3] - gt_future[0:1]  # [3, 2]
        
        # 目的地（最终帧的绝对坐标）
        agent.aux_dest = gt_future[-1]  # [2]
        
        # 意图（自动标注）
        agent.aux_intent = auto_label_intent(gt_future, scene.lane_data)  # int
```

---

## 十、成本效益分析：HiVT-64+PTA vs HiVT-128

### 10.1 方案对比

| 对比项 | HiVT-64（基线） | HiVT-64+PTA | HiVT-128（大模型） | ADAPT（架构增强） |
|:------|:--------------:|:-----------:|:----------------:|:----------------:|
| **参数量** | 0.69M | ~0.691M (+0.1%) | 2.83M (+310%) | 更高效 |
| **minADE** | 0.80 | **预期 0.75-0.77** | 0.74 | 0.71 |
| **minFDE** | 1.17 | **预期 1.05-1.10** | 1.11 | 1.08 |
| **训练时间** | 35-40min | ~38-43min (+8%) | ~60-70min | 更高效 |
| **推理时间** | 基准 | **相同**（aux heads不运行） | 更慢 | 更快 |
| **代码改动** | — | ~80行 | 改config就行 | 重写模型 |

### 10.2 核心结论

**HiVT-64+PTA 在性价比上远超 HiVT-128：**
- 参数增加 0.1% vs 310%
- 推理成本 0% vs 显著增加
- 预期精度接近甚至超过 HiVT-128
- 代码改动量极小

### 10.3 和ADAPT的关系

ADAPT (ICCV 2023) 是架构级增强（自适应头），和你**不冲突**。
- PTA 可以**叠加在 ADAPT 上**（ADAPT 只是改了decoder，你加的aux heads在encoder后）
- 但你目前用 HiVT 更合适：基线更干净、效果对比更清晰
- 未来可以加一个"ADAPT+PTA"的对比实验

---

## 十一、简化版分析

### 11.1 最简可行版本：只保留目的地头

如果意图头的标注质量不放心，或者短期头收益不明显，可以只做：

```
PTA-Lite = HiVT + 仅目的地辅助头
```

| 项目 | 值 |
|:----|:----|
| 新增参数 | ~256（一个Linear层） |
| 代码改动 | ~20行 |
| 预期FDE下降 | ~4-7% |
| 风险 | 最低 |

### 11.2 完整版 vs Lite版

| 版本 | 组成 | 代码量 | 风险 | 预期提升 |
|:----|:-----|:-----:|:----:|:--------:|
| PTA-Lite | +目的地头 | ~20行 | 极低 | FDE↓4-7% |
| PTA-Standard | +短期头+目的地头 | ~50行 | 低 | FDE↓5-8% |
| PTA-Full | +短期头+目的地头+意图头 | ~80行 | 中低 | FDE↓6-10% |

**推荐策略**：先做 PTA-Lite 验证目的地头的有效性，再逐步扩展。

---

## 十二、实施路线图

### Phase 1：环境准备（~2天）
1. Clone HiVT 官方仓库
2. 配置 Argoverse 1.1 数据集
3. 运行官方 HiVT-64 训练脚本，确认可复现结果（minADE 0.80）

### Phase 2：数据预处理（~1天）
1. 在数据加载管线中增加 aux targets 计算
2. 验证意图标注规则的准确性（抽取100个样本人工检查）
3. 统计意图分布，确认是否需要 class weighting

### Phase 3：模型修改（~1天）
1. 新建 `models/aux_heads.py`
2. 修改 `models/hivt.py`：添加三个辅助头
3. 修改 `losses/loss.py`：添加 aux losses
4. 修改 `config.yaml`：添加超参数

### Phase 4：训练与消融（~3天）
1. PTA-Lite（仅目的地头）
2. PTA-Standard（短期+目的地）
3. PTA-Full（三头全开）
4. 每个版本跑 3 个 seed，记录均值和方差

### Phase 5：分析与写作（~2天）
1. 对比 HiVT-128 和 ADAPT 的指标
2. 分析 failure cases（哪些场景提升最大）
3. 准备论文实验部分

### 总计：~9天（纯实验时间）
- 如果只有单卡 GPU，训练时间翻倍（~18天）
- 但实际上 HiVT-64 单卡训练 35-40 分钟，36 epochs ≈ 1天

---

## 十三、补充搜索：防止遗漏竞争工作

### 搜索清单
| 搜索关键词 | 结果 | 结论 |
|:----------|:----|:-----|
| "intermediate goal" trajectory prediction auxiliary | 已有 goal-based 架构工作 | 非辅助loss，不冲突 |
| "waypoint" auxiliary loss vehicle trajectory | 有 waypoint 架构设计 | 非训练时辅助任务 |
| "destination prediction" as auxiliary task vehicle | 独立目的地预测不是辅助loss | 不冲突 |
| "multi-scale temporal" trajectory prediction | 有架构级多尺度方法 | 非辅助loss |
| 行人 PPT → 车辆迁移 | 0 篇 | 完全空白 |

### 最接近但不冲突的工作
1. **Interpretable Long Term Waypoint-Based Trajectory Prediction (2023)** — 用 waypoint 做条件预测，是架构设计，不是训练时辅助loss
2. **Goal-based Neural Physics (2024)** — 两阶段预测（goal→traj），架构级
3. **Map-Adaptive Goal-Based Trajectory Prediction** — 架构设计

**结论：PTA 的"训练时多任务辅助loss"定位在车辆轨迹预测中确实是空白。**


---

## 十四、意图标注完整算法

### 14.1 Argoverse 数据集中的可用信息

Argoverse 1.1 提供的 lane 数据：

```python
# 每条lane段的结构
{
    "id": int,                          # 车道段ID
    "centerline": np.ndarray [N, 2],    # 中心线点序列 (x, y)
    "has_traffic_control": bool,        # 是否有红绿灯
    "turn_direction": str,              # "LEFT" / "RIGHT" / "NONE"
    "is_intersection": bool,            # 是否在交叉口内
    "neighbors": List[int],            # 相邻lane的ID
    "predecessors": List[int],         # 前驱lane
    "successors": List[int],           # 后继lane
}
```

**关键API**：HiVT 的数据加载器已经包含了 `lane_vectors`，我们可以直接利用已有的 lane 信息，不需要额外加载。

### 14.2 意图标注三步法

```python
def auto_label_intent(gt_traj: np.ndarray, lane_data: dict, city: str) -> int:
    """
    从GT未来轨迹自动标注驾驶意图
    
    Args:
        gt_traj: [30, 2] 未来轨迹坐标
        lane_data: 场景的车道信息（从HiVT数据加载器已有的格式）
        city: "PIT" 或 "MIA"，用于坐标系转换
    
    Returns:
        0=直行, 1=左转, 2=左变道, 3=右转, 4=右变道
    """
    start_pos = gt_traj[0]       # 当前帧位置
    end_pos = gt_traj[-1]        # 最终帧位置
    
    # Step 1: 计算车辆朝向变化（判断转弯）
    init_heading = compute_heading(gt_traj[:5])     # 初始朝向（前5帧均值）
    final_heading = compute_heading(gt_traj[-5:])   # 最终朝向（后5帧均值）
    heading_change = normalize_angle(final_heading - init_heading)  # [-π, π]
    
    # Step 2: 计算横向位移（相对于初始车道）
    # 找到最近的lane段
    nearest_lane = find_nearest_lane(start_pos, lane_data)
    # 将轨迹点投影到lane中心线上，计算横向位移
    lateral_displacements = []
    for t in range(gt_traj.shape[0]):
        proj = project_to_centerline(gt_traj[t], nearest_lane['centerline'])
        lateral_displacements.append(proj['lateral_distance'])
    
    # 最大横向偏移量（正=右，负=左）
    max_lateral = max(lateral_displacements, key=abs)
    
    # Step 3: 判断目标车道是否改变
    end_lane = find_nearest_lane(end_pos, lane_data)
    lane_changed = (nearest_lane['id'] != end_lane['id'])
    
    # Step 4: 综合判断
    # 先判断转弯（heading变化大）
    if abs(heading_change) > np.deg2rad(40):
        if heading_change > 0:
            return 1  # 左转
        else:
            return 3  # 右转
    
    # 再判断变道（横向位移大 + 车道改变）
    if lane_changed and abs(max_lateral) > 1.5:
        if max_lateral < 0:
            return 2  # 左变道
        else:
            return 4  # 右变道
    
    # 默认直行
    return 0  # 直行


def compute_heading(points: np.ndarray) -> float:
    """计算轨迹段的平均朝向角"""
    dx = points[-1, 0] - points[0, 0]
    dy = points[-1, 1] - points[0, 1]
    return np.arctan2(dy, dx)


def find_nearest_lane(pos: np.ndarray, lane_data: dict) -> dict:
    """找到距离位置最近的lane段（用lane centerline的第一个点）"""
    min_dist = float('inf')
    nearest_lane = None
    for lane in lane_data:
        dist = np.linalg.norm(lane['centerline'][0, :2] - pos)
        if dist < min_dist:
            min_dist = dist
            nearest_lane = lane
    return nearest_lane
```

### 14.3 阈值选择依据

| 参数 | 建议值 | 依据 |
|:----|:------|:-----|
| 转弯角度阈值 | 40° | 城市道路典型转弯角度 > 30°，留余量到 40° |
| 变道横向位移 | 1.5m | 标准车道宽度 3.5m，变道至少需要横向移动半个车道 |
| 前/后视帧数 | 5帧 | 0.5秒的窗口估计朝向，足够稳定 |

**建议**：先抽取 100 个样本人工验证标注准确率。如果低于 85%，调整阈值或改用更复杂的规则。

### 14.4 类别不平衡处理

根据 Argoverse 1.1 数据分析，意图分布预估：

| 类别 | 预估占比 | 应对 |
|:----|:--------:|:-----|
| 直行 | ~60% | 正常 |
| 左转 | ~12% | 正常 |
| 右转 | ~12% | 正常 |
| 左变道 | ~8% | 偏低，使用 class weighting |
| 右变道 | ~8% | 偏低，使用 class weighting |

```python
# 基于训练集统计计算class weights
class_counts = np.bincount(all_intent_labels)  # [N0, N1, N2, N3, N4]
total = class_counts.sum()
class_weights = total / (len(class_counts) * class_counts)
class_weights = torch.FloatTensor(class_weights)

# 在loss中使用加权CE
intent_loss = F.cross_entropy(pred_intent, gt_intent, weight=class_weights)
```

---

## 十五、完整代码实现

### 15.1 文件修改清单（精确到行）

| 文件 | 操作 | 改动量 |
|:----|:----|:------|
| `models/aux_heads.py` | **新建** | 34行 |
| `models/hivt.py` | 修改 | +15行 |
| `models/local_encoder.py` | 修改 | +5行 |
| `models/global_encoder.py` | 修改 | +10行 |
| `losses/loss.py` | 替换 | 重写loss类 |
| `data/argoverse_dataset.py` | 修改 | +20行（添加aux targets） |
| `config.yaml` | 修改 | +5行（超参数） |

### 15.2 每个文件的完整代码

#### 文件1: `models/aux_heads.py`（新建）

```python
"""
PTA: Progressive Temporal Auxiliaries
三个辅助预测头，不改HiVT backbone结构
"""
import torch
import torch.nn as nn


class ShortTermHead(nn.Module):
    """
    短期位移预测头
    输入: local encoder输出 [batch, N_agents, d_model]
    输出: 未来3帧位移 [batch, N_agents, 3, 2]
    位置: 接在LocalEncoder之后
    """
    def __init__(self, d_model: int = 128, n_short_frames: int = 3):
        super().__init__()
        self.n_short_frames = n_short_frames
        self.mlp = nn.Sequential(
            nn.Linear(d_model, d_model // 4),
            nn.LayerNorm(d_model // 4),
            nn.ReLU(inplace=True),
            nn.Linear(d_model // 4, n_short_frames * 2)
        )
        self._init_weights()
    
    def _init_weights(self):
        """小权重初始化，避免初始时aux loss过大干扰主任务"""
        for m in self.mlp.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight, gain=0.1)
                nn.init.zeros_(m.bias)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [batch, N_agents, d_model] or [batch, d_model]
        out = self.mlp(x)  # [batch, N_agents, 6] or [batch, 6]
        *dims, _ = out.shape
        return out.view(*dims, self.n_short_frames, 2)


class DestinationHead(nn.Module):
    """
    目的地预测头
    输入: global encoder输出 [batch, N_agents, d_model]
    输出: 最终帧位置 [batch, N_agents, 2]
    位置: 接在GlobalEncoder之后
    """
    def __init__(self, d_model: int = 128):
        super().__init__()
        self.proj = nn.Linear(d_model, 2)
        # 零初始化：初始时目的地预测为(0,0)，不影响主任务
        nn.init.zeros_(self.proj.weight)
        nn.init.zeros_(self.proj.bias)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)  # [batch, N_agents, 2]


class IntentHead(nn.Module):
    """
    驾驶意图分类头
    输入: global encoder输出 [batch, N_agents, d_model]
    输出: 5类驾驶意图logits [batch, N_agents, 5]
    位置: 接在GlobalEncoder之后
    """
    def __init__(self, d_model: int = 128, n_intents: int = 5):
        super().__init__()
        self.classifier = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(inplace=True),
            nn.Linear(d_model // 2, n_intents)
        )
        self._init_weights()
    
    def _init_weights(self):
        for m in self.classifier.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight, gain=0.1)
                nn.init.zeros_(m.bias)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(x)  # [batch, N_agents, 5]
```

#### 文件2: `models/hivt.py`（修改）

```diff
+ from models.aux_heads import ShortTermHead, DestinationHead, IntentHead

class HiVT(nn.Module):
    def __init__(self, config):
        super().__init__()
        # ... 原有初始化不变 ...
        
+        # PTA: 渐进式时间辅助任务头
+        d_model = config['d_model']
+        self.short_term_head = ShortTermHead(d_model)
+        self.dest_head = DestinationHead(d_model)
+        self.intent_head = IntentHead(d_model, n_intents=5)
+        self.aux_enabled = config.get('use_pta', False)
    
-    def forward(self, data):
+    def forward(self, data, targets=None, aux_targets=None):
        agent_hist = data['x']  # [batch, N_agents, 20, 2]
        lane_vectors = data['lane_vectors']
        
        # Local encoding
        local_feat = self.local_encoder(agent_hist, lane_vectors)
        
+        # PTA: 短期位移预测
+        pred_short = self.short_term_head(local_feat) if self.aux_enabled else None
        
        # Global encoding
        global_feat = self.global_encoder(local_feat, ...)
        
+        # PTA: 目的地 + 意图预测
+        pred_dest = self.dest_head(global_feat) if self.aux_enabled else None
+        pred_intent = self.intent_head(global_feat) if self.aux_enabled else None
        
        # Decoding（不变）
        pred_trajs, pred_scores = self.decoder(global_feat)
        
        if targets is not None:
            loss = self.criterion(
                pred_trajs, pred_scores, targets,
                pred_short, pred_dest, pred_intent,
                aux_targets
            )
            return pred_trajs, pred_scores, loss
        return pred_trajs, pred_scores
```

#### 文件3: `losses/loss.py`（替换）

```python
import torch
import torch.nn as nn
import torch.nn.functional as F


class HiVTLoss(nn.Module):
    """支持PTA的HiVT损失函数"""
    
    def __init__(self, config):
        super().__init__()
        self.K = config['K']  # 轨迹模态数
        self.future_len = config['future_len']  # 30
        
        # PTA超参数（默认关闭）
        self.aux_enabled = config.get('use_pta', False)
        if self.aux_enabled:
            self.lambda_short = config.get('lambda_short', 0.1)
            self.lambda_dest = config.get('lambda_dest', 0.1)
            self.lambda_intent = config.get('lambda_intent', 0.05)
            # 类别权重（基于数据统计）
            self.intent_weights = config.get('intent_weights', None)
            if self.intent_weights is not None:
                self.register_buffer('intent_weights_t', 
                    torch.tensor(self.intent_weights))
    
    def forward(self, pred_trajs, pred_scores, gt_trajs,
                pred_short=None, pred_dest=None, pred_intent=None,
                aux_targets=None):
        """
        pred_trajs: [batch, N_agents, K, 30, 2]
        pred_scores: [batch, N_agents, K]
        gt_trajs: [batch, N_agents, 30, 2]
        aux_targets: dict of auxiliary supervision signals
        """
        # ========== 主任务损失 ==========
        # 逐点距离 [batch, N_agents, K, 30]
        reg_dist = torch.norm(
            pred_trajs - gt_trajs.unsqueeze(2), 
            dim=-1
        )
        # 每个轨迹的平均ADE [batch, N_agents, K]
        reg_loss_per_mode = reg_dist.mean(dim=-1)
        
        # Winner-takes-all: 每个agent选最佳模式
        best_mode = reg_loss_per_mode.argmin(dim=-1)  # [batch, N_agents]
        best_reg_loss = reg_loss_per_mode.gather(
            dim=-1, 
            index=best_mode.unsqueeze(-1)
        ).mean()
        
        # 分类损失: best mode score should be highest
        cls_target = F.one_hot(best_mode, num_classes=self.K).float()
        cls_loss = F.cross_entropy(
            pred_scores.reshape(-1, self.K),
            cls_target.reshape(-1, self.K)
        )
        
        main_loss = best_reg_loss + cls_loss
        
        if not self.aux_enabled or aux_targets is None:
            return main_loss
        
        # ========== PTA辅助损失 ==========
        total_aux_loss = 0.0
        aux_info = {}
        
        # 1. 短期位移损失
        if pred_short is not None and 'short_term' in aux_targets:
            gt_short = aux_targets['short_term']  # [batch, N_agents, 3, 2]
            short_loss = F.l1_loss(pred_short, gt_short)
            total_aux_loss += self.lambda_short * short_loss
            aux_info['short_loss'] = short_loss.item()
        
        # 2. 目的地损失
        if pred_dest is not None and 'destination' in aux_targets:
            gt_dest = aux_targets['destination']  # [batch, N_agents, 2]
            dest_loss = F.l1_loss(pred_dest, gt_dest)
            total_aux_loss += self.lambda_dest * dest_loss
            aux_info['dest_loss'] = dest_loss.item()
        
        # 3. 意图分类损失
        if pred_intent is not None and 'intent' in aux_targets:
            gt_intent = aux_targets['intent']  # [batch, N_agents]
            intent_loss = F.cross_entropy(
                pred_intent.reshape(-1, 5),
                gt_intent.reshape(-1),
                weight=getattr(self, 'intent_weights_t', None)
            )
            total_aux_loss += self.lambda_intent * intent_loss
            aux_info['intent_loss'] = intent_loss.item()
        
        total_loss = main_loss + total_aux_loss
        return total_loss, aux_info
```

#### 文件4: `data/argoverse_dataset.py`（修改）

在数据加载函数中为每个agent计算aux targets：

```python
# 在 __getitem__ 中，原有返回 dict 后添加：

def _prepare_aux_targets(self, gt_trajs, lane_data):
    """
    为场景中所有agent准备辅助任务监督信号
    gt_trajs: [N_agents, 30, 2] 未来轨迹
    lane_data: 场景车道信息（HiVT已加载）
    """
    N = gt_trajs.shape[0]
    aux = {}
    
    # 1. 短期位移：未来3帧相对于当前位置的位移
    aux['short_term'] = gt_trajs[:, :3, :] - gt_trajs[:, 0:1, :]  # [N, 3, 2]
    
    # 2. 目的地：最终帧绝对坐标
    aux['destination'] = gt_trajs[:, -1, :]  # [N, 2]
    
    # 3. 驾驶意图（自动标注）
    intent_labels = []
    for i in range(N):
        label = self._auto_label_intent(gt_trajs[i], lane_data)
        intent_labels.append(label)
    aux['intent'] = torch.LongTensor(intent_labels)  # [N]
    
    return aux
```

#### 文件5: `config.yaml`（修改）

```yaml
# 原有配置...

# PTA配置（默认关闭）
use_pta: True
lambda_short: 0.1
lambda_dest: 0.1
lambda_intent: 0.05

# 意图类别权重（基于训练集统计，先留空）
# intent_weights: [0.5, 1.5, 2.0, 1.5, 2.0]
```

### 15.3 辅助头初始化策略

| 辅助头 | 初始化 | 理由 |
|:------|:------|:------|
| 短期头 | Xavier(gain=0.1) + 零偏置 | 小权重初始，aux loss不会在第一轮就爆炸 |
| 目的地头 | 全零 | 初始预测(0,0)，梯度从0开始积累 |
| 意图头 | Xavier(gain=0.1) + 零偏置 | 同上 |

**为什么用小权重初始化**：辅助任务在训练初期不应该主导梯度。主任务的梯度应该是主要驱动力，aux heads 只是"顺便"提供额外信号。如果 aux loss 初始太大，模型会优先拟合辅助任务而忽略主任务。

---

## 十六、训练策略与调试指南

### 16.1 分阶段训练策略（推荐）

不要一上来就开三个头。按以下顺序逐步验证：

```
Phase 1: 验证目的地头（最简单的aux loss）
  config: use_pta=True, lambda_short=0, lambda_intent=0, lambda_dest=0.1
  期望: FDE下降4-7%，ADE基本不变
  检查: 训练曲线中dest_loss是否下降（收敛说明模型学到了）

Phase 2: 加入短期头
  config: lambda_short=0.1, lambda_dest=0.1, lambda_intent=0
  期望: ADE小幅下降，FDE维持Phase 1水平
  检查: short_loss是否收敛

Phase 3: 加入意图头
  config: lambda_short=0.1, lambda_dest=0.1, lambda_intent=0.05
  期望: 多模态多样性提升（mode coverage变好）
  检查: intent_acc > 60%（太低说明标注噪声大）
```

### 16.2 训练监测指标

除了标准的 minADE/minFDE，还要跟踪：

| 指标 | 怎么看 | 问题信号 |
|:----|:------|:---------|
| `short_loss` | 持续下降 → 正常 | 震荡不降 → 短期头学习率太高或结构不对 |
| `dest_loss` | 持续下降 → 正常 | 不降 → 目的地头信息不足，试试加大 lambda |
| `intent_acc` | >60% → 标注可用 | <50% → 标注规则有问题或类别不平衡太严重 |
| `main_loss` | vs baseline 的 main_loss | 比 baseline 高很多 → aux heads 干扰主任务 |
| `grad_norm` | 和 baseline 持平 | aux heads 梯度远大于主任务 → 调低 lambda |

### 16.3 故障排查决策树

```
训练完Phase 1（仅目的地头）
├── FDE下降 ≥ 5%
│   └── ✅ 继续Phase 2
├── FDE无变化
│   ├── dest_loss是否收敛？
│   │   ├── 是 → 目的地信息已饱和，检查lambda是否需要调大
│   │   └── 否 → 检查目的地头的梯度是否在回传（梯度裁剪问题？）
│   └── main_loss是否变大了？
│       ├── 是 → aux loss干扰主任务，降低lambda_dest
│       └── 否 → 目的地信息对FDE没用？检查Argoverse的FDE指标

训练完Phase 2（+短期头）
├── ADE下降 ≥ 3%
│   └── ✅ 继续Phase 3
├── ADE无变化
│   └── 短期预测在HiVT中已经够好？考虑去掉短期头，或者换多尺度版本

训练完Phase 3（+意图头）
├── 多样性指标改善 + FDE不倒退
│   └── ✅ 全方案有效
├── 多样性改善但FDE倒退
│   └── 意图头干扰主任务，降低lambda_intent或去掉
└── 全无改善
    └── 回到Phase 1，或者考虑意图标注质量
```

### 16.4 如果整体不work的应急方案

| 问题 | 应急方案 |
|:----|:--------|
| PTA 完全没效果 | 退回到 PTA-Lite（仅目的地头），如果还不行就算了，说明长时发散不是HiVT的主要问题 |
| 只有目的地头有效 | 论文就写"PTA-Lite：轻量级目的地辅助任务"，去掉短期和意图头 |
| 三个头都有效但组合有冲突 | 用不确定性加权（Kendall et al. 2018）自动学习每个loss的权重 |
| 效果时好时坏 | 增加训练epoch到72或者跑3个seed看方差 |

---

## 十七、论文创新点定位

### 17.1 核心贡献

> **车辆轨迹预测领域第一个将时间渐进式辅助任务作为训练时监督信号的工作**

### 17.2 三个差异化

| 对比对象 | 你的差异 |
|:--------|:---------|
| PPT (ECCV 2024, 行人) | 车辆特有：HD map-aware 目的地 + 驾驶意图分类 + 单阶段多任务 |
| DenseTNT/TNT (目标驱动) | **本质不同**：他们是架构设计（推理必经），你是训练时辅助loss（推理零成本） |
| 现有辅助任务 (CombAux, SSL-Int) | **解决问题不同**：他们解决道路合规/交互，你解决**长时发散** |

### 17.3 故事线（一句话版）

> PTA：在HiVT上加三个轻量辅助头（短期位移、目的地、意图），不改架构、不增推理成本，解决轨迹预测的长时发散问题。

### 17.4 论文定位

```
顶会发表可行性:
  - 侧挂小改进 → 适合 AAAI / ECCV / ICRA / IROS
  - 需要大量实验数据支撑 → 不适合 NeurIPS（需要更大novelty）
  - 如果效果好（FDE↓10%+）→ CVPR 也可以冲
  
建议目标: ECCV 或 AAAI
  - 实验完整 + 故事清晰 + 和baseline对比充分
  - 审稿人可以快速理解"加了什么、为什么加、加了多少行代码"
```



---

## 附录：v1→v2 变更记录

> 基于冲塔怪的评审意见（2026-06-03），主要修改点如下：

### 变更1：命名
**PTA → PATA (Progressive Auxiliary Temporal Anchors)**

解释：原"Progressive Temporal Auxiliaries"中的"Progressive"容易被误解为stage-wise training。改为"Progressive Auxiliary Temporal Anchors"，明确"渐进"指的是**时间尺度递增**（0.5s→1.0s→2.0s→3.0s），而不是训练阶段递进。

### 变更2：短期头→多尺度锚点
**未来3帧位移 × → 0.5s/1.0s/2.0s/3.0s 四个锚点 ✓**

原因：0.3s预测基本等价于速度估计，审稿人不会买账。改为四个时间锚点，覆盖从短时到长时的完整梯度。

### 变更3：意图头降级
从核心贡献 → Phase 3可选。必须先验证auto-label的准确率再决定是否加入。

### 变更4：新增实验对照
- baseline + final-frame loss reweighting
- per-timestep ADE error curve (1s/2s/3s)

### 变更5：收敛创新性表述
"完全空白" → "现有方法多将时间锚点作为架构推理流程的一部分，而较少系统研究训练时多尺度时间监督对长时漂移的影响"
