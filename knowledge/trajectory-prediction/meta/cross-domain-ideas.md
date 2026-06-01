# 跨界假说验证日志

> 记录"从其他领域生成假说 → 文献验证 → 判断空白/已做/差异"的完整过程。
> 每个条目记录：假说来源、搜索过程、验证结果、修正后的认知。

---

## 验证记录

### 2026-06-01：第一轮跨界生成

#### 假说1：预测编码（Predictive Coding）辅助任务

| 项目 | 内容 |
|---|---|
| **来源领域** | 认知神经科学 |
| **核心假说** | 大脑通过不断预测感官输入、回传预测误差来学习。类比：轨迹预测模型不仅预测agent的未来，还应预测"预测的可靠程度"——把预测误差本身作为训练信号 |
| **具体形式** | 辅助头预测"预测轨迹和真值的误差分布"；当预测误差大时，模型应该"知道自己错了" |
| **搜索关键词** | "predictive coding" OR "prediction error" trajectory prediction; "prediction discrepancy" training signal trajectory prediction |
| **验证结果** | ✅ **未找到相关工作**。搜索返回0篇相关论文。一个关于脑科学中多巴胺预测误差的Nature论文也验证了"预测误差本身作为学习信号"在生物大脑中是有效的 |
| **结论** | **真空白**。可以在此基础上做一篇论文 |
| **关键词** | #元认知 #误差预测 #自监督 #生物启发 |

#### 假说2：规划器反向传播（Differentiable Planning Loss）

| 项目 | 内容 |
|---|---|
| **来源领域** | 强化学习、最优控制 |
| **核心假说** | 预测模型的损失应该包含"下游规划器对这个预测满不满意"。预测轨迹→规划器→规划轨迹质量→反向传播 |
| **搜索关键词** | "differentiable planning" backpropagate trajectory prediction; DiffTORI |
| **验证结果** | ⚡ **部分存在但框架不同**。DiffTORI (NeurIPS 2024) 用可微轨迹优化作为模仿学习的策略表征，不是作为预测模型的辅助loss。关键在于：它的优化方向是"用可微优化做规划"，不是"用规划结果训练预测" |
| **结论** | **差异化空白**。如果用"规划器的输出质量作为预测模型的loss"，这个具体方向没人做 |
| **关键词** | #规划反馈 #可微优化 #预测-规划闭环 |

#### 假说3：场景级OOD感知（Scene-level OOD Awareness）

| 项目 | 内容 |
|---|---|
| **来源领域** | 可靠性工程、异常检测 |
| **核心假说** | 模型在未见过的场景（新路口类型、天气、交通密度）下应该知道"我没把握"。加一个场景编码器，训练时区分ID/OOD |
| **搜索关键词** | "out-of-distribution" trajectory prediction detection; scene-level domain shift |
| **验证结果** | ⚠️ **存在但都是后处理**。"Forecasting the Past" (arxiv 2026) 和 "Latent Dynamics-Aware OOD Monitoring" (arxiv 2026) 都做OOD检测，但都是**事后**单独训练的检测器，不是**训练时和预测模型联合优化**的辅助任务 |
| **结论** | **差异化空白**。如果要做"训练时OOD感知作为辅助loss"，没人做过 |
| **关键词** | #分布偏移 #场景理解 #可靠性 |

#### 假说4：可微社会力损失（Differentiable Social Force Loss）

| 项目 | 内容 |
|---|---|
| **来源领域** | 物理学（社会力模型） |
| **核心假说** | 社会力模型能描述agent之间的吸引力/排斥力。把它写成可微函数，作为预测轨迹的辅助loss——类似于TPK对运动学做的事，对交互做同样的事 |
| **搜索关键词** | "social force model" loss trajectory prediction; ForceFormer |
| **验证结果** | ⚡ **部分存在但框架不同**。ForceFormer (2023) 把社会力集成到Transformer架构中，但作为架构设计（指导注意力），不是可微loss项。SoFGAN 用社会力+GAN 但也是在架构层面 |
| **结论** | **差异化空白**。把社会力写成可微loss（类似TPK的自行车模型loss），这个具体方向没人做 |
| **关键词** | #物理先验 #社会交互 #可微约束 |

#### 假说5：时间层次分解（Temporal Hierarchy）

| 项目 | 内容 |
|---|---|
| **来源领域** | 控制理论 |
| **核心假说** | 驾驶同时有秒级意图（变道/直行）和毫秒级执行（方向盘微调）。模型应同时预测两个层次，层次间一致性作为loss |
| **搜索关键词** | "hierarchical" temporal trajectory prediction; intention prediction multi-scale |
| **验证结果** | ⚠️ **有相关工作但不同**。HITP (ITSC 2025) 做层次化意图预测，但它是架构设计（分层解码），不是作为辅助loss项 |
| **结论** | 如果要做"层次一致性辅助loss"，有差异化空间但需要更仔细验证 |
| **关键词** | #时间层次 #意图分解 |

#### 假说6：反事实推理（Counterfactual Reasoning）

| 项目 | 内容 |
|---|---|
| **来源领域** | 因果推断 |
| **核心假说** | "如果ego做动作X，周围agent会怎么反应？"这种反事实推理能力可以作为辅助任务 |
| **搜索关键词** | "counterfactual" trajectory prediction; causal intervention trajectory |
| **验证结果** | ❌ **已被做过**。CausalHTP (ICCV 2021) 用反事实分析做去偏；CICR (MDPI 2025) 做多模态预测的反事实推理。虽然有差异（CausalHTP是做去偏而非ego-反事实），但"反事实"这个方向在轨迹预测中已经有多篇论文 |
| **结论** | **已被占据**。不推荐从反事实角度切入 |
| **关键词** | #因果推断 #反事实 #已有工作 |
