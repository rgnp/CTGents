"""rag.py — 索引/检索，重点回归增量更新的索引一致性。

rag.py 此前无测试。最关键的不变式：倒排表里的每个 doc_id 都必须落在
documents 范围内，且 num_docs == len(documents)，否则 search() 会 IndexError
或返回错块。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools import rag


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _assert_index_consistent(idx) -> None:
    assert idx.num_docs == len(idx.documents), "num_docs 与 documents 数不一致"
    max_doc_id = max(
        (doc_id for postings in idx.inverted_index.values() for doc_id, _ in postings),
        default=-1,
    )
    assert max_doc_id < len(idx.documents), "倒排表 doc_id 越界 → 索引损坏"


# ═══ chunk patterns ═══

def test_all_chunk_patterns_compile():
    """回归：_CHUNK_PATTERNS 的正则必须全部可编译。"""
    import re
    for _lang, (fn, comment) in rag._CHUNK_PATTERNS.items():
        re.compile(fn)
        re.compile(comment)
    re.compile(rag._DEFAULT_FN_PATTERN)
    re.compile(rag._DEFAULT_COMMENT_PATTERN)


def test_get_chunk_patterns_known_language():
    fn, cmt = rag._get_chunk_patterns("python")
    assert fn is not None
    assert cmt is not None


def test_get_chunk_patterns_unknown_language():
    """未知语言 → 默认 pattern。"""
    fn, cmt = rag._get_chunk_patterns("elvish")
    assert fn == rag._DEFAULT_FN_PATTERN


# ═══ index / query ═══

def test_index_and_query(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "alpha.py").write_text(
        "def alpha_func():\n    return 'alpha unique token'\n", encoding="utf-8"
    )
    rag.index_project(str(proj), force=True)
    out = rag.query_index("alpha_func", path=str(proj))
    assert "alpha.py" in out


def test_query_no_index(tmp_path):
    proj = _make_project(tmp_path)
    out = rag.query_index("anything", path=str(proj))
    assert "尚未建立" in out


def test_query_no_keywords(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "x.py").write_text("pass\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    out = rag.query_index("a b c", path=str(proj))
    assert "无法" in out or "未找到" in out


def test_query_no_results(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "x.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    out = rag.query_index("xyznonexistent12345", path=str(proj))
    assert "未找到" in out


def test_index_no_files(tmp_path):
    proj = _make_project(tmp_path)
    out = rag.index_project(str(proj), force=True)
    assert "未找到" in out


def test_index_force_rebuild(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    out = rag.index_project(str(proj), force=True)
    assert "全量索引" in out


def test_index_incremental_no_change(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    out = rag.index_project(str(proj), force=False)
    assert "已是最新" in out


def test_incremental_update_keeps_index_consistent(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def func_a():\n    return 'aaa'\n", encoding="utf-8")
    (proj / "b.py").write_text("def func_b():\n    return 'bbb'\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    (proj / "a.py").write_text(
        "def func_a_renamed():\n    return 'ccc unique token here'\n", encoding="utf-8"
    )
    rag.index_project(str(proj), force=False)
    idx = rag._load_index(rag._find_project_root(str(proj)))
    _assert_index_consistent(idx)
    assert "func_a_renamed" in rag.query_index("func_a_renamed", path=str(proj))


def test_incremental_handles_deleted_file(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "keep.py").write_text("def keeper():\n    return 1\n", encoding="utf-8")
    (proj / "gone.py").write_text("def goner():\n    return 2\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    (proj / "gone.py").unlink()
    rag.index_project(str(proj), force=False)
    idx = rag._load_index(rag._find_project_root(str(proj)))
    _assert_index_consistent(idx)
    assert all("gone.py" not in d.file_path for d in idx.documents)


def test_incremental_no_existing_index(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    out = rag.index_project(str(proj), force=False)
    assert "全量索引" in out


def test_status(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    out = rag.get_index_status(path=str(proj))
    assert "RAG" in out


def test_status_no_index(tmp_path):
    proj = _make_project(tmp_path)
    out = rag.get_index_status(path=str(proj))
    assert "未建立" in out


# ═══ file ignore ═══

def test_min_js_glob_ignored(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "app.js").write_text("function realCode() { return 'indexme'; }\n", encoding="utf-8")
    (proj / "vendor.min.js").write_text("function minified(){return 'skipme';}\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    idx = rag._load_index(rag._find_project_root(str(proj)))
    assert all("vendor.min.js" not in d.file_path for d in idx.documents)
    assert any("app.js" in d.file_path for d in idx.documents)


def test_should_ignore_dir():
    assert rag._should_ignore_dir(".git")
    assert rag._should_ignore_dir("__pycache__")
    assert rag._should_ignore_dir(".hidden")
    assert not rag._should_ignore_dir("src")


def test_should_ignore_file_exact():
    assert rag._should_ignore_file("package-lock.json", ".json")
    assert not rag._should_ignore_file("app.py", ".py")


def test_should_ignore_file_pyc():
    assert rag._should_ignore_file("module.pyc", ".pyc")


# ═══ identifier extraction ═══

def test_extract_identifiers():
    ids = rag._extract_identifiers("def hello_world(arg1, arg2):\n    pass\n")
    assert "hello_world" in ids
    assert "hello" in ids
    assert "world" in ids
    assert "arg1" in ids


def test_extract_identifiers_camel_case():
    ids = rag._extract_identifiers("camelCase\n")
    assert "camelCase" in ids
    assert "camel" in ids
    assert "Case" in ids


def test_extract_keywords_from_query():
    kws = rag._extract_keywords_from_query("find all python functions")
    assert "python" in kws
    assert "functions" in kws
    assert "the" not in kws
    assert "all" not in kws


# ═══ chunking ═══

def test_chunk_python_file_with_function(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def foo():\n    pass\n\ndef bar():\n    return 1\n", encoding="utf-8")
    chunks = rag._chunk_python_file(f, f.read_text(), "python")
    assert len(chunks) == 2
    assert chunks[0].name == "foo"
    assert chunks[1].name == "bar"


def test_chunk_python_file_with_class(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("class MyClass:\n    def method(self):\n        pass\n", encoding="utf-8")
    chunks = rag._chunk_python_file(f, f.read_text(), "python")
    assert len(chunks) >= 1
    assert chunks[0].name == "MyClass"
    assert chunks[0].chunk_type == "class"


def test_chunk_python_file_no_fn(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    chunks = rag._chunk_python_file(f, f.read_text(), "python")
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "block"


def test_chunk_generic_file(tmp_path):
    f = tmp_path / "test.js"
    f.write_text("function hello() {\n  return 1;\n}\n", encoding="utf-8")
    chunks = rag._chunk_generic_file(f, f.read_text(), "javascript")
    assert len(chunks) >= 1


def test_chunk_generic_file_no_fn(tmp_path):
    f = tmp_path / "test.md"
    content = "line\n" * 60
    f.write_text(content, encoding="utf-8")
    chunks = rag._chunk_generic_file(f, content, "markdown")
    assert len(chunks) > 0


def test_chunk_file_unicode_decode_error(tmp_path):
    """二进制文件 → 空列表或替换字符块（取决于 OS）。"""
    f = tmp_path / "test.py"
    f.write_bytes(b"\x80\x81\x82")
    rag._chunk_file(f, "python")
    # 不崩溃就是通过


def test_chunk_file_empty(tmp_path):
    """空文件不崩溃。"""
    f = tmp_path / "test.py"
    f.write_text("", encoding="utf-8")
    rag._chunk_file(f, "python")
    # 不崩溃就是通过


def test_split_large_chunk():
    lines = ["x = 1\n"] * 100
    content = "".join(lines)
    chunks = rag._split_large_chunk(Path("/tmp/fake.py"), "python", content, 0, "BigClass")
    assert len(chunks) > 1
    for c in chunks:
        assert "BigClass" in c.name


# ═══ CodeChunk / serialization ═══

def test_codechunk_to_dict_from_dict():
    c1 = rag.CodeChunk("a.py", "python", "function", "foo", 1, 3, "def foo():\n    pass\n")
    d = c1.to_dict()
    c2 = rag.CodeChunk.from_dict(d)
    assert c2.name == "foo"
    assert c2.file_path == "a.py"


# ═══ TfIdfIndex ═══

def test_tfidf_search_empty():
    idx = rag.TfIdfIndex()
    results = idx.search(["test"])
    assert results == []


def test_tfidf_serialization_roundtrip():
    idx = rag.TfIdfIndex()
    c = rag.CodeChunk("a.py", "python", "function", "foo", 1, 2, "def foo(): pass")
    idx.add_document(c)
    d = idx.to_dict()
    idx2 = rag.TfIdfIndex.from_dict(d)
    assert idx2.num_docs == 1
    assert idx2.documents[0].name == "foo"


# ═══ research indexing ═══

def test_doc_index_roundtrip():
    c = rag.DocChunk("arxiv:1234", "Test Paper", "This is an abstract.", "paper")
    d = c.to_dict()
    c2 = rag.DocChunk.from_dict(d)
    assert c2.source == "arxiv:1234"
    assert c2.title == "Test Paper"


def test_index_doc_chunks(tmp_path):
    chunks = [
        rag.DocChunk("src:1", "Doc A", "content about machine learning", "paper"),
        rag.DocChunk("src:2", "Doc B", "deep neural networks", "note"),
    ]
    n = rag._index_doc_chunks(chunks, "_test_docs")
    assert n == 2
    idx = rag._load_doc_index("_test_docs")
    assert idx is not None
    results = rag._search_doc_index(idx, "machine learning")
    assert len(results) >= 1
    assert results[0]["source"] == "src:1"


def test_load_doc_index_missing():
    assert rag._load_doc_index("_nonexistent_xyz") is None


def test_search_doc_index_empty_query():
    idx = {"idf": {}, "vectors": [], "chunks": [], "doc_count": 0}
    results = rag._search_doc_index(idx, "   ")
    assert results == []


# ═══ execute ═══

def test_execute_unknown_tool():
    result = rag.execute("unknown_tool", {})
    assert result is None


def test_execute_rag_status():
    result = rag.execute("rag_status", {})
    assert isinstance(result, str)


def test_execute_rag_index():
    """rag_index 通过 execute 正常调度。"""
    # 不传 path，用当前项目（会迅速结束因为已有索引）
    result = rag.execute("rag_index", {"path": None})
    assert isinstance(result, str)


def test_execute_rag_query():
    result = rag.execute("rag_query", {"query": "def main"})
    assert isinstance(result, str)


# ═══ hash cache ═══

def test_hash_cache(tmp_path):
    proj = _make_project(tmp_path)
    hashes = {"a.py": "1234-56"}
    rag._write_hash_cache(proj, hashes)
    loaded = rag._read_hash_cache(proj)
    assert loaded == hashes


def test_get_file_hash(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("hello", encoding="utf-8")
    h = rag._get_file_hash(f)
    assert "-" in h


# ═══ rebuild_from_documents ═══

def test_rebuild_from_documents():
    c1 = rag.CodeChunk("a.py", "python", "function", "f", 1, 2, "def f(): pass")
    c2 = rag.CodeChunk("b.py", "python", "function", "g", 1, 2, "def g(): pass")
    idx = rag._rebuild_from_documents([c1, c2])
    assert idx.num_docs == 2
    _assert_index_consistent(idx)


# ═══ find_project_root ═══

def test_find_project_root_git(tmp_path):
    proj = _make_project(tmp_path)
    found = rag._find_project_root(str(proj / "src"))
    assert found == proj.resolve()


def test_find_project_root_no_marker(tmp_path):
    found = rag._find_project_root(str(tmp_path))
    assert found == tmp_path.resolve()
