"""Health/heartbeat access-log separation tests.

User feedback (2026-08-12): status-poll heartbeat lines (
/api/jarvis/status, /api/llm/status, /api/live/status,
/api/services/extended-status, /health, /v1/models) flooded webui.err.log and
drowned real events. The fix routes successful heartbeat polls to DEBUG
(visible with JOYAI_LOG_LEVEL=DEBUG) while real events stay INFO; heartbeat
failures (status >= 400) are still logged at INFO.
"""

from __future__ import annotations

import sys
from pathlib import Path

WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

from joy_interaction_webui.server import _is_heartbeat_path  # noqa: E402

SERVER_PY = WEBUI_SRC / "joy_interaction_webui" / "server.py"


def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


def test_heartbeat_status_paths_are_detected():
    assert _is_heartbeat_path("/api/jarvis/status") is True
    assert _is_heartbeat_path("/api/llm/status") is True
    assert _is_heartbeat_path("/api/live/status") is True
    assert _is_heartbeat_path("/api/services/status") is True
    assert _is_heartbeat_path("/api/services/extended-status") is True
    assert _is_heartbeat_path("/api/tts/health") is True
    assert _is_heartbeat_path("/health") is True
    assert _is_heartbeat_path("/v1/models") is True


def test_heartbeat_query_string_is_stripped():
    assert _is_heartbeat_path("/api/jarvis/status?session_id=abc") is True
    assert _is_heartbeat_path("/api/live/status?session_id=default") is True


def test_real_event_paths_are_not_heartbeat():
    assert _is_heartbeat_path("/") is False
    assert _is_heartbeat_path("/ws") is False
    assert _is_heartbeat_path("/offer") is False
    assert _is_heartbeat_path("/api/llm/message") is False
    assert _is_heartbeat_path("/api/tts/synthesize") is False
    assert _is_heartbeat_path("/api/live/start") is False
    assert _is_heartbeat_path("/api/jarvis/stop") is False
    assert _is_heartbeat_path("/api/session/cleanup") is False
    assert _is_heartbeat_path("/api/services/config") is False


def test_access_middleware_routes_heartbeat_to_debug():
    src = _server_source()
    # Heartbeat success -> DEBUG; everything else (and failures) -> INFO.
    assert "_is_heartbeat_path(request.path) and status < 400" in src
    assert "_access_logger.debug(line)" in src
    assert "_access_logger.info(line)" in src
    # The debug branch must come before the info branch (heartbeat wins).
    debug_idx = src.index("_access_logger.debug(line)")
    info_idx = src.index("_access_logger.info(line)")
    assert debug_idx < info_idx


def test_debug_level_restores_heartbeat_via_env():
    src = _server_source()
    # JOYAI_LOG_LEVEL gates both the root basicConfig and the access handler.
    assert 'os.environ.get("JOYAI_LOG_LEVEL", "INFO")' in src
    assert '_os_for_accesslog.environ.get("JOYAI_LOG_LEVEL", "INFO")' in src
    # The access handler must not hard-block DEBUG at the logger level.
    assert "_access_logger.setLevel(logging.DEBUG)" in src
