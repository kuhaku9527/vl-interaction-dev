# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Services config management (split out of server.py).

Owns the live in-memory services config (``_services_config``), its persistence
to ``config/services.json``, the PUT-time validation gates (format +
reachability) and the ADR-0014 config-change event log. Cross-module helpers
that the test suite patches through the server facade (``server._probe_*``,
``server._SERVICES_CONFIG_PATH``, ``server._asr_bridge_*``) are resolved
lazily at call time so the module-load graph stays acyclic.
"""

import asyncio
import copy
import datetime
import json
import logging
import os

from . import asr as asr_module

logger = logging.getLogger(__name__)


# Default in-memory services config. This is the base layer; any persisted
# file (``config/services.json``) is deep-merged ON TOP of these at startup
# (see ``_merge_services_config_file``) so the file only needs to override what
# differs from the defaults. Kept as a separate constant so a "restart" can be
# simulated by resetting to it and re-applying the file.
_SERVICES_CONFIG_DEFAULTS: dict = {
    "llm": {
        "api_base": "http://127.0.0.1:8070/v1",
        "model": "streaming-infer-adapter",
        "api_key": "",
    },
    # 2026-08-16: 摘要模型默认改 OpenRouter 免费视觉模型（N8）——
    # google/gemma-4-26b-a4b-it:free 实测图片摘要可用且 cost=0（真实免费），
    # 不再用 MiniMax-M3（省钱：摘要吃帧图，M3 计费）。前端可 PUT provider
    # 切回 minimax（api_base/model/api_key 联动覆盖）。
    # api_key 默认继承 webui 进程环境的 OPENROUTER_API_KEY（User env）。
    "summary": {
        "provider": "openrouter",
        "api_base": "https://openrouter.ai/api/v1",
        "model": "google/gemma-4-26b-a4b-it:free",
        "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
    },
    "tts": {"api_base": "http://127.0.0.1:8985/v1/synthesize", "model": "", "api_key": ""},
    "asr": {
        "api_base": "",
        "model": "D:/AI/models/sherpa-onnx/models/asr/streaming-paraformer-bilingual-zh-en",
        "api_key": "",
    },
    # 2026-08-15 (N7.1)：agent / embedding 槽位——前端可切 provider（同 summary 热切模式）。
    # agent: provider = codex|hermes（background-agent POST /v1/provider/route 热切，
    #   未知名 fail-loud -> 400；api_base/api_key 预留远程 agent）。
    "agent": {"provider": "codex", "api_base": "", "api_key": ""},
    # embedding: provider = local|siliconflow|nvidia（memory-store POST
    #   /v1/settings/embedding 热切）。默认 siliconflow = run-windows.env
    #   EMBEDDING_PROVIDER（云端 BAAI/bge-m3 实测免费可用）。
    "embedding": {
        "provider": "siliconflow",
        "api_base": "",
        "model": "",
        "api_key": "",
    },
    # 2026-08-17: 无线电静默 (Radio Silence) 设置槽位 (spec
    # radio-silence.md §3)。webui 持久化这 5 项设置到 services.json;
    # webinfer 拥有 live `suppressed` 状态位与 T1/T2 超时计时器。重启默认回
    # live 常驻--suppressed 位不持久化 (防"忘了开静默变哑巴", spec §2)。
    # 字段类型映射见 _SILENCE_FIELDS (merge + validate 共用)。默认值与
    # webinfer silence_control.DEFAULT_SILENCE_SETTINGS / 前端 radio_silence.js
    # 对齐 (auto_wake_minutes=30)。
    "silence": {
        "hotkey": "Ctrl+Shift+S",
        "asr_enabled": False,
        "kws_enabled": True,
        "timeout_hint_enabled": True,
        "timeout_hint_minutes": 15,
        "auto_wake_enabled": False,
        "auto_wake_minutes": 30,
    },
}

#: silence 槽位字段 -> 期望类型 (merge + validate 共用)。分钟字段显式排除
#: bool (Python 里 bool 是 int 子类, True 不能当分钟数存)。
_SILENCE_FIELDS: dict[str, type] = {
    "hotkey": str,
    "asr_enabled": bool,
    "kws_enabled": bool,
    "timeout_hint_enabled": bool,
    "timeout_hint_minutes": int,
    "auto_wake_enabled": bool,
    "auto_wake_minutes": int,
}

#: silence 分钟字段的合法区间 (1 分钟 ~ 24 小时)。T1 静默提示 / T2 自动唤醒
#: 两个计时器共用该区间; 超出即 400 (约法三章②: invalid 配置显式拒绝)。
_SILENCE_MINUTES_MIN = 1
_SILENCE_MINUTES_MAX = 1440

# Live, mutable services config — the single source of truth the webui owns.
_services_config: dict = copy.deepcopy(_SERVICES_CONFIG_DEFAULTS)

#: provider 字段白名单（N7.1 + N8）：只有这些槽位接受 ``provider`` 选择字段，
#: 且值必须 ∈ 集合（与各后端 provider 注册表可用名一致，D-080 提前拦截）。
_PROVIDER_CHOICES: dict[str, tuple[str, ...]] = {
    "agent": ("codex", "hermes"),
    "embedding": ("local", "siliconflow", "nvidia"),
    "summary": ("minimax", "openrouter"),
}


def _default_services_config_path() -> str:
    """Absolute path of the persisted services config (repo-root ``config/``)."""
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    return os.path.join(repo_root, "config", "services.json")


# Overridable in tests (monkeypatch before the PUT handler runs) so persistence
# can be exercised against a tmp_path instead of the real repo config dir.
_SERVICES_CONFIG_PATH = _default_services_config_path()


def _merge_services_config_file(target: dict, path: str) -> bool:
    """Deep-merge the on-disk ``services.json`` over ``target`` (in place).

    Only the known slots and known fields are merged: the api slots
    (``llm`` / ``summary`` / ``tts`` / ``asr`` / ``agent`` / ``embedding``)
    accept ``api_base`` / ``model`` / ``api_key`` / ``provider`` strings, and
    the ``silence`` slot accepts its 7 typed settings (see ``_SILENCE_FIELDS``,
    coerced by ``_merge_silence_slot``). Anything else is ignored so a
    partially-written or hand-edited file can never inject unexpected keys
    into the runtime config.

    Returns
    -------
    bool
        ``True`` if the file existed and parsed (even if empty / partial);
        ``False`` if it was absent.

    Notes
    -----
    Raises nothing — a missing or corrupt file must not abort webui startup;
    it simply falls back to the in-memory defaults (logging the reason). This
    is the load path, not the validation gate; PUT-time validation lives in
    ``_validate_and_apply_slot``.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("could not read services config file %s: %s", path, exc)
        return False
    if not isinstance(data, dict):
        logger.warning("services config file %s is not a JSON object; ignoring", path)
        return False
    for slot, slot_cfg in data.items():
        if slot not in _SERVICES_CONFIG_DEFAULTS:
            continue
        if not isinstance(slot_cfg, dict):
            continue
        dst = target.setdefault(slot, {})
        if slot == "silence":
            _merge_silence_slot(dst, slot_cfg)
            continue
        for key in ("api_base", "model", "api_key", "provider"):
            val = slot_cfg.get(key)
            if isinstance(val, str):
                dst[key] = val
    return True


def _merge_silence_slot(dst: dict, slot_cfg: dict) -> None:
    """Merge the typed silence-slot fields from a persisted file (in place).

    Only the 7 known fields are merged, coerced to their declared type so a
    hand-edited file cannot inject a wrong-typed value. Invalid / out-of-range
    values are ignored — the file load path is best-effort (PUT-time
    validation in ``_validate_and_apply_silence_slot`` is the real gate; a bad
    file just keeps the in-memory default).
    """
    for key, typ in _SILENCE_FIELDS.items():
        val = slot_cfg.get(key)
        if val is None:
            continue
        if typ is bool:
            if isinstance(val, bool):
                dst[key] = val
        elif typ is int:
            if isinstance(val, bool):
                continue
            try:
                parsed = int(val)
            except (TypeError, ValueError):
                continue
            if _SILENCE_MINUTES_MIN <= parsed <= _SILENCE_MINUTES_MAX:
                dst[key] = parsed
        elif isinstance(val, str):
            dst[key] = val


def _reload_services_config_from_file() -> None:
    """Reset to defaults and re-apply the persisted file.

    Used to simulate a webui restart in tests, and if ever needed, to force a
    re-read of ``config/services.json`` without a full process restart.
    """
    from . import server as _server

    _services_config.clear()
    _services_config.update(copy.deepcopy(_SERVICES_CONFIG_DEFAULTS))
    _merge_services_config_file(_services_config, _server._SERVICES_CONFIG_PATH)


def _persist_services_config() -> None:
    """Atomically write the current ``_services_config`` to ``services.json``.

    Writes to a temp file in the same directory then ``os.replace`` so a reader
    never observes a half-written file. The file is ``chmod 0600`` (local-only,
    gitignored) because it may carry ``api_key`` plaintext — see the api_key
    persistence tradeoff recorded in the issue. Raises on directory creation /
    write failure: persistence is a hard requirement of a successful PUT, not a
    best-effort nicety.
    """
    from . import server as _server

    path = os.path.abspath(_server._SERVICES_CONFIG_PATH)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(_services_config, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    os.chmod(path, 0o600)


def _validate_api_base(api_base: str) -> str | None:
    """Validate the ``api_base`` format.

    Returns ``None`` when the value is acceptable (empty string, meaning
    "use default / local", or a syntactically valid http(s) / ws(s) URL).
    Returns a human-readable reason string when the value must be rejected
    (HTTP 400).

    ws(s):// is allowed because the external ASR may be a websocket bridge
    (e.g. ``asr_adapter.py`` exposing ``/ws/asr``); the webui connects to it
    via ``aiohttp.ws_connect`` (see asr.connect_asr).
    """
    if not isinstance(api_base, str):
        return "api_base must be a string"
    if api_base != api_base.strip():
        return "api_base must not have leading/trailing whitespace"
    if not api_base:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(api_base)
    if parsed.scheme not in ("http", "https"):
        return "api_base must be empty or an http(s) URL"
    if not parsed.netloc:
        return "api_base is missing a host"
    return None


def _probe_result_ok(result: object) -> bool:
    """Normalize the heterogeneous probe return shapes into a single bool.

    ``_probe_summary`` / ``_probe_asr`` return ``{"ok": bool, ...}`` while
    ``_probe_llm`` / ``_probe_tts`` return ``{"status": "ok"|"error"|...}``.
    """
    if not isinstance(result, dict):
        return False
    if "ok" in result:
        return bool(result.get("ok"))
    if "status" in result:
        return result.get("status") == "ok"
    return False


def _probe_result_reason(result: object, default: str = "probe failed") -> str:
    """Extract a short, capped reason string from a probe result."""
    if not isinstance(result, dict):
        return default
    reason = result.get("reason") or default
    return str(reason)[:200]


async def _probe_slot(slot: str, proposed: dict, loop: asyncio.AbstractEventLoop) -> dict | None:
    """Run the reachability probe for one slot.

    Returns the probe result dict, or ``None`` when no probe applies (e.g. an
    HTTP slot whose ``api_base`` is empty). ASR is special: it probes either an
    http(s) ``api_base`` OR a local model directory, so it is always probed
    when reachability is in scope.
    """
    from . import server as _server

    api_base = proposed.get("api_base", "")
    if slot == "llm":
        return await loop.run_in_executor(None, _server._probe_llm, api_base)
    if slot == "summary":
        return await loop.run_in_executor(None, _server._probe_summary, {"api_base": api_base})
    if slot == "tts":
        return await loop.run_in_executor(None, _server._probe_tts, api_base)
    if slot == "asr":
        return await loop.run_in_executor(
            None, _server._probe_asr, {"api_base": api_base, "model": proposed.get("model", "")}
        )
    return None


async def _validate_and_apply_slot(
    slot: str, incoming: dict, loop: asyncio.AbstractEventLoop
) -> tuple[dict | None, bool]:
    """Validate and apply one incoming slot to ``_services_config``.

    Per 约法三章②, invalid config is rejected with an explicit structured body
    and is NEVER silently written. Only the fields the caller actually changes
    are validated / probed — the current persisted state is already trusted, so
    a no-op PUT never triggers a reachability probe.

    Parameters
    ----------
    slot: str
        Service slot (``llm`` / ``summary`` / ``tts`` / ``asr`` /
        ``agent`` / ``embedding`` / ``silence``).
    incoming: dict
        The ``{api_base, model, api_key}`` object for this slot from the PUT.
    loop: asyncio.AbstractEventLoop
        Event loop used to run the (sync) probes off the aiohttp loop.

    Returns
    -------
    tuple[dict | None, bool]
        ``(invalid_entry, applied)``. ``invalid_entry`` is the structured 4xx
        body fragment (including its own ``status``) when the slot is rejected,
        else ``None``. ``applied`` is ``True`` when at least one field was
        committed. Nothing is applied when the slot is rejected.
    """
    from . import server as _server

    if slot == "silence":
        # Typed settings slot (hotkey / bool toggles / minute ints) — no
        # api_base / provider fields, no reachability probe. Validated through
        # the same gate so invalid values are rejected, never persisted.
        return await _validate_and_apply_silence_slot(incoming)

    cur = _services_config.setdefault(slot, {})
    changing = [
        k
        for k in ("api_base", "model", "api_key", "provider")
        if k in incoming and incoming[k] != cur.get(k)
    ]
    if not changing:
        return None, False

    # 1) Format gate (before applying): api_base must be empty or a valid
    #    http(s) URL. Reject typos like "htp://" with a 400.
    if "api_base" in changing:
        fmt_err = _validate_api_base(incoming["api_base"])
        if fmt_err is not None:
            return (
                {
                    "error": fmt_err,
                    "slot": slot,
                    "field": "api_base",
                    "reason": fmt_err,
                    "status": 400,
                },
                False,
            )

    # 1.5) Provider gate (N7.1): agent/embedding 的 provider 必须 ∈ 白名单。
    #      各后端 provider 注册表本身也 fail-loud（D-080），这里提前 400
    #      拦截非法名，避免把坏配置写进 services.json。
    if "provider" in changing:
        choices = _PROVIDER_CHOICES.get(slot)
        if choices is None:
            return (
                {
                    "error": f"slot {slot!r} does not support a provider field",
                    "slot": slot,
                    "field": "provider",
                    "reason": "unsupported field",
                    "status": 400,
                },
                False,
            )
        if incoming["provider"].strip().lower() not in choices:
            return (
                {
                    "error": f"unknown {slot} provider {incoming['provider']!r}; "
                    f"expected one of: {', '.join(choices)}",
                    "slot": slot,
                    "field": "provider",
                    "reason": "unknown provider",
                    "status": 400,
                },
                False,
            )

    # 2) Reachability gate (before applying): probe the new endpoint. Never
    #    silently accept an unreachable service (no local fallback, D-080).
    #    For non-ASR slots an empty api_base means "use default / local" — it
    #    is valid and must NOT be probed.
    #    ASR: the user-facing api_base is an http(s) provider URL. When set we
    #    must bring the internal bridge up BEFORE the probe (and stop it when
    #    the slot reverts to local). Empty api_base = local in-process (no probe).
    proposed_api_base = incoming.get("api_base", cur.get("api_base", ""))
    if slot == "asr" and changing:
        proposed_model = incoming.get("model", cur.get("model", ""))
        proposed_key = incoming.get("api_key", cur.get("api_key", ""))
        # Bridge start/stop is a blocking subprocess op (up to 15s readiness
        # poll). Run it off the aiohttp event loop so saving a cloud ASR config
        # never freezes the whole WebUI. Per code-review BLOCKING fix.
        if proposed_api_base:
            await loop.run_in_executor(
                None, _server._asr_bridge_ensure, proposed_api_base, proposed_model, proposed_key
            )
        else:
            await loop.run_in_executor(None, _server._asr_bridge_stop)
    reachability_in_scope = (
        (("api_base" in changing) and bool(proposed_api_base))
        if slot != "asr"
        else bool(proposed_api_base) and proposed_api_base.startswith(("http://", "https://"))
    )
    if reachability_in_scope:
        proposed = {
            "api_base": proposed_api_base,
            "model": incoming.get("model", cur.get("model", "")),
        }
        result = await _probe_slot(slot, proposed, loop)
        if result is not None and not _probe_result_ok(result):
            reason = _probe_result_reason(result, "service unreachable")
            field = "model" if (slot == "asr" and not proposed["api_base"]) else "api_base"
            return (
                {
                    "error": "service unreachable: %s" % reason,
                    "slot": slot,
                    "field": field,
                    "reason": reason,
                    "status": 422,
                },
                False,
            )

    # 3) Valid -> apply the change and audit-log it (ADR-0014 redaction).
    changed_fields = []
    redacted = {}
    for key in ("api_base", "model", "api_key", "provider"):
        if key in incoming and incoming[key] != cur.get(key):
            # provider 规范化存储（strip + lower），保证内存态与白名单一致，
            # 前端下拉回显不会出现 '  HERMES ' 这种不匹配 option 的值。
            cur[key] = incoming[key].strip().lower() if key == "provider" else incoming[key]
            changed_fields.append(key)
            if key == "api_key":
                redacted["api_key"] = "***set***" if incoming[key] else "***cleared***"
            else:
                redacted[key] = incoming[key]
    if changed_fields:
        _log_config_change(slot, changed_fields, redacted)
    return None, bool(changed_fields)


async def _validate_and_apply_silence_slot(incoming: dict) -> tuple[dict | None, bool]:
    """Validate + apply one incoming silence-settings patch.

    The silence slot carries 7 typed fields (hotkey string, 4 bool toggles,
    2 minute ints). Per 约法三章②, a wrong-typed / out-of-range value is
    rejected with an explicit structured 400 and is NEVER applied or
    persisted. Only fields actually present in ``incoming`` are validated —
    a partial patch (e.g. just ``{"kws_enabled": False}``) leaves the rest
    untouched, matching the other slots' incremental semantics.

    Parameters
    ----------
    incoming: dict
        The ``{hotkey?, asr_enabled?, ...}`` object for the silence slot.

    Returns
    -------
    tuple[dict | None, bool]
        ``(invalid_entry, applied)`` — same contract as
        ``_validate_and_apply_slot``: ``invalid_entry`` is the structured 4xx
        body fragment when a field is rejected, else ``None``; ``applied`` is
        ``True`` when at least one field was committed.
    """
    cur = _services_config.setdefault("silence", {})
    changed_fields: list[str] = []
    redacted: dict = {}
    for key, typ in _SILENCE_FIELDS.items():
        if key not in incoming:
            continue
        val = incoming[key]
        # bool is an int subclass in Python; minutes must reject True/False.
        if typ is int and isinstance(val, bool):
            return (
                {
                    "error": f"silence.{key} must be an integer",
                    "slot": "silence",
                    "field": key,
                    "reason": "expected int, got bool",
                    "status": 400,
                },
                False,
            )
        if not isinstance(val, typ):
            return (
                {
                    "error": f"silence.{key} must be a {typ.__name__}",
                    "slot": "silence",
                    "field": key,
                    "reason": f"expected {typ.__name__}, got {type(val).__name__}",
                    "status": 400,
                },
                False,
            )
        if typ is int and not (_SILENCE_MINUTES_MIN <= val <= _SILENCE_MINUTES_MAX):
            return (
                {
                    "error": f"silence.{key} must be between {_SILENCE_MINUTES_MIN} and "
                    f"{_SILENCE_MINUTES_MAX} minutes",
                    "slot": "silence",
                    "field": key,
                    "reason": "minutes out of range",
                    "status": 400,
                },
                False,
            )
        if key == "hotkey" and not val.strip():
            return (
                {
                    "error": "silence.hotkey must be a non-empty hotkey string",
                    "slot": "silence",
                    "field": key,
                    "reason": "empty hotkey",
                    "status": 400,
                },
                False,
            )
        if val == cur.get(key):
            continue
        cur[key] = val.strip() if key == "hotkey" else val
        changed_fields.append(key)
        redacted[key] = val
    if changed_fields:
        _log_config_change("silence", changed_fields, redacted)
    return None, bool(changed_fields)


def _log_config_change(slot, changed_fields, redacted_values, events_dir=None):
    """Append one config_change event to the per-service JSONL event stream.

    Aligns with ADR-0014 (``doc/adr/0014-log-event-schema.md``): writes one
    JSON object per line to ``logs/events/webui-<UTC-YYYY-MM-DD>.jsonl`` with
    the four required fields — ``ts`` (ISO-8601 UTC), ``level`` ∈
    {debug,info,warn,error,critical}, ``service`` (``"webui"``) and ``event``
    (kebab-case ``config.services.patch``). The original ``slot`` /
    ``changed_fields`` / ``redacted_values`` are carried inside the optional
    ``extra`` object.

    PII red line (spec S-1 / D-2026-08-01-061): api_key is NEVER written in
    plaintext — the caller already replaces it with ``***set***`` /
    ``***cleared***``, so this helper only persists what it is given.

    Parameters
    ----------
    slot: str
        Service slot that changed (llm / summary / tts / asr).
    changed_fields: list[str]
        Names of the fields that actually changed.
    redacted_values: dict
        Field -> redacted representation. Non-secret fields (api_base, model)
        may carry their plaintext; api_key must be redacted.
    events_dir: str | None
        Override for the events directory (used by tests to redirect output).
        Defaults to ``<repo>/logs/events``.
    """
    try:
        if events_dir is None:
            repo_logs = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "..",
                "..",
                "..",
                "logs",
            )
            events_dir = os.path.join(repo_logs, "events")
        os.makedirs(events_dir, exist_ok=True)
        utc_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        log_path = os.path.join(events_dir, "webui-%s.jsonl" % utc_date)
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": "info",
            "service": "webui",
            "event": "config.services.patch",
            "extra": {
                "slot": slot,
                "changed_fields": list(changed_fields),
                "redacted_values": dict(redacted_values),
            },
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("failed to append config-change event: %s", exc)


if _merge_services_config_file(_services_config, _SERVICES_CONFIG_PATH):
    logger.info("loaded persisted services config from %s", _SERVICES_CONFIG_PATH)
else:
    logger.debug("no persisted services config at %s; using defaults", _SERVICES_CONFIG_PATH)

# Feed the live service config to the ASR module so it can hot-reload the
# external ASR url/api_key without a process restart (see asr.connect_asr).
asr_module.set_asr_config_source(_services_config)
