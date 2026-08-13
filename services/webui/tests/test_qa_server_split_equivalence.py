# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""QA regression: server.py split (batch 5) — mechanical-equivalence checks.

This test file verifies (with fresh subprocess probes, NOT importing the live
modules) that the functions moved out of server.py into the 8 new modules are
mechanically identical to the pre-split implementations, after normalizing the
documented refactor patterns:

1. ``from . import server as _server`` added at function top (lazy resolution).
2. Global references rewritten to ``_server.<name>`` (facade-based resolution).
3. ``send_to_session(...)`` inside notify_* -> ``_server_send_to_session(...)``
   (which itself resolves through the server facade).
4. Module-level constants moved to the new module (values must be equal).

The baseline is the server.py snapshot at the parent of commit 5c0089e
(retrieved via ``git show``), so this test pins the "zero behavior change"
claim at the statement level.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO_ROOT / "services" / "webui" / "src" / "joy_interaction_webui"

# Baseline: server.py just before batch-5 split (parent of 5c0089e).
BASELINE = "5c0089e~1"


def _git_show_server_baseline():
    """Return the pre-split server.py source (via git show)."""
    out = subprocess.run(
        ["git", "show", f"{BASELINE}:services/webui/src/joy_interaction_webui/server.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.fixture(scope="module")
def baseline_server():
    return _git_show_server_baseline()


def _extract_defs(source, names):
    """Extract top-level FunctionDef bodies keyed by name from source."""
    tree = ast.parse(source)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            out[node.name] = ast.get_source_segment(source, node)
    return out


def _normalize(text):
    """Normalize documented refactor patterns for comparison."""
    # Remove the lazy facade import line added at function top.
    text = re.sub(r"^[ \t]*from \. import server as _server\n", "", text, flags=re.M)
    # _server.X -> X (facade resolution is the documented pattern)
    text = re.sub(r"\b_server\.", "", text)
    # _server_send_to_session(session_id, payload) -> send_to_session(...) where
    # the json.dumps is absorbed inside the helper (semantically identical).
    text = text.replace(
        "_server_send_to_session(session_id, payload)",
        "send_to_session(session_id, json.dumps(payload, ensure_ascii=False))",
    )
    # _probe_asr caches ASR_BRIDGE_HTTP into a local for the lazy facade read.
    text = text.replace("bridge_http = ASR_BRIDGE_HTTP", "")
    text = re.sub(r"\bbridge_http\b", "ASR_BRIDGE_HTTP", text)
    # Collapse ALL whitespace so pure reformatting (ruff line-wrapping) is
    # ignored while statement order / tokens stay compared.
    text = re.sub(r"\s+", "", text)
    return text


# name -> module file it now lives in (or None if it must remain in server).
MOVED = {
    # ws_notify
    "_safe_send_str": "ws_notify.py",
    "send_to_session": "ws_notify.py",
    "notify_session_json": "ws_notify.py",
    "notify_session_llm_reply": "ws_notify.py",
    "notify_session_tts_sentence": "ws_notify.py",
    "notify_session_pilot_utterance": "ws_notify.py",
    "notify_session_asr_partial": "ws_notify.py",
    "handle_background_handoff_for_interaction": "ws_notify.py",
    "get_session_callback": "ws_notify.py",
    "_safe_send_str_all": "ws_notify.py",
    "broadcast_text_update": "ws_notify.py",
    # service_probe
    "_probe_llm": "service_probe.py",
    "_probe_tts": "service_probe.py",
    "_probe_kws": "service_probe.py",
    "_probe_summary": "service_probe.py",
    "_probe_asr": "service_probe.py",
    "_now": "service_probe.py",
    "_resolve_service_targets": "service_probe.py",
    "llm_status": "service_probe.py",
    "tts_health": "service_probe.py",
    # tts_endpoint
    "_wav_chunk_header": "tts_endpoint.py",
    "build_tts_synthesize_payload": "tts_endpoint.py",
    "_tts_synthesize_handler": "tts_endpoint.py",
    # ws_handler
    "websocket_handler": "ws_handler.py",
    # asr_bridge
    "_asr_bridge_ensure": "asr_bridge.py",
    "_asr_bridge_sync": "asr_bridge.py",
    "_asr_bridge_wait_ready": "asr_bridge.py",
    "_asr_bridge_stop": "asr_bridge.py",
    # webinfer_proxy
    "_webinfer_base_url": "webinfer_proxy.py",
    "_webinfer_proxy_summarizer_routing": "webinfer_proxy.py",
    "_webinfer_summarizer_route_handler": "webinfer_proxy.py",
    # services_config
    "_default_services_config_path": "services_config.py",
    "_merge_services_config_file": "services_config.py",
    "_persist_services_config": "services_config.py",
    "_reload_services_config_from_file": "services_config.py",
    "_validate_and_apply_slot": "services_config.py",
    "_validate_api_base": "services_config.py",
    "_log_config_change": "services_config.py",
    "_probe_result_ok": "services_config.py",
    "_probe_result_reason": "services_config.py",
    "_probe_slot": "services_config.py",
    # NOTE: _services_config_handler / _services_status_handler / extended_status
    # landed in admin_endpoints.py (final batch) — listed there below.
    # admin_endpoints
    "_append_screen_latency": "admin_endpoints.py",
    "_ingest_text_handler": "admin_endpoints.py",
    "_propagate_services_to_runtime": "admin_endpoints.py",
    "_proxy_to_memory_store": "admin_endpoints.py",
    "_screen_latency_handler": "admin_endpoints.py",
    "_services_config_handler": "admin_endpoints.py",
    "_services_status_handler": "admin_endpoints.py",
    "extended_status": "admin_endpoints.py",
}


# Functions that intentionally diverge from the pre-split baseline because of
# a LATER bugfix (not part of the mechanical split). For each entry the strict
# equality claim is waived, but the test still pins that no existing WS message
# branch was removed (the fix may only ADD handling).
_BUGFIX_DIVERGED = {
    "websocket_handler": (
        "audit-frontend-2026-08-13 P1-4/P1-5: update_frames_per_batch now "
        "writes back the new value (previously only echoed the old one); "
        "update_background_config gained a backend handler (previously no "
        "backend processor existed)."
    ),
}


def _extract_message_branches(func_source: str) -> set[str]:
    """Collect the WS message type literals compared as ``t == \"...\"``."""
    tree = ast.parse(func_source)
    types: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "t"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and isinstance(node.comparators[0].value, str)
        ):
            types.add(node.comparators[0].value)
    return types


@pytest.mark.parametrize("name,module", sorted(MOVED.items()))
def test_moved_function_mechanically_equivalent(baseline_server, name, module):
    """Moved function body matches pre-split implementation after normalization."""
    module_path = WEBUI_SRC / module
    assert module_path.exists(), f"module {module} missing"
    new_source = module_path.read_text(encoding="utf-8")

    old = _extract_defs(baseline_server, {name}).get(name)
    new = _extract_defs(new_source, {name}).get(name)
    assert old is not None, f"{name} not found in pre-split server.py"
    assert new is not None, f"{name} not found in {module}"

    if name in _BUGFIX_DIVERGED:
        # Legitimate post-split bugfix: the body diverges from the pre-split
        # baseline, so the equality claim is waived. We still pin that NO
        # existing WS message branch was dropped — the fix is additive.
        missing_branches = _extract_message_branches(old) - _extract_message_branches(new)
        assert not missing_branches, (
            f"function {name} dropped WS message branch(es): {sorted(missing_branches)}\n"
            f"reason for divergence: {_BUGFIX_DIVERGED[name]}"
        )
        return

    assert _normalize(old) == _normalize(new), (
        f"function {name} differs from pre-split implementation:\n"
        f"--- OLD ---\n{_normalize(old)}\n--- NEW ---\n{_normalize(new)}"
    )


CONSTANTS = {
    "_LLM_PROBE_CACHE": "service_probe.py",
    "_LLM_PROBE_TTL_S": "service_probe.py",
    "_last_asr_propagated": "admin_endpoints.py",
}


def _extract_const(source, name):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.get_source_segment(source, node)
    return None


@pytest.mark.parametrize("name,module", sorted(CONSTANTS.items()))
def test_moved_constants_equal(baseline_server, name, module):
    new_source = (WEBUI_SRC / module).read_text(encoding="utf-8")
    old = _extract_const(baseline_server, name)
    new = _extract_const(new_source, name)
    assert old is not None
    assert new is not None
    assert _normalize(old) == _normalize(new), f"constant {name} differs"


# Internal-only helpers (no external callers) legitimately do NOT need a
# server.py facade re-export.
_INTERNAL_ONLY = {
    "_safe_send_str",
    "_safe_send_str_all",
    "handle_background_handoff_for_interaction",
}


def test_server_facade_reexports_all_moved_symbols():
    """server.py must re-export every moved callable (as-x facade)."""
    server_src = (WEBUI_SRC / "server.py").read_text(encoding="utf-8")
    missing = []
    for name, module in MOVED.items():
        if name in _INTERNAL_ONLY:
            continue
        mod = module.replace(".py", "")
        # re-exported via `from .X import ( ... name ... )` (possibly multiline)
        in_facade = re.search(
            rf"from \.{mod} import \(.*?\b{re.escape(name)}\b.*?\)", server_src, flags=re.S
        ) or re.search(rf"from \.{mod} import [^(\n]*\b{re.escape(name)}\b", server_src)
        if not in_facade and not re.search(
            rf"^def {name}\b|^async def {name}\b", server_src, flags=re.M
        ):
            missing.append(f"{name} (expected in {module})")
    assert not missing, f"server.py does not re-export: {missing}"


def test_no_reverse_import_in_jarvis_session():
    """jarvis_session must not import notify_* from .server anymore."""
    src = (WEBUI_SRC / "jarvis_session.py").read_text(encoding="utf-8")
    assert "from .server import notify" not in src
    # the 4 notify callbacks must come from ws_notify
    assert src.count("from .ws_notify import notify_") == 4
