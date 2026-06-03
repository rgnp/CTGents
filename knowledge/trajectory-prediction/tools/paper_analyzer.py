
"""
论文方法论分类器 + Gap 匹配引擎

此工具固化以下能力（从 research-assistant-mode.md 中提取并编译为代码）：
1. 自动分类论文的方法论类型（6种类型体系）
2. 检查新论文是否命中已有 Gap
3. 生成论文卡片模板

设计理念：
  提示词驱动 → 工具驱动：逻辑一次编译，反复稳定调用
  手工分析 → 自动匹配：关键词交叉验证，消除遗漏
  主观判断 → 量化打分：置信度分数，可追溯
"""

import re
import json
import os
from pathlib import Path
from datetime import datetime

# ============================================================
# 方法论分类体系
# 来源：已读 10 篇轨迹预测论文的模式归纳
# ============================================================

METHODOLOGY_TYPES = {
    "architecture": {
        "label": "架构创新",
        "description": "设计新的模型架构（编码器、解码器、注意力机制等）",
        "keywords": ["transformer", "encoder", "decoder", "attention", "gnn",
                     "graph neural", "hierarchical", "architecture", "backbone",
                     "module", "layer", "autoencoder"],
        "examples": ["HiVT (CVPR 2022)", "ADAPT (ICCV 2023)", "PFR-HiVT (2025)"],
        "risk": "中等——需要显著性能提升，架构改动大",
        "innovation_level": "moderate"
    },
    "problem_driven": {
        "label": "问题驱动修复",
        "description": "诊断一个具体问题，用成熟方法修复，不做新架构。CCTR 范式。",
        "keywords": ["calibration", "fix", "improve", "limitation", "gap",
                     "shortcoming", "calibrat", "correct", "refine",
                     "post-hoc", "postprocessing", "temperature scaling",
                     "uncertainty", "mismatch", "inconsistency"],
        "examples": ["CCTR (AAAI 2024)"],
        "risk": "低——方向明确，改动可控，对标清晰",
        "innovation_level": "incremental"
    },
    "learning_paradigm": {
        "label": "学习范式创新",
        "description": "新的损失函数、辅助任务、预训练策略、训练方法",
        "keywords": ["loss", "auxiliary", "pretext", "self-supervised",
                     "contrastive", "distillation", "pretrain", "finetune",
                     "multi-task", "regularization", "masked", "reconstruction"],
        "examples": ["SSL-Interactions (2024)", "Plan-MAE (2025)",
                     "TrajCLIP (NeurIPS 2024)", "LaKD (NeurIPS 2024)"],
        "risk": "中低——Loss 改动小，关键看故事和验证",
        "innovation_level": "paradigm"
    },
    "representation": {
        "label": "表示学习",
        "description": "改进特征编码方式：对比学习、自蒸馏、域不变表示等",
        "keywords": ["representation", "embedding", "feature", "encoding",
                     "latent", "distill", "invariant", "disentangle",
                     "contrastive", "consistency"],
        "examples": ["PerReg+ (CVPR 2025)", "TrajCLIP (NeurIPS 2024)"],
        "risk": "中等——需要证明表示质量对下游任务提升",
        "innovation_level": "moderate"
    },
    "benchmark_analysis": {
        "label": "基准/分析",
        "description": "提供统一评测、发现系统性问题和 insight",
        "keywords": ["benchmark", "survey", "analysis", "evaluation", "metric",
                     "dataset", "unified", "framework", "empirical", "study",
                     "what truly", "matters"],
        "examples": ["UniTraj (ECCV 2024)", "What Truly Matters (NeurIPS 2023)"],
        "risk": "低——不需要新方法，关键看 insight 质量",
        "innovation_level": "analytical"
    },
    "generation": {
        "label": "生成式方法",
        "description": "扩散模型、VAE、GAN、Normalizing Flows 等",
        "keywords": ["diffusion", "vae", "gan", "generative", "sampling",
                     "denoising", "score-based", "normalizing flow", "ddpm"],
        "examples": ["MotionDiffuser (2023)", "Diffusion-Planner (ICLR 2025)"],
        "risk": "中高——计算开销大，实时性挑战",
        "innovation_level": "disruptive"
    }
}

# 已知 Gap 的关键词签名（与 meta/gaps.md 同步）
KNOWN_GAPS = {
    "Gap A: 规划兼容性辅助训练": {
        "keywords": ["planning", "planner", "differentiable", "planning loss",
                     "planning-aware", "downstream", "planning compatible",
                     "motion planning", "planning feedback"],
        "status": "✅",
        "file": "gaps.md#GapA"
    },
    "Gap B: 训练期不确定性校准": {
        "keywords": ["calibration", "uncertainty", "confidence", "ece",
                     "calibration loss", "end-to-end calibration",
                     "training-time calibration", "expected calibration"],
        "status": "✅",
        "file": "gaps.md#GapB"
    },
    "Gap C: 预测编码辅助任务": {
        "keywords": ["predictive coding", "prediction error",
                     "error prediction", "metacognition",
                     "uncertainty estimation", "error distribution",
                     "self-assessment", "epistemic"],
        "status": "✅",
        "file": "gaps.md#GapC"
    },
    "Gap D: 闭环评估框架": {
        "keywords": ["closed-loop", "simulation", "simulator",
                     "evaluation framework", "online evaluation",
                     "reactive", "interactive evaluation"],
        "status": "✅",
        "file": "gaps.md#GapD"
    },
    "Gap E: HiVT驱动的交互规划器": {
        "keywords": ["joint prediction planning", "integrated",
                     "interactive planner", "prediction-planning",
                     "joint framework", "unified prediction planning"],
        "status": "🔶",
        "file": "gaps.md#GapE"
    }
}


def classify_methodology(title, abstract, contributions=""):
    """
    分类论文方法论类型。
    返回：primary type + confidence + 风险 + 典型会议
    """
    text = f"{title} {abstract} {contributions}".lower()
    scores = {}
    
    for mtype, info in METHODOLOGY_TYPES.items():
        hits = sum(1 for kw in info["keywords"] if kw.lower() in text)
        scores[mtype] = hits / max(len(info["keywords"]), 1)
    
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary_type, primary_score = ranked[0]
    secondary_type, secondary_score = ranked[1] if len(ranked) > 1 else (None, 0)
    
    info = METHODOLOGY_TYPES[primary_type]
    result = {
        "primary": {
            "type": primary_type,
            "label": info["label"],
            "confidence": round(primary_score, 2),
            "description": info["description"],
            "examples": info["examples"],
            "risk": info["risk"],
            "innovation_level": info["innovation_level"]
        }
    }
    
    if secondary_type and secondary_score > 0.1:
        sec_info = METHODOLOGY_TYPES.get(secondary_type, {})
        result["secondary"] = {
            "type": secondary_type,
            "label": sec_info.get("label", "N/A"),
            "confidence": round(secondary_score, 2)
        }
    
    return result


def match_gaps(title, abstract, contributions=""):
    """
    检查新论文是否命中已有 Gap。
    返回：命中的 Gap 列表 + 匹配度
    """
    text = f"{title} {abstract} {contributions}".lower()
    matches = []
    
    for gap_name, gap_info in KNOWN_GAPS.items():
        hits = []
        for kw in gap_info["keywords"]:
            if kw.lower() in text:
                hits.append(kw)
        
        if hits:
            impact = ("🟢 可能填补" if len(hits) >= 3
                     else "🟡 部分相关" if len(hits) >= 1
                     else "⚪ 微弱关联")
            matches.append({
                "gap": gap_name,
                "status": gap_info["status"],
                "hit_keywords": hits,
                "hit_count": len(hits),
                "impact": impact
            })
    
    return sorted(matches, key=lambda x: x["hit_count"], reverse=True)


def generate_card(title, authors, conference, year,
                  abstract, contributions, methodology, gap_matches):
    """生成论文卡片 Markdown 模板"""
    primary = methodology["primary"]
    secondary = methodology.get("secondary")
    
    # Gap 命中部分
    if gap_matches:
        gap_lines = []
        for m in gap_matches:
            gap_lines.append(
                f"- **{m['gap']}** [{m['status']}]: {m['impact']} "
                f"({', '.join(m['hit_keywords'])})"
            )
        gap_section = "\n".join(gap_lines)
    else:
        gap_section = "⚠️ **未命中任何已知 Gap → 可能是新的空白方向！**"

    safe_title = title.split(".")[0].strip().replace(":", " -")
    
    return f"""# {safe_title}

| 字段 | 内容 |
|---|---|
| **会议/年份** | {conference} {year} |
| **作者** | {authors} |
| **代码** | 待搜索 |
| **阅读日期** | {datetime.now().strftime('%Y-%m-%d')} |
| **阅读状态** | 摘要 |
| **方法论** | {primary['label']}（置信度 {primary['confidence']:.0%}） |
| **创新等级** | {primary['innovation_level']} |
| **风险** | {primary['risk']} |

---

## 核心贡献（一句话）

（待填写）

---

## 方法论分析

- **类型**: {primary['label']}
- **说明**: {primary['description']}
- **同类**: {', '.join(primary['examples'])}{f'''
- **次级**: {secondary['label']}（{secondary['confidence']:.0%}）''' if secondary else ''}

---

## Gap 命中

{gap_section}

---

## 方法细节（待填写）

---

## 实验结论（待填写）

---

## 局限性（待填写）

---

## 与本项目关系（待填写）
"""


# ============================================================
# 测试：用已知论文验证分类器
# ============================================================
if __name__ == "__main__":
    # 测试 CCTR
    cctr_result = classify_methodology(
        "Calibrating Trajectory Prediction for Uncertainty-Aware Motion Planning",
        "Trajectory prediction models output uncertainty that does not match "
        "actual errors. We propose a post-hoc calibration method using "
        "customized temperature scaling to align confidence with error.",
        "Post-hoc calibration via temperature scaling improves planner safety."
    )
    print("=== CCTR 分类结果 ===")
    print(json.dumps(cctr_result, ensure_ascii=False, indent=2))
    
    cctr_gaps = match_gaps(
        "Calibrating Trajectory Prediction for Uncertainty-Aware Motion Planning",
        "Trajectory prediction models output uncertainty that does not match "
        "actual errors. We propose a post-hoc calibration method."
    )
    print("\n=== CCTR Gap 匹配 ===")
    print(json.dumps(cctr_gaps, ensure_ascii=False, indent=2))
    
    # 测试 HiVT
    hivt_result = classify_methodology(
        "HiVT: Hierarchical Vector Transformer for Multi-Agent Motion Prediction",
        "We propose a hierarchical transformer architecture that decomposes "
        "multi-agent motion prediction into local context extraction and "
        "global interaction modeling using attention mechanisms.",
    )
    print("\n=== HiVT 分类结果 ===")
    print(json.dumps(hivt_result, ensure_ascii=False, indent=2))
