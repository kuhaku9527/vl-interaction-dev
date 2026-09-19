"""Named connection profiles — 后端持久化的「连接列表」(A2-b)。

背景与设计决策（2026-09-18）：
  原先「预设」存在浏览器 localStorage（``joyai.providers.<slot>``），
  换浏览器 / 清缓存即丢失，且用户反馈"忘了怎么保存"。
  现改为后端持久化的命名连接列表。

为什么是**独立文件** ``config/connections.json``（而非并入 services.json）：
  - services.json 只承载「当前生效的一份配置」，其加载路径
    （``_merge_services_config_file``）会**忽略未知槽位键**；
    把连接列表塞进去要么被忽略、要么被迫改动该校验逻辑。
  - 独立文件对现有 services.json **零侵入** —— 不改任何既有读写路径，
    因此没有数据迁移风险，回滚只需删掉本文件。

安全基线（E3，与 services.json 一致）：
  - ``chmod 0600`` + 位于 gitignore 的 ``config/`` 目录
  - **api_key 明文存储**，与 services.json 同一基线（不引入第二套加密，
    避免"一处加密一处明文"的不一致；读取时由调用方决定是否脱敏）
  - 不写日志明文

数据结构::

    {
      "version": 1,
      "connections": {
        "llm":       [ {"id","name","api_base","model","api_key","provider"} ],
        "summary":   [ ... ],
        "embedding": [ ... ],
        "asr":       [ ... ],
        "tts":       [ ... ]
      }
    }

每个连接必带稳定 ``id``（前端生成的 uuid 亦可），因为用户可重命名，
用 name 做主键会在重命名时丢引用。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

#: 允许存连接列表的槽位。与 services_config._SERVICES_CONFIG_DEFAULTS 对齐，
#: 但**排除 silence**（它不是服务连接）与 **agent**（agent 是 provider 二选一 +
#: gateway 地址，历史上不走「连接列表」语义；若将来需要，在此追加即可）。
_CONNECTION_SLOTS: tuple[str, ...] = ("llm", "summary", "embedding", "asr", "tts")

#: 单个连接允许的字段（白名单：拒绝未知键，避免脏数据写入）
_CONNECTION_FIELDS: tuple[str, ...] = ("name", "api_base", "model", "api_key", "provider")

#: 每个槽位最多保存的连接数（防御性上限，避免文件无限膨胀）
_MAX_PER_SLOT = 50

#: 字段长度上限（防御性）
_MAX_FIELD_LEN = 512


def connections_file_path() -> str:
    """``config/connections.json`` 的绝对路径（与 services.json 同目录）。"""
    from . import services_config as _sc

    services_path = os.path.abspath(_sc._SERVICES_CONFIG_PATH)
    return os.path.join(os.path.dirname(services_path), "connections.json")


def _empty() -> dict[str, Any]:
    return {"version": 1, "connections": {slot: [] for slot in _CONNECTION_SLOTS}}


def _sanitize_connection(raw: Any) -> dict[str, str] | None:
    """把一条原始记录收敛为白名单字段的字符串字典。

    返回 ``None`` 表示该条无效（丢弃而不是报错 —— 加载路径是 best-effort；
    真正的门禁在 PUT 时的 ``_validate_connections_payload``）。
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for key in _CONNECTION_FIELDS:
        val = raw.get(key)
        if isinstance(val, str):
            out[key] = val[:_MAX_FIELD_LEN]
    cid = raw.get("id")
    if isinstance(cid, str) and cid.strip():
        out["id"] = cid.strip()[:128]
    if not out.get("name"):
        # name 是必需的可读标识；缺失则退回用 id，再缺则丢弃
        if out.get("id"):
            out["name"] = out["id"]
        else:
            return None
    return out


def _sanitize(data: Any) -> dict[str, Any]:
    """把任意读入内容收敛为合法结构（丢弃非法项，不抛异常）。"""
    result = _empty()
    if not isinstance(data, dict):
        return result
    conns = data.get("connections")
    if not isinstance(conns, dict):
        return result
    for slot in _CONNECTION_SLOTS:
        rows = conns.get(slot)
        if not isinstance(rows, list):
            continue
        cleaned: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for raw in rows[:_MAX_PER_SLOT]:
            item = _sanitize_connection(raw)
            if not item:
                continue
            # id 去重（重复 id 会让前端「按 id 删除」误删）
            cid = item.get("id") or ""
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            cleaned.append(item)
        result["connections"][slot] = cleaned
    return result


def load_connections() -> dict[str, Any]:
    """读取连接列表。文件缺失/损坏时返回空结构（不抛异常）。

    与 ``services.json`` 的加载语义一致：失败只记日志并回退，绝不阻断启动。
    """
    path = connections_file_path()
    if not os.path.exists(path):
        logger.debug("no connections file at %s; starting empty", path)
        return _empty()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("could not read connections file %s: %s", path, exc)
        return _empty()
    return _sanitize(data)


def _persist(data: dict[str, Any]) -> None:
    """原子写 + chmod 0600（与 ``_persist_services_config`` 同一手法）。

    失败会向上抛：持久化是成功 PUT 的硬要求，不是 best-effort。
    """
    path = connections_file_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".connections-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        # 清理临时文件，避免目录里堆积半成品
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_connections(connections: dict[str, Any]) -> dict[str, Any]:
    """替换整个连接列表并落盘，返回写入后的规范化结构。"""
    cleaned = _sanitize({"connections": connections})
    _persist(cleaned)
    return cleaned


def validate_connections_payload(payload: Any) -> str | None:
    """校验 PUT 请求体。返回 ``None`` 表示通过，否则返回人类可读的原因。"""
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    conns = payload.get("connections")
    if not isinstance(conns, dict):
        return "connections must be an object keyed by slot"
    unknown = [k for k in conns if k not in _CONNECTION_SLOTS]
    if unknown:
        return "unknown slot(s): %s" % ", ".join(sorted(unknown)[:5])
    for slot, rows in conns.items():
        if not isinstance(rows, list):
            return "connections.%s must be an array" % slot
        if len(rows) > _MAX_PER_SLOT:
            return "connections.%s exceeds the %d-item limit" % (slot, _MAX_PER_SLOT)
        for i, raw in enumerate(rows):
            if not isinstance(raw, dict):
                return "connections.%s[%d] must be an object" % (slot, i)
            for key, val in raw.items():
                if key not in _CONNECTION_FIELDS and key != "id":
                    return "connections.%s[%d] has unknown field %r" % (slot, i, key)
                if not isinstance(val, str):
                    return "connections.%s[%d].%s must be a string" % (slot, i, key)
            if not (raw.get("name") or "").strip():
                return "connections.%s[%d] is missing name" % (slot, i)
    return None


__all__ = [
    "connections_file_path",
    "load_connections",
    "save_connections",
    "validate_connections_payload",
]
