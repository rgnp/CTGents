"""evolution_loop.py 测试 — 自进化编排器的 prompt 构建和流程逻辑。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.evolution_loop import (
    build_evolution_system_prompt,
    build_research_prompt,
    RESEARCH_PROMPT,
    SYNTHESIS_PROMPT,
    GENERATION_PROMPT,
)


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


class TestResearchPrompt:
    """研究模式 prompt 测试。"""

    def test_formats_goal(self):
        prompt = build_research_prompt("学习异步IO")
        assert "学习异步IO" in prompt

    def test_contains_steps(self):
        prompt = build_research_prompt("test")
        assert "第1步" in prompt or "历史" in prompt
        assert "search_web" in prompt
        assert "read_page" in prompt

    def test_contains_completion_marker(self):
        prompt = build_research_prompt("test")
        assert "研究阶段完成" in prompt


class TestPromptTemplates:
    """模板完整性测试。"""

    def test_research_prompt_has_format_placeholder(self):
        assert "{goal}" in RESEARCH_PROMPT

    def test_synthesis_prompt_has_structure(self):
        assert "方案ID" in SYNTHESIS_PROMPT
        assert "优点" in SYNTHESIS_PROMPT
        assert "缺点" in SYNTHESIS_PROMPT

    def test_generation_prompt_has_safety_steps(self):
        assert "evolve_check_access" in GENERATION_PROMPT
        assert "git_commit" in GENERATION_PROMPT
        assert "evolve_validate" in GENERATION_PROMPT


if __name__ == "__main__":
    tests = []
    for cls in [TestEvolutionSystemPrompt, TestResearchPrompt, TestPromptTemplates]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(instance, name)))

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
