"""自进化编排入口。

真实运行由 evolution_runner 创建 run/state/patch 快照；本模块保留旧导入路径。

流程：
  1. 研究阶段：多源搜索 + 交叉验证 + 提取模式
  2. 综合阶段：组合模式生成 2-3 个候选方案
  3. 生成阶段：LLM 落地代码修改
  4. 验证阶段：运行 validate.py 流水线
  5. 决策阶段：合入、修复或停止，记录进化档案
"""

from .evolution_runner import build_evolution_system_prompt as _build_runner_prompt


def build_evolution_system_prompt(goal: str) -> str:
    """Build a compatible evolution prompt without creating a persistent run."""
    return _build_runner_prompt(goal)
