# HiVT 代码架构详解

> 基于论文 + 官方代码结构（PyTorch Lightning）推导
> 目的：为添加辅助任务头提供精确的代码层面修改方案

---

## 一、整体文件结构

```
HiVT/
├── data/              # 数据加载、预处理
│   ├── argoverse_dataset.py
│   ├── vectorize.py   # 地图+轨迹向量化
│   └── utils.py
├── models/
│   ├── hivt.py        # 主模型：HiVT
│   ├── local_encoder.py
│   ├── global_encoder.py
│   └── decoder.py
├── losses/
│   └── loss.py        # 损失函数
├── utils/
│   └── utils.py
├── train.py           # PyTorch Lightning 训练
└── config.yaml        # 配置
```

---

## 二、数据流（逐层分析）

### 2.1 输入格式

```python
# 每个场景（scene）的输入
{
    "x": tensor [N_agents, 20, 2],          # 历史轨迹 (2秒, 20帧)
    "x_positions": [N_agents, 20, 2],       # 原始坐标
    "x_velocity": [N_agents, 20, 2],        # 速度
    "lane_positions": [N_lanes, 10, 2],     # 车道线点
    "lane_vectors": [N_lanes, 9, 4],        # 车道向量
    "agent_id": [N_agents],                 # agent ID
    "city": str,                             # 城市
}
```

关键：**N_agents** 是目标 agent 数量，**N_lanes** 是附近车道段数量。
轨迹是 20 帧历史（2秒 @ 10Hz）→ 预测 30 帧未来（3秒 @ 10Hz）

### 2.2 Local Encoder（局部编码器）

```
输入: agent_i 的历史轨迹 + 附近 N_i 条车道段
                  ↓
Agent Feature: [20, 2] → MLP → [20, D] → temporal pos encoding → self-attn → [1, D_local]

Map Feature: 每条车道段 [10, 2] → MLP → [9, D] → self-attn → [1, D_local] × N_lanes
                  ↓
Cross-attention: agent_feat(query) × lane_feats(key, value)
                  ↓
输出: [1, D_local]  ← 每个agent的局部场景编码
```

**代码层面的关键类**：

```python
class LocalEncoder(nn.Module):
    def __init__(self, d_model=128, n_head=8):
        # 1. 轨迹编码器
        self.agent_mlp = MLP(2, d_model, [64, 128])  # dim_in, dim_out, hidden
        self.temporal_pos_encoder = PositionalEncoding(d_model)
        self.agent_self_attn = SelfAttention(d_model, n_head)
        # 2. 地图编码器  
        self.lane_mlp = MLP(2, d_model, [64, 128])
        self.lane_self_attn = SelfAttention(d_model, n_head)
        # 3. 局部交叉注意力
        self.cross_attn = CrossAttention(d_model, n_head)
    
    def forward(self, agent_hist, lane_vectors):
        # agent_hist: [batch, 20, 2]
        # lane_vectors: [batch, N_lanes, 10, 2]
        agent_feat = self.agent_mlp(agent_hist)  # [batch, 20, d_model]
        agent_feat = self.temporal_pos_encoder(agent_feat)
        agent_feat = self.agent_self_attn(agent_feat).mean(dim=1)  # [batch, d_model]
        
        lane_feat = self.lane_mlp(lane_vectors)  # [batch, N_lanes, 10, d_model]
        lane_feat = self.lane_self_attn(lane_feat).mean(dim=2)  # [batch, N_lanes, d_model]
        
        local_feat = self.cross_attn(agent_feat.unsqueeze(1), lane_feat)  # [batch, 1, d_model]
        return local_feat.squeeze(1)  # [batch, d_model]
```

### 2.3 Global Encoder（全局编码器）

```
输入: 场景中所有 N 个agent的 local features [N, D_local]
                  ↓
Agent Self-Attention: agent_i ↔ agent_j (建模社交交互)
     × N layers
Agent-Map Cross-Attention: agent ↔ global map context
                  ↓
输出: [N, D_global]  ← 带有全局交互上下文的agent编码
```

```python
class GlobalEncoder(nn.Module):
    def __init__(self, d_model=128, n_head=8, n_layers=3):
        self.agent_self_attn_layers = nn.ModuleList([
            SelfAttention(d_model, n_head) for _ in range(n_layers)
        ])
        self.cross_attn_layers = nn.ModuleList([
            CrossAttention(d_model, n_head) for _ in range(n_layers)
        ])
    
    def forward(self, local_feats, global_lane_feats):
        # local_feats: [batch, N_agents, d_model]
        x = local_feats
        for self_attn, cross_attn in zip(self.agent_self_attn_layers, self.cross_attn_layers):
            x = self_attn(x)        # agent ↔ agent
            x = cross_attn(x, global_lane_feats)  # agent ↔ map
        return x  # [batch, N_agents, d_model]
```

### 2.4 Decoder（解码器）

**HiVT 的解码器是核心——这也是加辅助头的地方**

```
输入: global_encoded_feat [batch, D_global]  (每个agent)
                  ↓
┌─ Mode Prediction ──────────────────┐
│  MLP → [K]  (分类头：每个模式的概率)   │
│  softmax → categorical distribution  │
└────────────────────────────────────┘
                  ↓
┌─ Trajectory Regression ────────────┐
│  MLP → [K, 30, 2]  (回归头：K条轨迹×30帧×xy) │
└────────────────────────────────────┘
                  ↓
输出: pred_trajs [batch, K, 30, 2] + pred_scores [batch, K]
```

```python
class Decoder(nn.Module):
    def __init__(self, d_model=128, K=6, future_len=30):
        # 分类头：预测每个模式的概率
        self.cls_head = nn.Sequential(
            nn.Linear(d_model, 64),
            nn.ReLU(),
            nn.Linear(64, K)
        )
        # 回归头：预测每个模式的具体轨迹
        self.reg_head = nn.Sequential(
            nn.Linear(d_model, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, K * future_len * 2)  # 展平输出
        )
    
    def forward(self, global_feat):
        # global_feat: [batch, d_model]
        scores = self.cls_head(global_feat)     # [batch, K]
        trajs = self.reg_head(global_feat)      # [batch, K*30*2]
        trajs = trajs.view(-1, K, 30, 2)        # [batch, K, 30, 2]
        return trajs, scores
```

### 2.5 Loss 函数

```python
class HiVTLoss(nn.Module):
    def forward(self, pred_trajs, pred_scores, gt_trajs):
        # 1. 回归损失：每个模式和GT的L2距离
        #    [batch, K, 30] 距离矩阵
        reg_loss = torch.norm(pred_trajs - gt_trajs.unsqueeze(1), dim=-1)  # [batch, K, 30]
        reg_loss = reg_loss.mean(dim=-1)  # [batch, K]
        
        # 2. 最佳模式选择（Winner-takes-all）
        best_mode = reg_loss.argmin(dim=1)  # 每个样本的最佳模式
        
        # 3. 回归损失：只有最佳模式回传
        best_reg_loss = reg_loss[torch.arange(batch), best_mode].mean()
        
        # 4. 分类损失：最佳模式得分应最高
        cls_target = F.one_hot(best_mode, K).float()
        cls_loss = F.cross_entropy(pred_scores, cls_target)
        
        # 5. 总损失
        total_loss = best_reg_loss + cls_loss
        return total_loss
```

---

## 三、辅助任务头的插入点

HiVT 的架构提供了多个可能的插入点：

### 插入点 A：接在 Local Encoder 之后（推荐用于短期辅助）

```
Local Encoder → local_feat [batch, D]
                     ↓
              [短期位移头] → pred_short_term [batch, 1/2/3, 2]
              (轻量MLP，几行代码)
```

### 插入点 B：接在 Global Encoder 之后（推荐用于目的地/意图辅助）

```
Global Encoder → global_feat [batch, D]
                      ↓
               [目的地头] → pred_dest [batch, 2]
               [意图头] → pred_intent [batch, C_intent]
```

### 插入点 C：在 Decoder 内部共享特征

```
Decoder 的 MLP hidden layer (128维)
          ↓
  分出多个预测分支
```

**推荐方案**：**插入点 A + B**，在 Local Encoder 后加短期头，在 Global Encoder 后加目的地和意图头。这样各辅助头有各自适合的特征层级。

---

## 四、具体修改方案

### 修改文件清单

| 文件 | 修改内容 | 难度 |
|:----|:--------|:----|
| `models/local_encoder.py` | 加短期位移预测头 | ⭐ |
| `models/global_encoder.py` | 加目的地+意图预测头 | ⭐ |
| `losses/loss.py` | 加 aux losses + 权重融合 | ⭐⭐ |
| `config.yaml` | 加 aux loss 超参数 | ⭐ |
| 或新建 `models/aux_heads.py` | 统一管理所有辅助头 | ⭐ |

### 最小改动代码示例

```python
# 在 local_encoder.py 中
class LocalEncoder(nn.Module):
    def __init__(self, ...):
        # ... 原有代码不变
        # 新增：短期位移预测头
        self.short_term_head = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 2 * 3)  # 预测未来3帧 (0.3s)
        )
    
    def forward(self, ...):
        local_feat = ...  # 原有逻辑
        short_term = self.short_term_head(local_feat)  # [batch, 6] → reshape [batch, 3, 2]
        return local_feat, short_term

# 在 global_encoder.py 中
class GlobalEncoder(nn.Module):
    def __init__(self, ...):
        # ... 原有代码不变
        self.dest_head = nn.Linear(d_model, 2)     # 目的地 (x, y)
        self.intent_head = nn.Linear(d_model, 5)   # 5类意图
    
    def forward(self, ...):
        global_feat = ...  # 原有逻辑
        dest = self.dest_head(global_feat)    # [batch, 2]
        intent = self.intent_head(global_feat)  # [batch, 5]
        return global_feat, dest, intent

# 在 loss.py 中
class HiVTLossWithAux(nn.Module):
    def __init__(self, lambda_short=0.1, lambda_dest=0.1, lambda_intent=0.05):
        self.lambda_short = lambda_short
        self.lambda_dest = lambda_dest
        self.lambda_intent = lambda_intent
    
    def forward(self, pred_trajs, pred_scores, gt_trajs,
                pred_short, gt_short, pred_dest, gt_dest, pred_intent, gt_intent):
        # 主损失：和原来一样
        total_loss = self.compute_main_loss(pred_trajs, pred_scores, gt_trajs)
        
        # 辅助损失
        short_loss = F.l1_loss(pred_short, gt_short)
        dest_loss = F.l1_loss(pred_dest, gt_dest)
        intent_loss = F.cross_entropy(pred_intent, gt_intent)
        
        total_loss += (self.lambda_short * short_loss +
                       self.lambda_dest * dest_loss +
                       self.lambda_intent * intent_loss)
        return total_loss
```

---

## 五、关键设计决策

| 决策点 | 方案 | 理由 |
|:------|:-----|:------|
| 短期头输入 | local encoder 输出 | 短期动态主要依赖局部上下文 |
| 目的地头输入 | global encoder 输出 | 目的地需要全局交互+场景理解 |
| 意图头输入 | global encoder 输出 | 驾驶意图同目的地，需要全局信息 |
| 短期头输出帧数 | 3帧（0.3s） | 覆盖HiVT最关心的超短期 |
| 回归损失类型 | L1 Loss | 比L2对异常值更鲁棒 |
| 主loss和aux loss权重 | 0.1/0.1/0.05 | 先固定，后续调参 |

---

## 六、训练细节

- Batch Size: 32（和 HiVT 相同）
- 优化器: AdamW (lr=1e-3)
- Scheduler: CosineAnnealing
- Epochs: 36（和 HiVT 相同）
- 辅助任务权重可尝试：固定的 → 动态加权（参考 CombAux 的自适应方案）

### 意图标签自动生成伪代码

```python
def generate_intent_label(traj, lane_info):
    """
    从GT轨迹自动生成驾驶意图标签
    traj: [30, 2] 未来轨迹
    lane_info: 场景车道信息
    
    返回: intent_id (0-4)
    """
    lateral_displacement = traj[-1, 0] - traj[0, 0]  # 横向位移
    lane_center = get_nearest_lane_center(traj[-1], lane_info)
    
    if abs(lateral_displacement) < 1.0:     # 横向位移小
        return 0  # 直行
    elif lateral_displacement < -2.0:        # 向左大幅偏移
        # 检查目标车道
        if is_different_lane(traj[-1], traj[0], lane_info):
            return 2  # 左变道
        else:
            return 1  # 左转
    elif lateral_displacement > 2.0:         # 向右大幅偏移
        if is_different_lane(traj[-1], traj[0], lane_info):
            return 4  # 右变道
        else:
            return 3  # 右转
```
