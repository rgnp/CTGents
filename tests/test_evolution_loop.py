"""evolution_loop.py 测试 — 自进化编排器的 prompt 构建和流程逻辑。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.evolution_loop import build_evolution_system_prompt


class TestEvolutionSystemPrompt:
    """系统 prompt 构建测试。"""

    def test_contains_goal(self):
        prompt = build_evolution_system_prompt("优化文件搜索性能")
        assert "优化文件搜索性能" in prompt

    def test_contains_flow_diagram(self):
        prompt = build_evolution_system_prompt("test")
        assert "研究" in prompt
        assert "综合" in prompt
        assert "生成" in prompt
        assert "验证" in prompt

    def test_contains_key_tools(self):
        prompt = build_evolution_system_prompt("test")
        assert "evolve_query" in prompt
        assert "evolve_check_access" in prompt
        assert "evolve_validate" in prompt
        assert "evolve_status" in prompt

    def test_contains_safety_rules(self):
        prompt = build_evolution_system_prompt("test")
        assert "git_commit" in prompt
        assert "回滚" in prompt or "rollback" in prompt.lower()

    def test_markdown_formatting(self):
        prompt = build_evolution_system_prompt("test")
        assert prompt.startswith("##")


if __name__ == "__main__":
    tests = []
    instance = TestEvolutionSystemPrompt()
    for name in dir(instance):
        if name.startswith("test_"):
            tests.append((f"TestEvolutionSystemPrompt.{name}", getattr(instance, name)))

    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
