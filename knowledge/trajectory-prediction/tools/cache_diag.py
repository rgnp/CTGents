"""
缓存健康诊断工具

功能：
1. 检测前缀膨胀（环境信息重复次数）
2. 估算缓存命中率影响
3. 给出清理建议

用法：
  对话中直接查看当前上下文估算
  或在出现缓存命中率低时运行诊断
"""

import os
import re
from pathlib import Path


# 已知的前缀膨胀标记（Agent 系统每次重启注入的内容）
BLOAT_MARKERS = [
    # 环境信息
    "当前环境：",
    "操作系统: Windows 10",
    
    # 项目信息
    "当前项目: ",
    "语言: Python/C/C++",
    "框架: pdm/poetry/setuptools/pip/Make",
    "测试: pytest",
    "运行: make run",
    
    # 记忆提示
    "你拥有长期记忆",
    "需要时用 recall 搜索相关记忆",
    
    # 摘要标记
    "之前对话的摘要：",
    "安全模式:",
    
    # 工具失败记录
    "近期工具失败记录",
    "tool=run_command",
    "tool=think",
    
    # 对话开始标记
    "以上为运行环境信息",
    "不需要在回复中复述或罗列",
]


def estimate_context_from_file(log_file=None):
    """
    如果有一个对话日志文件，可以分析其中的前缀膨胀。
    否则返回一个 "运行时诊断说明"。
    """
    if log_file and os.path.exists(log_file):
        content = Path(log_file).read_text(encoding="utf-8")
        return analyze_text(content)
    
    return {
        "status": "no_file",
        "message": "未提供对话日志文件。请在能看到上下文内容时手动检查。"
    }


def analyze_text(text):
    """分析文本中的重复模式"""
    results = {}
    
    for marker in BLOAT_MARKERS:
        count = text.count(marker)
        if count > 1:
            results[marker] = count
    
    if not results:
        return {"status": "clean", "message": "未检测到前缀膨胀。"}
    
    # 按重复次数排序
    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    
    # 估算影响
    max_repeat = max(r[1] for r in sorted_results)
    
    # 估算每条环境信息约 ~130 tokens
    bloat_tokens = sum(count * 15 for _, count in sorted_results)  # 每条标记约15tokens
    # 实际上每条 env message 约 128 tokens，用标记匹配不太准
    # 保守估计：标记数 * 8 tokens
    
    impact_map = {
        1: "✅ 无膨胀",
        2: "🟡 轻度膨胀（缓存命中率 -5% 至 -10%）",
        3: "🔶 中度膨胀（缓存命中率 -10% 至 -20%）",
        4: "🔴 严重膨胀（缓存命中率 -20% 至 -30%）",
        5: "⛔ 极度膨胀（缓存几乎不命中）",
    }
    impact = impact_map.get(max_repeat, "⛔")
    
    return {
        "status": "bloated" if max_repeat >= 2 else "clean",
        "repeated_markers": sorted_results,
        "max_repeat": max_repeat,
        "impact": impact,
        "estimated_bloat_tokens": f"~{max_repeat * 128} tokens（约 {max_repeat} 份 env message）"
    }


def check_cache_hygiene():
    """
    缓存健康检查清单。
    用户可以根据这个手动评估当前对话的缓存健康度。
    """
    print("=" * 50)
    print("缓存健康检查清单")
    print("=" * 50)
    print()
    
    print("检查项 1: 前缀重复")
    print("  [ ] 当前上下文中有几份 '当前环境：'？")
    print("  [ ] 当前上下文中有几份 '之前对话的摘要：'？")
    print("  [ ] 当前上下文中有几份 '近期工具失败记录'？")
    print("  如果任意一项 > 1，就是前缀膨胀")
    print()
    
    print("检查项 2: 工具调用模式")
    print("  [ ] 本轮对话写了几个文件？（目标: ≤ 5 次）")
    print("  [ ] 是否使用了 edit_file_lines 小粒度编辑？（替代: write_file 一次写入）")
    print("  [ ] 是否在 read→write→read→write 交替？（替代: 批量读取→批量写入）")
    print()
    
    print("检查项 3: 摘要变化")
    print("  [ ] 每次 /clear 后的摘要有变化吗？")
    print("  [ ] 如果有变化，前缀就变了，缓存必然失效")
    print()
    
    print("健康标准:")
    print("  ✅ 前缀无重复 + 写入 ≤ 5 次 + 摘要稳定 → 缓存命中率 95%+")
    print("  🟡 前缀重复 1 处 + 写入 ≤ 8 次 → 缓存命中率 85-95%")
    print("  🔴 前缀重复 ≥ 2 处 + 写入 > 8 次 → 缓存命中率 < 85%")
    print()


# ============================================================
# 修复建议（针对 Agent 系统层，不在此代码中可修复）
# ============================================================

def print_fix_recommendation():
    """输出修复建议"""
    print("=" * 50)
    println("根因修复建议（需要修改 Agent 系统）")
    println("=" * 50)
    println()
    println("问题: /clear 或重启时注入新的 env message，但不清理旧的")
    println()
    println("修复方向 A: /clear 时清理旧 env message")
    println("  在 /clear 的 handler 中: 遍历对话历史，删除所有旧的 env message")
    println("  然后再注入新的 env message")
    println()
    println("修复方向 B: 不把摘要写入 env message")
    println("  摘要是每次变化的内容 → 把它放在 env message 之外")
    println("  这样 env message 的内容就稳定了（环境 + 项目信息）")
    println()
    println("修复方向 C: 固定摘要位置，不重复堆叠")
    println("  如果必须有多个摘要，用 '最后一条' 而非 '所有历史'")
    println()
    println("短期缓解（你可以在对话中做）：")
    println("  - 注意到前缀膨胀时，提醒我遵守 Cache 优化协议")
    println("  - 减少不必要的 /clear（只在新话题需要时）")
    println("  - 在当前对话中，检查有几份重复内容")


# 兼容 println
def println(s=""):
    print(s)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_cache_hygiene()
    elif len(sys.argv) > 2 and sys.argv[1] == "--file":
        result = estimate_context_from_file(sys.argv[2])
        if result["status"] in ("clean", "bloated") and "repeated_markers" in result:
            print(f"检测状态: {result['status']}")
            print(f"最大重复次数: {result['max_repeat']}")
            print(f"影响评估: {result['impact']}")
            print(f"估算膨胀: {result['estimated_bloat_tokens']}")
            print()
            if result["repeated_markers"]:
                print("重复标记:")
                for marker, count in result["repeated_markers"][:10]:
                    print(f"  '{marker}' 重复 {count} 次")
        else:
            print(result["message"])
        print()
        check_cache_hygiene()
        print()
        print_fix_recommendation()
    else:
        check_cache_hygiene()
        print()
        print_fix_recommendation()
