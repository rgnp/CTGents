"""session.py 测试 — 会话保存/加载/删除。"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.session import (
    _sanitize_surrogates,
    delete_session,
    get_session_name,
    list_sessions,
    load_session,
    save_session,
)


class TestSanitizeSurrogates:
    def test_clean_string(self):
        assert _sanitize_surrogates("hello") == "hello"

    def test_surrogate_in_string(self):
        """U+D800 代理字符被替换。"""
        bad = "hello\ud800world"
        result = _sanitize_surrogates(bad)
        assert "\ud800" not in result

    def test_nested_dict(self):
        obj = {"a": "hello\ud800", "b": {"c": "world"}}
        result = _sanitize_surrogates(obj)
        assert "\ud800" not in result["a"]
        assert result["b"]["c"] == "world"

    def test_nested_list(self):
        obj = ["hello\ud800", ["world"]]
        result = _sanitize_surrogates(obj)
        assert "\ud800" not in result[0]

    def test_non_str_passthrough(self):
        assert _sanitize_surrogates(42) == 42


class TestSessionIO:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        sid = save_session([{"role": "user", "content": "hi"}], session_id="test1")
        assert sid == "test1"
        msgs = load_session("test1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hi"

    def test_save_auto_generates_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        sid = save_session([{"role": "user", "content": "hi"}])
        assert sid is not None
        assert "-" in sid

    def test_save_filters_volatile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([
            {"role": "user", "content": "keep"},
            {"role": "system", "content": "temp", "_volatile": True},
        ], session_id="test2")
        msgs = load_session("test2")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "keep"

    def test_list_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "a"}], session_id="s1")
        save_session([{"role": "user", "content": "b"}], session_id="s2")
        sessions = list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_delete_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "x"}], session_id="del_me")
        assert "del_me" in list_sessions()
        delete_session("del_me")
        assert "del_me" not in list_sessions()

    def test_get_session_name_no_meta(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "x"}], session_id="noname")
        name = get_session_name("noname")
        assert name is not None

    def test_get_session_name_with_meta(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "x"}], session_id="named")
        # 写 meta.json
        meta_dir = os.path.join(str(tmp_path), "named")
        with open(os.path.join(meta_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "My Session"}, f)
        name = get_session_name("named")
        assert name == "My Session"
