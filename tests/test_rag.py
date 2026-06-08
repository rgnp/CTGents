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
    (tmp_path / ".git").mkdir()  # 让 _find_project_root 认它为根
    return tmp_path


def _assert_index_consistent(idx) -> None:
    assert idx.num_docs == len(idx.documents), "num_docs 与 documents 数不一致"
    max_doc_id = max(
        (doc_id for postings in idx.inverted_index.values() for doc_id, _ in postings),
        default=-1,
    )
    assert max_doc_id < len(idx.documents), "倒排表 doc_id 越界 → 索引损坏"


def test_all_chunk_patterns_compile():
    """回归：_CHUNK_PATTERNS 的正则必须全部可编译。

    javascript.fn / java.fn 曾括号不平衡 → 对应语言文件一索引就 re.error 崩溃。
    """
    import re

    for _lang, (fn, comment) in rag._CHUNK_PATTERNS.items():
        re.compile(fn)      # 不抛 re.error 即通过
        re.compile(comment)
    re.compile(rag._DEFAULT_FN_PATTERN)
    re.compile(rag._DEFAULT_COMMENT_PATTERN)


def test_index_and_query(tmp_path):
    proj = _make_project(tmp_path)
    (proj / "alpha.py").write_text(
        "def alpha_func():\n    return 'alpha unique token'\n", encoding="utf-8"
    )
    rag.index_project(str(proj), force=True)
    out = rag.query_index("alpha_func", path=str(proj))
    assert "alpha.py" in out


def test_incremental_update_keeps_index_consistent(tmp_path):
    """回归：增量更新后倒排表 doc_id 必须与 documents 对齐，search 不崩。"""
    proj = _make_project(tmp_path)
    (proj / "a.py").write_text("def func_a():\n    return 'aaa'\n", encoding="utf-8")
    (proj / "b.py").write_text("def func_b():\n    return 'bbb'\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)

    # 修改 a.py → 触发增量重索引
    (proj / "a.py").write_text(
        "def func_a_renamed():\n    return 'ccc unique token here'\n", encoding="utf-8"
    )
    rag.index_project(str(proj), force=False)

    idx = rag._load_index(rag._find_project_root(str(proj)))
    _assert_index_consistent(idx)
    # 新名查得到（search 不抛异常），旧块已被替换
    assert "func_a_renamed" in rag.query_index("func_a_renamed", path=str(proj))


def test_min_js_glob_ignored(tmp_path):
    """回归：_IGNORE_FILES 的 glob（*.min.js）需真正生效，压缩文件不进索引。"""
    proj = _make_project(tmp_path)
    (proj / "app.js").write_text("function realCode() { return 'indexme'; }\n", encoding="utf-8")
    (proj / "vendor.min.js").write_text("function mininfied(){return 'skipme_token';}\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)
    idx = rag._load_index(rag._find_project_root(str(proj)))
    assert all("vendor.min.js" not in d.file_path for d in idx.documents)
    assert any("app.js" in d.file_path for d in idx.documents)


def test_incremental_handles_deleted_file(tmp_path):
    """删除文件后增量更新，索引仍一致且不含已删块。"""
    proj = _make_project(tmp_path)
    (proj / "keep.py").write_text("def keeper():\n    return 1\n", encoding="utf-8")
    (proj / "gone.py").write_text("def goner():\n    return 2\n", encoding="utf-8")
    rag.index_project(str(proj), force=True)

    (proj / "gone.py").unlink()
    rag.index_project(str(proj), force=False)

    idx = rag._load_index(rag._find_project_root(str(proj)))
    _assert_index_consistent(idx)
    assert all("gone.py" not in d.file_path for d in idx.documents)
