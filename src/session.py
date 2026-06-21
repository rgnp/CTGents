import json
import os
from datetime import datetime

from .config import SESSION_DIR


def list_sessions() -> list[str]:
    """列出所有历史会话 ID，按时间倒序。"""
    os.makedirs(SESSION_DIR, exist_ok=True)
    dirs = sorted(
        (d for d in os.listdir(SESSION_DIR) if os.path.isdir(os.path.join(SESSION_DIR, d))),
        reverse=True,
    )
    return dirs


def _session_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, session_id)


def _messages_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "messages.json")


def _meta_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "meta.json")


def _prefix_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "prefix.json")


def get_session_name(session_id: str) -> str:
    """获取会话名称，未设置则返回会话 ID。"""
    try:
        with open(_meta_path(session_id), encoding="utf-8") as f:
            meta = json.load(f)
            return meta.get("name", session_id)
    except Exception:
        return session_id


def list_session_names(session_ids: list[str]) -> dict[str, str]:
    """批量获取会话名称，一次扫描全部 meta.json。

    比逐个调 get_session_name（N 次文件开）快得多。
    """
    result = {}
    for sid in session_ids:
        try:
            with open(_meta_path(sid), encoding="utf-8") as f:
                meta = json.load(f)
                result[sid] = meta.get("name", sid)
        except Exception:
            result[sid] = sid
    return result




def get_sessions_info(session_ids: list[str]) -> dict[str, dict]:
    """批量获取会话信息：名字 + 格式化的日期/时间。

    返回 {sid: {"name": str, "date": str, "time": str}}
    date 为空串表示"今天"，date 非空表示"月-日"。
    """
    now = datetime.now()
    result: dict[str, dict] = {}
    for sid in session_ids:
        info: dict[str, str] = {"name": sid, "date": "", "time": ""}
        # 名字：从 meta.json 读取
        try:
            with open(_meta_path(sid), encoding="utf-8") as f:
                meta = json.load(f)
                info["name"] = meta.get("name", sid)
        except Exception:
            pass
        # 时间：从 session ID（时间戳）解析
        try:
            dt = datetime.strptime(sid, "%Y-%m-%d-%H%M%S")
            if dt.date() == now.date():
                info["time"] = dt.strftime("%H:%M")
            else:
                info["date"] = dt.strftime("%m-%d")
                info["time"] = dt.strftime("%H:%M")
        except ValueError:
            # 回退到文件修改时间
            try:
                mtime = os.path.getmtime(_messages_path(sid))
                dt = datetime.fromtimestamp(mtime)
                if dt.date() == now.date():
                    info["time"] = dt.strftime("%H:%M")
                else:
                    info["date"] = dt.strftime("%m-%d")
                    info["time"] = dt.strftime("%H:%M")
            except Exception:
                pass
        result[sid] = info
    return result


def delete_session(session_id: str) -> None:
    """删除指定会话目录。"""
    import shutil
    path = _session_path(session_id)
    if os.path.isdir(path):
        shutil.rmtree(path)


def _sanitize_surrogates(obj):
    """递归替换字符串中的孤立代理字符（U+D800-U+DFFF）。

    Windows 上 subprocess 管道可能因编码不匹配产生代理字符，
    这些字符无法被 UTF-8 编码，会导致 json.dump 失败。
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


def save_session(messages: list[dict], session_id: str | None = None) -> str:
    """保存会话。不传 session_id 则自动生成。返回 session_id。"""
    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    sess_dir = _session_path(session_id)
    os.makedirs(sess_dir, exist_ok=True)

    # 过滤掉运行时注入的易变消息（环境上下文等）
    persist = [m for m in messages if not m.get("_volatile")]

    with open(_messages_path(session_id), "w", encoding="utf-8") as f:
        json.dump(_sanitize_surrogates(persist), f, ensure_ascii=False, indent=2)

    return session_id


def load_session(session_id: str) -> list[dict]:
    """加载会话消息。"""
    with open(_messages_path(session_id), encoding="utf-8") as f:
        return json.load(f)


def save_prefix(session_id: str, prefix_msgs: list[dict]) -> None:
    """持久化会话的冻结前缀（缓存前缀字节）。

    前缀整段是 _volatile（date/AGENTS.md/记忆索引），会被 save_session 过滤掉，
    从不落盘——导致每次重载都从当前磁盘重新生成前缀。一旦 AGENTS.md 或记忆文件
    在会话之间变动（agent 自改、收割重写档案），重建出的前缀字节就和上一进程
    DeepSeek 服务端缓存的前缀不一致，整段前缀作废、命中暴跌到 0。

    这里把前缀单独存进 prefix.json，下次加载原样复用（见 load_prefix），让同一会话
    跨重启复用同一份前缀字节、服务端缓存保持热。内容不变则跳过写盘（autosave 高频
    调用不产生磁盘抖动）。
    """
    sess_dir = _session_path(session_id)
    os.makedirs(sess_dir, exist_ok=True)
    data = json.dumps(_sanitize_surrogates(prefix_msgs), ensure_ascii=False, indent=2)
    path = _prefix_path(session_id)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == data:
                return  # 未变化，跳过写盘
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def load_prefix(session_id: str) -> list[dict] | None:
    """读取会话落盘的冻结前缀；不存在（新会话 / 旧存档）返回 None，调用方回退现生成。"""
    try:
        with open(_prefix_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
