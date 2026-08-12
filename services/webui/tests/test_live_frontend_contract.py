"""Live mode (Phase C C.A) front-end static contract tests.

Pins the live entry point in ``index.html``:

  * a dedicated "live mode" button exists with independent state;
  * clicking it calls ``/api/live/start`` (and stop on exit);
  * the WebRTC offer for live carries ``live_audio: true`` (so the server
    mounts a LiveStateMachine, not a jarvis session);
  * live replies reuse the P0-A ``tts_sentence`` queue and the P1
    ``llmReplyGeneration`` guard (same WS chain);
  * ``playLlmReplyAudio`` skips ``live_voice`` (streaming path) so the
    browser does not double-play via /api/tts/synthesize;
  * the live status pill polls /api/live/status.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static" / "index.html"
STYLES_CSS = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static" / "styles.css"


def _index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _styles_css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", html, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


def test_live_mode_button_exists_with_independent_state():
    html = _index_html()

    assert 'id="liveModeBtn"' in html
    assert "进入 Live 常驻模式" in html
    # Independent state vars (not the jarvis btListening vars).
    assert "let liveModeActive = false" in html
    assert "let liveModeStarting = false" in html
    assert "function setLiveModeActive(active)" in html


def test_live_mode_start_calls_api_and_establishes_webrtc():
    html = _index_html()
    body = _function_body(html, "startLiveMode")

    assert "'/api/live/start'" in body
    assert "session_id: sessionId" in body
    assert "getUserMedia" in body
    assert "addTransceiver('audio', { direction: 'sendrecv' })" in body
    # Live offers mount a LiveStateMachine server-side via live_audio flag.
    assert "live_audio: true" in body
    assert "jarvis_audio: true" not in body


def test_live_mode_stop_calls_api():
    html = _index_html()
    body = _function_body(html, "stopLiveMode")

    assert "'/api/live/stop'" in body
    assert "session_id: sessionId" in body
    assert "stopLlmReplyAudio()" in body  # leave no dangling reply audio


def test_live_mode_click_handler_toggles():
    """The live button is wired to the radio selector, which toggles start/stop
    (and stops the jarvis mode first — mutual exclusion lives in selectLiveMode)."""
    html = _index_html()
    idx = html.index("liveModeBtn.addEventListener('click'")
    snippet = html[idx : idx + 400]
    assert "selectLiveMode" in snippet
    body = _function_body(html, "selectLiveMode")
    assert "startLiveMode()" in body
    assert "stopLiveMode()" in body
    assert "stopBtListening()" in body  # radio-group: jarvis stopped first


def test_live_replies_reuse_tts_sentence_queue_and_p1_guard():
    """Live replies flow through the same WS chain as jarvis (no new path)."""
    html = _index_html()
    handler_body = _function_body(html, "installLlmReplyHandler")

    assert "data.type === 'tts_sentence'" in handler_body
    assert "enqueueLlmReplySentence(data)" in handler_body
    assert "data.reply_epoch < llmReplyGeneration" in handler_body
    assert "data.type === 'asr_partial'" in handler_body
    assert "stopLlmReplyAudio()" in handler_body


def test_live_voice_skips_frontend_tts_synthesis():
    """live_voice (streaming path) must not double-play via /api/tts/synthesize."""
    body = _function_body(_index_html(), "playLlmReplyAudio")
    assert "meta.source === 'jarvis_voice' || meta.source === 'live_voice'" in body


def test_live_status_pill_polls_live_status():
    html = _index_html()

    assert 'id="liveStatus"' in html
    assert "function renderLiveStatus(payload)" in html
    assert "pollLiveStatus" in html
    assert "'/api/live/status?session_id=' + encodeURIComponent(sid)" in html
    assert "LIVE_STATE_MAP" in html


def test_live_status_badge_colors_exist_in_stylesheet():
    css = _styles_css()

    assert ".status-badge.live-listening" in css
    assert ".status-badge.live-speaking" in css
    assert ".status-badge.live-processing" in css
    assert ".status-badge.live-interrupted" in css
    assert ".status-badge.live-error" in css
    assert ".status-badge.live-disconnected" in css
    assert ".chat-prompt-action.listen.live-mode.listening" in css


def test_live_does_not_break_jarvis_listen_button():
    html = _index_html()

    assert 'id="btListenBtn"' in html
    assert "startBtListening()" in html
    assert "stopBtListening()" in html
    # The live button is a sibling, not a replacement.
    assert 'id="liveModeBtn"' in html
    assert "jarvis_audio: true" in html


def test_server_registers_live_routes_and_offer_branch():
    server_py = (WEBUI_ROOT / "src" / "joy_interaction_webui" / "server.py").read_text(
        encoding="utf-8"
    )

    assert "setup_live_routes(app)" in server_py
    assert "bind_live_audio_for_peer" in server_py
    assert 'params.get("live_audio") is True' in server_py
    # The live branch must precede the jarvis audio branch (both inside offer()).
    live_idx = server_py.index('params.get("live_audio") is True')
    jarvis_idx = server_py.index("elif _offer_has_jarvis_audio(params):")
    assert live_idx < jarvis_idx


def test_live_routes_module_exposes_endpoints():
    routes_py = (WEBUI_ROOT / "src" / "joy_interaction_webui" / "live_routes.py").read_text(
        encoding="utf-8"
    )

    assert 'add_post("/api/live/start", live_start)' in routes_py
    assert 'add_post("/api/live/stop", live_stop)' in routes_py
    assert 'add_get("/api/live/status", live_status)' in routes_py
    assert "def bind_live_audio_for_peer" in routes_py
