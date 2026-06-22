"""项目知识收割测试：跳过条件、隔离收割+清洗、落盘 type=knowledge 且被索引注入（摘要）。

关键不变量：save 写出的档案必须出现在 memory._build_context() 注入文本里（防只写不读）。
type=knowledge → 注入的是一行摘要（非全文），故断言名字+首句关键内容可见。
"""

import dataclasses
import importlib

import pytest

from src import project_model


@pytest.fixture
def iso_mem(monkeypatch, tmp_path):
    """记忆目录指向 tmp；取 sys.modules 现行 memory 实例打补丁（同 test_user_model）。"""
    mem = importlib.import_module("src.tools.memory")
    import src.config as cfg
    d = tmp_path / "memory"
    d.mkdir()
    monkeypatch.setattr(mem, "MEMORY_DIR", str(d))
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(d))
    mem.mark_dirty()
    return d, mem

def _log(*pairs: tuple[str, str]) -> list[dict]:
    return [{"role": r, "content": c} for r, c in pairs]

def _patch_params(monkeypatch, **kw):
    monkeypatch.setattr(project_model, "PROJECT_KNOWLEDGE",
                        dataclasses.replace(project_model.PROJECT_KNOWLEDGE, **kw))

# ── 跳过条件（绝不触发 LLM）──

def test_skip_when_disabled(monkeypatch):
    _patch_params(monkeypatch, enabled=False)
    assert project_model.harvest_project_knowledge(
        _log(("user", "a"), ("user", "b"), ("user", "c"))) is None

def test_skip_when_too_few_user_messages(monkeypatch):
    _patch_params(monkeypatch, min_user_messages=3)
    assert project_model.harvest_project_knowledge(_log(("user", "a"), ("user", "b"))) is None

# ── 收割（mock 隔离 backend）──

def test_harvest_isolated_and_cleans(monkeypatch, iso_mem):
    from src import llm
    captured: dict = {}

    def fake(messages, _cb, tools=None, max_tokens=None):
        captured["messages"] = messages
        captured["tools"] = tools
        captured["max_tokens"] = max_tokens
        return ("```markdown\n## 资源\n- 科研用 DATASET_XYZ\n```", None)

    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream", fake)
    log = _log(("user", "我们科研用 DATASET_XYZ"), ("user", "对"),
               ("user", "嗯"), ("assistant", "记下了"))
    body = project_model.harvest_project_knowledge(log)

    assert body is not None
    assert "DATASET_XYZ" in body
    assert "```" not in body
    assert captured["tools"] is None
    payload = captured["messages"][1]["content"]
    assert "现有档案" in payload and "本次对话" in payload

def test_harvest_network_failure_returns_none(monkeypatch, iso_mem):
    from src import llm
    monkeypatch.setattr(project_model.time, "sleep", lambda *_a: None)
    _patch_params(monkeypatch, harvest_retries=1)

    def boom(*_a, **_k):
        raise OSError("net down")  # RETRYABLE 含 OSError

    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream", boom)
    log = _log(("user", "a"), ("user", "b"), ("user", "c"), ("assistant", "x"))
    assert project_model.harvest_project_knowledge(log) is None

# ── 落盘 + 注入（核心不变量：写了真被读）──

def test_save_type_knowledge_no_severity(iso_mem):
    d, _mem = iso_mem
    assert project_model.save_project_knowledge("科研用 DATASET_XYZ 数据集。") is True
    text = (d / "project-knowledge.md").read_text(encoding="utf-8")
    assert "type: knowledge" in text
    assert "severity" not in text  # 无 severity → 不被 _build_context 当 lesson 跳过

def test_save_empty_is_noop(iso_mem):
    d, _mem = iso_mem
    assert project_model.save_project_knowledge("   ") is False
    assert not (d / "project-knowledge.md").exists()

def test_saved_knowledge_indexed_into_context(iso_mem):
    """关键不变量：落盘的项目知识出现在每轮注入索引里（摘要形态，非全文）。"""
    _d, mem = iso_mem
    project_model.save_project_knowledge("科研用 DATASET_XYZ 数据集，流程见别处。")
    ctx = mem._build_context()
    assert ctx is not None
    assert "project-knowledge" in ctx       # 被索引
    assert "DATASET_XYZ" in ctx             # 首句关键内容进了摘要

def test_harvest_and_save_end_to_end(monkeypatch, iso_mem):
    _d, mem = iso_mem
    from src import llm
    monkeypatch.setattr(llm.AVAILABLE_MODELS["pro"], "chat_non_stream",
                        lambda *_a, **_k: ("项目约定 DEPLOY_FRIDAY 周五部署。", None))
    log = _log(("user", "我们周五部署"), ("user", "对"), ("user", "嗯"), ("assistant", "好"))
    note = project_model.harvest_and_save(log)
    assert note is not None
    assert "DEPLOY_FRIDAY" in mem._build_context()
