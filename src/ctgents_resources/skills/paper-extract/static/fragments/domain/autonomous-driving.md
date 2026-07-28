# AD 领域：特殊判断准则

> 加载: domain=autonomous-driving | 关联: autonomous-driving Psyche 的五筛子

## 边界检查（D12 用户决策前置）

论文是否在用户边界内？

- ✅ latent/occupancy 世界模型（BEV latent / occupancy prediction / WM for planning）
- ✅ 轨迹规划相关（planning / evaluation / scoring）——如果和 WM 有关联
- ❌ VLA / VLM（除非仅作 backbone）
- ❌ 视频生成世界模型（DriveDreamer / STAGE / Vista 类）
- ❌ 3D Gaussian / Gaussian Splatting
- ❌ 仿真（simulation）

灰区：occupancy prediction 且不生成像素的 → 边界内。扩散做轨迹规划 → 边界内。

## 算力约束检查

- 训练需要什么卡？4×4090 可以，8×A100 不行
- 如果只做 test-time 评测 + 可控实验 → A40 够不够？
- 如果做对比研究（用已发布模型做 evaluation）→ 不需训练卡

## Benchmark 权重

NAVSIM-only 论文的结论要打折——没有 nuScenes 开环验证，泛化性存疑。
nuScenes-only 同理——没有闭环验证，"安全"结论不可靠。
最好的组合：nuScenes + NAVSIM + Bench2Drive 中至少两个。

## NAVSIM v2 特别提醒

NAVSIM v2 已经发布（2026 年中），和 v1 差异显著：
- v2 使用 PDM Score v2（不同子指标权重）
- v2 路线更长、场景更复杂
- 如果论文用 v1 → 标注"未在 v2 验证"，结果可能不迁移

## 领域坐标参考

当前 latent WM 领域的已知坐标点（用于 D10 定位）：
- 显式生成侧：OccWorld / Drive-WM / WoTE
- 潜在建模侧：GraphWorld / Latent-WAM / World4Drive / ResWorld
- 规划导向侧：VAD / DiffusionDrive / Hydra-MDP / DrivoR
- 仿真导向侧：DriveDreamer / GAIA-1 / WoTE
