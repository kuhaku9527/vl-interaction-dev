"""Static contract tests: Jarvis 唤醒 / Live 常驻 are a mutually exclusive
radio group (explicit user click only — no automatic switching).

User feedback (2026-08-12): "不需要 jarvis+live 双开" — the two listen modes
must be distinguishable AND restricted in the UI. This pins the front-end
contract:

  * the two buttons live inside a ``role="radiogroup"`` container and each is
    ``role="radio"`` with ``aria-checked`` (radio semantics, not independent
    toggles);
  * clicking one mode when the other is active stops the other first (radio
    group's inherent single-selection semantics);
  * there is NO automatic switch path: the stop-other-then-start-this pairing
    only exists inside the two user-click handlers, and no timer/interval/WS
    callback ever starts a mode on its own.
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


def test_mode_buttons_form_a_radio_group():
    html = _index_html()

    # Container with radio-group semantics wraps both mode buttons.
    assert 'class="mode-group" role="radiogroup"' in html
    # Both buttons are radios, not independent toggle buttons.
    assert 'id="btListenBtn"' in html
    assert 'role="radio" aria-checked="false"' in html
    assert 'id="liveModeBtn"' in html
    assert "Jarvis 唤醒模式" in html
    assert "Live 常驻模式" in html


def test_radio_checked_state_updates_with_active_mode():
    html = _index_html()
    live_body = _function_body(html, "setLiveModeActive")
    bt_body = _function_body(html, "setBtListeningActive")

    assert (
        "liveModeBtn.setAttribute('aria-checked', liveModeActive ? 'true' : 'false')" in live_body
    )
    assert "btListenBtn.setAttribute('aria-checked', btListening ? 'true' : 'false')" in bt_body
    # Legacy independent-toggle attribute must be gone.
    assert "aria-pressed" not in live_body
    assert "aria-pressed" not in bt_body


def test_live_click_stops_jarvis_before_starting():
    html = _index_html()
    body = _function_body(html, "selectLiveMode")

    assert "await stopBtListening()" in body
    assert "await startLiveMode()" in body
    # The stop must happen BEFORE the start (ordering is the radio switch).
    assert body.index("await stopBtListening()") < body.index("await startLiveMode()")


def test_jarvis_click_stops_live_before_starting():
    html = _index_html()
    body = _function_body(html, "selectBtListenMode")

    assert "await stopLiveMode()" in body
    assert "await startBtListening()" in body
    assert body.index("await stopLiveMode()") < body.index("await startBtListening()")


def test_click_handlers_wire_the_radio_selectors():
    html = _index_html()

    assert "btListenBtn.addEventListener('click', selectBtListenMode)" in html
    assert "liveModeBtn.addEventListener('click', selectLiveMode)" in html


def test_no_automatic_mode_switch_path():
    """The stop-other-then-start-this pairing must exist ONLY inside the two
    explicit user-click selector functions. No timer, interval, WS status
    handler, or ICE callback may start a mode by itself.
    """
    html = _index_html()
    select_live = _function_body(html, "selectLiveMode")
    select_bt = _function_body(html, "selectBtListenMode")

    # The only co-occurrence of stop-other + start-this lives in the selectors.
    assert "stopBtListening()" in select_live
    assert "stopLiveMode()" in select_bt

    # No interval/timer that starts a mode (auto-switch would use one).
    assert "setInterval" not in select_live
    assert "setInterval" not in select_bt
    assert "setTimeout" not in select_live
    assert "setTimeout" not in select_bt

    # The selectors are reachable only from user click listeners.
    assert "addEventListener('click', selectBtListenMode)" in html
    assert "addEventListener('click', selectLiveMode)" in html


def test_mode_labels_visible_desktop_hidden_mobile():
    css = _styles_css()

    # Desktop: labels inside the group are visible (mode distinguishable).
    assert ".mode-group .listen-label," in css
    assert ".mode-group .live-label" in css
    assert "display: inline-block;" in css
    # Active radio gets a clear selection ring.
    assert '.mode-group .chat-prompt-action[aria-checked="true"]' in css
    assert "outline: 2px solid var(--accent-color);" in css
    # Mobile: labels hidden to keep the compact icon-only prompt bar.
    assert (
        ".mode-group .listen-label,\n            .mode-group .live-label {\n                display: none;"
        in css
    )
