"""
跨论文交叉验证器

功能：对一篇新论文，自动与知识库中已有的论文做交叉验证
  - 检测矛盾/冲突（Contradiction）
  - 检测互补/支撑（Corroboration）
  - 检测是否填补已有关 Gap 或暴露新 Gap
  - 检测方法论类型对比
  - 生成结构化交叉验证报告

用法：
  python cross_validator.py --paper "论文标题" --abstract "摘要" --contributions "贡献"

设计理念：
  把"读一篇新论文→判断它和已有知识的关系"这个高频操作，
  从手工程序升级为半自动化的确定性流程。
  减少遗漏，消除偏见，每次一致。
"""

import json
import re
import os
import sys
from pathlib import Path

# ============================================================
# 知识库路径配置
# ============================================================

KNOWLEDGE_BASE = Path(__file__).parent.parent  # knowledge/trajectory-prediction/
PAPERS_DIR = KNOWLEDGE_BASE / "papers"
META_DIR = KNOWLEDGE_BASE / "meta"
INDEX_FILE = KNOWLEDGE_BASE / "KNOWLEDGE_INDEX.md"


# ============================================================
# 从已有的论文卡片中提取关键信息
# ============================================================

def load_paper_cards():
    """遍历 papers/ 目录，解析 Markdown 卡片中的结构化信息"""
    papers = {}
    if not PAPERS_DIR.exists():
        return papers
    
    for md_file in sorted(PAPERS_DIR.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        info = parse_paper_card(content, md_file.stem)
        if info:
            papers[md_file.stem] = info
    
    return papers


def parse_paper_card(content, filename):
    """从论文卡片中提取结构化字段"""
    info = {"file": filename}
    
    # 标题（第一行 # 后面的内容）
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if title_match:
        info["title"] = title_match.group(1).strip()
    
    # 表格字段
    patterns = {
        "conference": r"\*\*会议/年份\*\*\s*\|\s*(.+)",
        "year": r"\*\*会议/年份\*\*\s*\|\s*.+?(\d{4})",
        "authors": r"\*\*作者\*\*\s*\|\s*(.+)",
        "task": r"\*\*任务\*\*\s*\|\s*(.+)",
        "dataset": r"\*\*数据集\*\*\s*\|\s*(.+)",
        "code": r"\*\*代码\*\*\s*\|\s*(.+)",
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            info[key] = match.group(1).strip()
    
    # 核心贡献（## 核心贡献 后面的一行）
    contrib_match = re.search(r'## 核心贡献.*?\n([^\n]+)', content, re.DOTALL)
    if contrib_match:
        info["contribution"] = contrib_match.group(1).strip().replace("**", "")
    
    # 实验结论部分
    experiment_match = re.search(r'## 实验结论\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if experiment_match:
        info["experiment"] = experiment_match.group(1).strip()[:500]
    
    # 局限性部分
    limitations_match = re.search(r'## 局限性.*?\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if limitations_match:
        info["limitations"] = limitations_match.group(1).strip()[:500]
    
    # 全文（用于关键词搜索）
    info["full_text"] = content
    
    return info


def load_gaps():
    """从 gaps.md 加载已知 Gap"""
    gaps_file = META_DIR / "gaps.md"
    if not gaps_file.exists():
        return {}
    
    content = gaps_file.read_text(encoding="utf-8")
    gaps = {}
    
    # 提取每个 Gap 块
    gap_blocks = re.findall(r'### Gap ([A-E]): (.+?)(?=### |\Z)', content, re.DOTALL)
    for letter, block_content in gap_blocks:
        # 去掉冒号前的字母
        name_match = re.search(rf'### Gap {letter}: (.+)', block_content[:200])
        name = name_match.group(1).strip() if name_match else f"Gap {letter}"
        
        # 提取核心主张
        claim_match = re.search(r'核心主张[：:]\s*(.+?)(?:\n|$)', block_content)
        claim = claim_match.group(1).strip() if claim_match else ""
        
        # 提取等级
        status_match = re.search(r'等级[：:]\s*(.+?)(?:\n|$)', block_content)
        status = status_match.group(1).strip() if status_match else "未知"
        
        gaps[letter] = {
            "name": f"Gap {letter}: {name}",
            "claim": claim,
            "status": status
        }
    
    return gaps


# ============================================================
# 交叉验证核心逻辑
# ============================================================

def check_contradiction(new_info, existing_papers):
    """
    检测矛盾/冲突。
    矛盾类型：
    - 结论相反：A说好，B说不好
    - 假设对立：A假设静态环境，B证明静态假设不成立
    - 性能倒挂：A宣称SOTA，B宣称超越A
    """
    contradictions = []
    new_title = new_info.get("title", "").lower()
    new_text = new_info.get("full_text", "").lower()
    new_contrib = new_info.get("contribution", "").lower()
    
    for paper_id, paper in existing_papers.items():
        paper_title = paper.get("title", "").lower()
        paper_contrib = paper.get("contribution", "").lower()
        paper_limitations = paper.get("limitations", "").lower()
        
        # 类型1：如果新论文的摘要/贡献中提到了已有论文并说"不好"
        for keyword in [paper_id.replace("-", " "), paper_title[:30].lower()]:
            if keyword and len(keyword) > 5:
                if keyword in new_text:
                    # 检查是否提到 limitation / outperform / surpass
                    context_matches = []
                    for ctx_kw in ["limitation", "outperform", "surpass", "outperform",
                                   "better than", "superior", "weakness", "fails",
                                   "does not", "cannot", "however", "limitation"]:
                        if ctx_kw in new_text:
                            context_matches.append(ctx_kw)
                    if context_matches:
                        contradictions.append({
                            "paper": paper.get("title", paper_id),
                            "type": "可能挑战",
                            "evidence": f"新论文提及 '{paper.get('title', paper_id)[:40]}' "
                                       f"并包含对立性语气词: {', '.join(context_matches)}",
                            "severity": "中"
                        })
        
        # 类型2：如果新论文的贡献直接对立已有论文的贡献
        if paper_contrib and new_contrib:
            # 检测"我们超过/优于XXX"
            for outperform_kw in ["outperform", "state-of-the-art", "sota", "better than"]:
                if outperform_kw in new_contrib:
                    contradictions.append({
                        "paper": paper.get("title", paper_id),
                        "type": "性能超越",
                        "evidence": f"新论文声称 '{outperform_kw}'",
                        "severity": "高（需要仔细对比实验设置）"
                    })
                    break
    
    return contradictions


def check_corroboration(new_info, existing_papers):
    """
    检测互补/支撑。
    互补类型：
    - 方法论互补：不同方法解决同一问题
    - 发现互补：A发现现象X，B解释现象X
    - 延伸互补：A做了基础，B做了延伸
    """
    corroborations = []
    new_text = new_info.get("full_text", "").lower()
    new_task = new_info.get("task", "").lower()
    
    for paper_id, paper in existing_papers.items():
        paper_text = paper.get("full_text", "").lower()
        paper_task = paper.get("task", "").lower()
        paper_contrib = paper.get("contribution", "").lower()
        
        # 类型1：相似任务不同方法
        if paper_task and new_task and paper_task == new_task:
            corroborations.append({
                "paper": paper.get("title", paper_id),
                "type": "同任务不同方法",
                "detail": f"都是做 {paper_task}",
                "value": "可以做方法对比实验"
            })
        
        # 类型2：引用了已有论文
        paper_keywords = []
        for kw_base in [paper_id.replace("-", " "), paper.get("title", paper_id)[:20].lower()]:
            if kw_base and len(kw_base) > 4 and kw_base in new_text:
                paper_keywords.append(kw_base)
        
        if paper_keywords:
            corroborations.append({
                "paper": paper.get("title", paper_id),
                "type": "直接引用",
                "detail": f"新论文引用了本文",
                "value": "是直接相关工作的延续或对比"
            })
        
        # 类型3：局限性互补
        limitations = paper.get("limitations", "")
        if limitations and len(limitations) > 20:
            lim_lower = limitations.lower()
            # 检查新论文是否声称解决了这个局限
            for lim_kw in ["lack", "does not", "cannot", "fails", "limited",
                           "not handle", "not consider", "only"]:
                if lim_kw in lim_lower:
                    # 抽取局限性句子的关键词
                    lim_sentences = re.findall(r'[^。]*' + re.escape(lim_kw) + r'[^。]*。', limitations)
                    if lim_sentences:
                        for sent in lim_sentences[:2]:
                            # 检查新论文的方法是否对应
                            new_methods = re.findall(r'(propose|introduce|present|develop)\s+(a\s+)?(\w+\s+){1,5}', new_text)
                            if new_methods:
                                corroborations.append({
                                    "paper": paper.get("title", paper_id),
                                    "type": "解决局限性",
                                    "detail": f"已有论文指出 '{sent.strip()[:80]}'，"
                                             f"新论文可能通过 {'...'.join([m[0] for m in new_methods[:2]])} 解决",
                                    "value": "直接延续关系，适合作为 Related Work 对比"
                                })
                        break
    
    return corroborations


def check_gap_impact(new_info, gaps):
    """检测新论文对已知 Gap 的影响"""
    impacts = []
    new_text = new_info.get("full_text", "").lower()
    new_title = new_info.get("title", "").lower()
    combined = f"{new_title} {new_text}"
    
    # Gap 关键词映射
    gap_keywords = {
        "A": ["planning", "planner", "planning-aware", "planning compatible",
              "downstream", "differentiable planning"],
        "B": ["calibration", "uncertainty", "confidence", "ece",
              "expected calibration", "temperature scaling"],
        "C": ["predictive coding", "prediction error", "error prediction",
              "metacognition", "self-assessment"],
        "D": ["closed-loop", "simulation", "simulator", "interactive evaluation"],
        "E": ["joint prediction planning", "integrated", "interactive planner",
              "prediction-planning"]
    }
    
    for letter, gap_info in gaps.items():
        keywords = gap_keywords.get(letter, [])
        hits = [kw for kw in keywords if kw.lower() in combined]
        
        if hits:
            gap_name = gap_info["name"]
            gap_status = gap_info["status"]
            
            # 判断影响类型
            if len(hits) >= 3:
                impact_type = "🟢 可能填补" if "确认" in gap_status else "🟢 可能验证"
            elif len(hits) >= 1:
                impact_type = "🟡 部分相关"
            else:
                impact_type = "⚪ 微弱关联"
            
            impacts.append({
                "gap": gap_name,
                "current_status": gap_status,
                "impact": impact_type,
                "hit_keywords": hits,
                "detail": gap_info.get("claim", "")[:100]
            })
    
    return impacts


def extract_new_findings(new_info):
    """提取新论文暴露的新 Gap / 新 insight"""
    findings = []
    text = new_info.get("full_text", "").lower()
    limitations = new_info.get("limitations", "")
    
    # 检查 limitations/未来工作 部分
    if limitations:
        # 提取"没有/未能/局限"等信号
        gap_signals = re.findall(
            r'(limitation|future work|not explored|remain|open challenge|'
            r'need to|should|requires further|not yet|not studied|limited to)'
            r'[^。]*。',
            limitations
        )
        for signal in gap_signals[:3]:
            findings.append({
                "type": "⚪ 潜在新 Gap",
                "source": "局限性/未来工作",
                "detail": signal.strip()[:150]
            })
    
    # 检查实验结论中的意外发现
    experiment = new_info.get("experiment", "")
    if experiment:
        unexpected_signals = re.findall(
            r'(surprising|unexpected|counterintuitive|interestingly|'
            r'notably|remarkably|unfortunately|contrary)'
            r'[^。]*。',
            experiment
        )
        for signal in unexpected_signals[:2]:
            findings.append({
                "type": "⚪ 意外发现",
                "source": "实验结论",
                "detail": signal.strip()[:150]
            })
    
    return findings


# ============================================================
# 生成方法论对比
# ============================================================

def compare_methodology(new_info, existing_papers):
    """对比新论文与已有论文的方法论类型"""
    from paper_analyzer import classify_methodology
    comparisons = []
    
    new_title = new_info.get("title", "Unknown")
    new_abstract = new_info.get("full_text", "")[:1000]
    new_contrib = new_info.get("contribution", "")
    
    new_mtype = classify_methodology(new_title, new_abstract, new_contrib)
    new_primary = new_mtype["primary"]["label"]
    new_risk = new_mtype["primary"]["risk"]
    
    for paper_id, paper in existing_papers.items():
        paper_title = paper.get("title", paper_id)
        paper_text = paper.get("full_text", "")[:1000]
        paper_contrib = paper.get("contribution", "")
        
        paper_mtype = classify_methodology(paper_title, paper_text, paper_contrib)
        paper_primary = paper_mtype["primary"]["label"]
        
        if paper_primary == new_primary:
            relation = "同类型"
        else:
            relation = f"{new_primary} vs {paper_primary}"
        
        comparisons.append({
            "paper": paper.get("title", paper_id),
            "new_methodology": new_primary,
            "paper_methodology": paper_primary,
            "relation": relation,
            "new_risk": new_risk
        })
    
    return comparisons


# ============================================================
# 主报告生成
# ============================================================

def generate_report(new_title, new_abstract, new_contributions=""):
    """生成完整的交叉验证报告"""
    
    # 加载知识库
    papers = load_paper_cards()
    gaps = load_gaps()
    
    if not papers:
        return {"error": "知识库中没有已读论文，无法做交叉验证"}
    
    # 构建新论文的信息字典
    new_info = {
        "title": new_title,
        "full_text": f"{new_title}\n{new_abstract}\n{new_contributions}",
        "contribution": new_contributions,
        "task": "",  # 可选的
        "experiment": "",
        "limitations": ""
    }
    
    # 执行交叉验证
    contradictions = check_contradiction(new_info, papers)
    corroborations = check_corroboration(new_info, papers)
    gap_impacts = check_gap_impact(new_info, gaps)
    new_findings = extract_new_findings(new_info)
    
    # 方法论对比
    try:
        methodology_comparisons = compare_methodology(new_info, papers)
    except Exception:
        methodology_comparisons = []
    
    # 综合评估
    total_papers = len(papers)
    contradict_count = len(contradictions)
    corroborate_count = len(corroborations)
    gap_hit_count = len(gap_impacts)
    
    if gap_hit_count >= 2:
        gap_assessment = f"🟢 命中 {gap_hit_count} 个已有 Gap，方向性强"
    elif gap_hit_count >= 1:
        gap_assessment = f"🟡 部分相关 {gap_hit_count} 个已有 Gap"
    else:
        gap_assessment = "⚪ 未命中任何已有 Gap —— 可能是全新方向"
    
    if contradict_count > 0:
        conflict_assessment = f"⚠️ 与 {contradict_count} 篇论文存在潜在矛盾，需要仔细对比实验设置"
    else:
        conflict_assessment = "✅ 未检测到明显矛盾"
    
    report = f"""# 🧬 交叉验证报告

## 新论文
{new_title}

## 知识库状态
- 已有论文: {total_papers} 篇
- 已知 Gap: {len(gaps)} 个

---

## 📊 综合评估

- **Gap 匹配**: {gap_assessment}
- **矛盾检测**: {conflict_assessment}
- **互补关系**: 与 {corroborate_count} 篇论文存在互补/支撑关系
- **潜在新发现**: {len(new_findings)} 个
- **方法论对比**: 比较了 {len(methodology_comparisons)} 篇论文的方法论类型

---

## 1. Gap 命中分析

"""
    
    if gap_impacts:
        for gi in gap_impacts:
            report += f"""### {gi['gap']}
- **当前状态**: {gi['current_status']}
- **影响**: {gi['impact']}
- **命中关键词**: {', '.join(gi['hit_keywords'])}
- **详情**: {gi['detail'][:100]}

"""
    else:
        report += "未命中任何已知 Gap。\n\n"
    
    report += "## 2. 矛盾/冲突检测\n\n"
    
    if contradictions:
        for c in contradictions:
            report += f"""- **{c['paper']}** [{c['type']}]
  - 证据: {c['evidence'][:200]}
  - 严重程度: {c['severity']}

"""
    else:
        report += "✅ 未检测到明显矛盾。\n\n"
    
    report += "## 3. 互补/支撑关系\n\n"
    
    if corroborations:
        for c in corroborations[:5]:  # 最多显示5条
            report += f"""- **{c['paper']}** [{c['type']}]
  - {c['detail'][:200]}
  - 价值: {c.get('value', 'N/A')}

"""
    else:
        report += "未检测到明显互补关系。\n\n"
    
    report += "## 4. 潜在新发现\n\n"
    
    if new_findings:
        for f in new_findings:
            report += f"""- **{f['type']}**: {f['detail'][:200]}
  - 来源: {f['source']}

"""
    else:
        report += "未提取到潜在新发现（需要进一步阅读全文）。\n\n"
    
    report += "## 5. 方法论对比摘要\n\n"
    
    if methodology_comparisons:
        type_counts = {}
        for mc in methodology_comparisons:
            t = mc["relation"]
            type_counts[t] = type_counts.get(t, 0) + 1
        
        report += "| 关系类型 | 数量 | 涉及论文 |\n"
        report += "|:---------|:----:|:---------|\n"
        for t, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
            examples = [mc["paper"][:30] for mc in methodology_comparisons if mc["relation"] == t][:2]
            report += f"| {t} | {count} | {', '.join(examples)} |\n"
    
    report += f"""

---

## 建议的下一步

"""
    
    # 生成建议
    suggestions = []
    if gap_hit_count >= 2:
        suggestions.append("1. 优先阅读这篇论文中与命中 Gap 相关的部分，确认是否真的填补或深化了已有理解")
    if contradict_count > 0:
        suggestions.append("2. 仔细对比矛盾论文的实验设置（数据集、指标、基线），判断矛盾是否真实存在")
    if corroborate_count == 0:
        suggestions.append("3. 这篇论文可能与已有知识关系不大，建议独立评估其价值")
    suggestions.append("4. 如果决定录入知识库，运行 paper_analyzer.py 生成论文卡片模板")
    
    report += "\n".join(suggestions)
    
    return report


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="跨论文交叉验证器")
    parser.add_argument("--paper", type=str, help="论文标题")
    parser.add_argument("--abstract", type=str, help="论文摘要")
    parser.add_argument("--contributions", type=str, default="", help="论文贡献")
    parser.add_argument("--file", type=str, help="从 JSON 文件读取论文信息")
    
    args = parser.parse_args()
    
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
            title = data.get("title", "")
            abstract = data.get("abstract", "")
            contributions = data.get("contributions", "")
    else:
        title = args.paper or ""
        abstract = args.abstract or ""
        contributions = args.contributions or ""
    
    if not title or not abstract:
        print("请提供论文标题和摘要，或使用 --file 指定 JSON 文件")
        print("用法示例：")
        print('  python cross_validator.py --paper "标题" --abstract "摘要"')
        print('  python cross_validator.py --file paper_data.json')
        sys.exit(1)
    
    report = generate_report(title, abstract, contributions)
    
    if "error" in report:
        print(f"错误: {report['error']}")
        sys.exit(1)
    
    print(report)
