"""session.py 测试 — 会话保存/加载/删除。"""

import contextlib
import json
import os

from src.session import (
    _sanitize_surrogates,
    _session_preview,
    delete_session,
    get_session_name,
    get_sessions_info,
    list_sessions,
    load_session,
    save_session,
    save_session_name,
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


class TestNameAndPreview:
    def test_rename_roundtrips(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "hi"}], session_id="s")
        save_session_name("s", "世界模型讨论")
        assert get_session_name("s") == "世界模型讨论"
        assert get_sessions_info(["s"])["s"]["name"] == "世界模型讨论"

    def test_rename_empty_clears(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "hi"}], session_id="s")
        save_session_name("s", "临时名")
        save_session_name("s", "  ")           # 空 → 清除，回退 sid
        assert get_session_name("s") == "s"

    def test_preview_falls_back_to_first_user_msg(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([
            {"role": "system", "content": "注入"},
            {"role": "user", "content": "世界模型评测基准有哪些？"},
        ], session_id="s")
        assert "世界模型评测基准" in _session_preview("s")

    def test_preview_prefers_summary(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session.SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "原始问题"}], session_id="s")
        with open(os.path.join(str(tmp_path), "s", "summary.txt"), "w", encoding="utf-8") as f:
            f.write("这次聊了世界模型仿真\n更多细节")
        assert _session_preview("s") == "这次聊了世界模型仿真"


class TestAtomicWrite:
    """save_session 原子写：写盘中途崩溃绝不留下截断的 messages.json。"""

    def test_crash_mid_write_keeps_old_intact(self, tmp_path, monkeypatch):
        """os.replace 崩溃（模拟换名前进程死）→ 原有完整存档不被破坏。"""
        import src.session as sess
        monkeypatch.setattr(sess, "SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "good"}], session_id="s")

        def boom(*a, **k):
            raise RuntimeError("simulated crash before rename")

        monkeypatch.setattr(sess.os, "replace", boom)
        with contextlib.suppress(RuntimeError):
            save_session([{"role": "user", "content": "new-but-crashes"}], session_id="s")
        # 旧存档仍然完整可读，绝无截断
        msgs = load_session("s")
        assert msgs == [{"role": "user", "content": "good"}]

    def test_no_tmp_files_left_after_success(self, tmp_path, monkeypatch):
        import src.session as sess
        monkeypatch.setattr(sess, "SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "x"}], session_id="s")
        leftovers = [p for p in os.listdir(os.path.join(str(tmp_path), "s"))
                     if p.startswith(".tmp-")]
        assert leftovers == []

    def test_crash_leaves_no_tmp_file(self, tmp_path, monkeypatch):
        import src.session as sess
        monkeypatch.setattr(sess, "SESSION_DIR", str(tmp_path))
        save_session([{"role": "user", "content": "good"}], session_id="s")

        def boom(*a, **k):
            raise RuntimeError("crash before rename")

        monkeypatch.setattr(sess.os, "replace", boom)
        with contextlib.suppress(RuntimeError):
            save_session([{"role": "user", "content": "y"}], session_id="s")
        leftovers = [p for p in os.listdir(os.path.join(str(tmp_path), "s"))
                     if p.startswith(".tmp-")]
        assert leftovers == []
