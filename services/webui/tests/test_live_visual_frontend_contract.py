"""C.B live visual (spec live-visual-cb.md §3 层 3) front-end static contracts.

Pins the live-panel additions in ``index.html`` + ``styles.css``:

  * a 开画面 (live-video) button with a single-select source (screen/camera);
  * screen capture reuses ``screen_capture.js`` over the SAME main websocket
    so ``server.py``'s ``frame`` handler forwards frames to the live session
    ring buffer (live WS = the main websocket, no new transport);
  * the camera source ships WS ``frame`` messages (``source: 'camera'``) over
    the same websocket — the backend treats any ``frame`` identically;
  * the 主动搭话 switch calls ``POST /api/live/proactive`` (runtime switch);
  * leaving live mode stops the live visual capture;
  * the status poll keeps ``proactive_supported`` / ``proactive_enabled`` in
    sync so the switch is disabled with an env hint when
    ``LIVE_PROACTIVE_ENABLED`` is off.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static" / "index.html"
STYLES_CSS = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static" / "styles.css"


# Batch-3 split: index.html's inline script#2 was extracted into standalone JS
# files (same dependency order as the <script src> tags). Assertions run against
# the combined sources so moved code keeps its contract with unchanged semantics.
SPLIT_JS = (
    "app_boot.js",
    "app_main.js",
    "sidebar_toggle.js",
    "incremental_wiring.js",
    "vlm_history.js",
    "llm_reply_ui.js",
    "ws_dispatcher.js",
    "vlm_render.js",
    "background_rich.js",
    "tts_player.js",
    "speech_input.js",
    "live_ui.js",
    "llm_reply_audio.js",
    "status_poll.js",
)


def _index_html() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    for name in SPLIT_JS:
        parts.append((INDEX_HTML.parent / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _styles_css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", html, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


def test_live_video_controls_exist():
    html = _index_html()

    assert 'id="liveVideoBtn"' in html
    assert "开画面" in html
    assert 'id="liveVideoSource"' in html
    assert 'value="screen"' in html
    assert 'value="camera"' in html
    # Single-select source (spec §4 边界: screen OR camera, never both).
    assert 'aria-label="画面来源"' in html


def test_live_proactive_switch_exists_default_off():
    html = _index_html()

    assert 'id="liveProactiveToggle"' in html
    assert "主动搭话" in html
    # Default off + disabled until live mode is active (mirrors liveEnrollBtn).
    assert 'id="liveProactiveToggle" aria-label="主动搭话" disabled' in html
    assert "let liveProactiveSupported = false" in html


def test_live_video_button_wired_to_start_stop():
    html = _index_html()
    idx = html.index("liveVideoBtn.addEventListener('click'")
    snippet = html[idx : idx + 400]
    assert "startLiveVideo()" in snippet
    assert "stopLiveVideoCapture()" in snippet


def test_live_video_screen_reuses_screen_capture_over_main_ws():
    body = _function_body(_index_html(), "startLiveVideo")

    assert "window.startScreenCapture(window.websocket, { fps: 1 })" in body
    assert "window.getScreenCaptureStream" in body
    # Single select: screen OR camera, never both.
    assert "source === 'camera'" in body
    # Frames travel over the live session's WS = the main websocket.
    assert "window.websocket" in body


def test_live_camera_ships_frame_over_main_ws():
    body = _function_body(_index_html(), "startLiveCameraCapture")

    assert "getUserMedia" in body
    assert "type: 'frame'" in body
    assert "source: 'camera'" in body
    assert "window.websocket.send(JSON.stringify(" in body
    assert "frame_seq: liveCameraFrameSeq" in body
    assert "toDataURL('image/jpeg', 0.92)" in body


def test_live_stop_cleans_capture():
    # stopLiveVideoCapture stops the owned screen capture + the camera sender.
    body = _function_body(_index_html(), "stopLiveVideoCapture")
    assert "window.stopScreenCapture" in body
    assert "stopLiveCameraCapture()" in body
    assert "liveVideoOwned" in body

    # Leaving live mode triggers the cleanup inside setLiveModeActive(false).
    set_body = _function_body(_index_html(), "setLiveModeActive")
    assert "stopLiveVideoCapture()" in set_body
    assert "!liveModeActive" in set_body


def test_proactive_switch_calls_api():
    body = _function_body(_index_html(), "toggleLiveProactive")

    assert "'/api/live/proactive'" in body
    assert "session_id: sessionId" in body
    assert "enabled" in body
    assert "data.applied === false" in body
    assert "LIVE_PROACTIVE_ENABLED=true" in body


def test_proactive_status_sync():
    body = _function_body(_index_html(), "renderLiveStatus")

    assert "payload.proactive_supported" in body
    assert "payload.proactive_enabled" in body
    assert "setLiveProactiveUiState()" in body


def test_proactive_env_gate_disables_switch():
    body = _function_body(_index_html(), "setLiveProactiveUiState")

    assert "liveModeActive && liveProactiveSupported" in body
    assert "LIVE_PROACTIVE_ENABLED=true" in body


def test_live_start_response_seeds_proactive_support():
    body = _function_body(_index_html(), "startLiveMode")

    assert "startData.proactive_supported" in body


def test_live_video_styles_exist():
    css = _styles_css()

    assert ".chat-prompt-action.live-video" in css
    assert ".chat-prompt-action.live-video.listening" in css
    assert ".live-video-source" in css
    assert ".live-proactive-toggle" in css
    assert ".live-video-hint" in css
    assert ".live-proactive-hint" in css


def test_live_visual_does_not_break_jarvis():
    """Jarvis listen path is untouched: no live-visual hooks inside it, and
    the live/jarvis radio-group mutual exclusion stays intact."""
    html = _index_html()

    assert 'id="btListenBtn"' in html
    start_bt = _function_body(html, "startBtListening")
    assert "startLiveVideo" not in start_bt
    assert "liveProactiveToggle" not in start_bt
    # Radio group unchanged.
    assert 'role="radiogroup"' in html
