# 论文类型分类学

> 定位: 跨 skill 共享的论文分类体系
> 使用: 读取论文时用于判定 paper_type 轴取值

---

## 分类体系

### 1. 实验研究型 (research)
- **特征**: 提出方法 → 实验验证 → 声称改进
- **论证结构**: empirical claim + experimental data as grounds
- **典型 venue**: CVPR/ICCV/ECCV（CV）、NeurIPS/ICML/ICLR（ML）、CoRL/ICRA（机器人）
- **判断**: 有 train/val/test split + 定量指标对比表

### 2. 综述 (survey)
- **特征**: 系统性地回顾、分类、总结已有文献
- **论证结构**: 分类学 + 叙事 + open problems
- **典型 venue**: TPAMI/IJCV（长综述）、各会议的 survey track
- **判断**: 标题含 "Survey"/"Review"/"A Comprehensive Study" 等

### 3. 理论型 (theory)
- **特征**: 以定理/证明/理论分析为主体，实验为辅或没有
- **论证结构**: mathematical claim + proof as warrant
- **典型 venue**: COLT、ICML/NeurIPS（theory track）
- **判断**: 核心贡献是定理而非实验结果，实验是验证而非主体

### 4. 立场/观点型 (position)
- **特征**: 提出新视角、挑战范式、倡导新方向
- **论证结构**: logical argument + evidence citation（非原创实验）
- **典型 venue**: 各会议的 position paper track，CACM Viewpoints
- **判断**: 没有新的实验方法，核心贡献在"视角"或"批评"

### 5. 基准/数据集 (benchmark)
- **特征**: 主要贡献是新数据集或新 benchmark
- **论证结构**: dataset description + baseline experiments
- **典型 venue**: NeurIPS Datasets & Benchmarks
- **判断**: 标题含 benchmark/dataset，核心贡献在数据而非方法

## 边界案例

- "既是综述又提了新方法" → 归为 research（核心在方法）
- "理论为主但有大量实验" → 归为 theory（核心在理论）
- 不确定时 → 默认 research，标注不确定
