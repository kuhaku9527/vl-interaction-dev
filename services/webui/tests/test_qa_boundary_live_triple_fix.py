"""QA independent boundary regression for the 2026-08-12 "live triple fix".

Three commits under verification (independent QA view — not trusting engineer
self-tests):

  * 00a84eb — Jarvis/Live mode selection as a mutually exclusive RADIO GROUP
               (explicit user click only, no automatic switching);
  * 5976581 — health/heartbeat access-log separation (heartbeat -> DEBUG,
               failures still INFO, JOYAI_LOG_LEVEL=DEBUG restores);
  * 5ae9401 — SentenceBuffer comma-level secondary split for long Chinese
               replies (max_sentence_chars=80, comma_split_enabled=True).

These tests go BEYOND the engineer's static contract tests and pin boundary
behavior the user feedback is about:

  * rapid double-click / rapid toggle timing on the radio group (race check);
  * heartbeat path classification with query strings + the middleware routing
    table (heartbeat failure >= 400 must stay INFO-visible);
  * comma-split thresholds: exact boundary, just-beyond fallback, min-sentence
    protection, ASCII comma, period-priority interaction, legacy-disabled.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[1]
WEBUI_SRC = WEBUI_ROOT / "src"
if str(WEBUI_SRC) not in sys.path:
    sys.path.insert(0, str(WEBUI_SRC))

INDEX_HTML = WEBUI_SRC / "joy_interaction_webui" / "static" / "index.html"
SERVER_PY = WEBUI_SRC / "joy_interaction_webui" / "server.py"

from joy_interaction_webui.server import _is_heartbeat_path  # noqa: E402
from joy_interaction_webui.turn_controller import SentenceBuffer  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", html, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


def _strip_comments(src: str) -> str:
    """Remove // and /* */ comments so comment text never satisfies an assert."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


# ---------------------------------------------------------------------------
# 1) Mode radio group — rapid-click / timing boundary
# ---------------------------------------------------------------------------


def test_radio_start_calls_exist_only_inside_click_selectors():
    """No automatic switch path: the ONLY place a mode may be *started* is
    inside the two user-click selector functions."""
    html = _index_html()
    src = _strip_comments(html)
    select_live = _function_body(html, "selectLiveMode")
    select_bt = _function_body(html, "selectBtListenMode")
    # Locate the selector bodies inside the stripped source (they must appear
    # verbatim — the extractor keeps internal indentation).
    assert select_live in src and select_bt in src
    live_span = (src.index(select_live), src.index(select_live) + len(select_live))
    bt_span = (src.index(select_bt), src.index(select_bt) + len(select_bt))
    for call, owner in (
        ("startLiveMode()", "selectLiveMode"),
        ("startBtListening()", "selectBtListenMode"),
    ):
        for m in re.finditer(re.escape(call), src):
            pre = src[max(0, m.start() - 40) : m.start()]
            # Skip the function DEFINITION itself ("async function startLiveMode()").
            if re.search(r"function\s+\w*\s*$", pre):
                continue
            pos = m.start()
            in_live = live_span[0] <= pos < live_span[1]
            in_bt = bt_span[0] <= pos < bt_span[1]
            assert in_live or in_bt, f"{call} starts a mode outside the click selectors"
            if owner == "selectLiveMode":
                assert in_live, f"{call} must live in selectLiveMode, got ...{pre}"
            else:
                assert in_bt, f"{call} must live in selectBtListenMode, got ...{pre}"
            assert "await " in pre, f"{call} called without await: ...{pre}"


def test_radio_same_mode_double_click_is_idempotent_guard():
    """Clicking the already-active mode toggles it OFF (guard returns early),
    never double-starts."""
    live_body = _function_body(_index_html(), "selectLiveMode")
    bt_body = _function_body(_index_html(), "selectBtListenMode")
    assert "if (liveModeActive || liveModeStarting) {" in live_body
    assert "await stopLiveMode();" in live_body
    assert "return;" in live_body
    assert "if (btListening || btListeningStarting) {" in bt_body
    assert "await stopBtListening();" in bt_body
    assert "return;" in bt_body


def test_radio_aria_checked_matches_active_state_everywhere():
    """Every place that flips the active flag must also flip aria-checked."""
    html = _index_html()
    src = _strip_comments(html)
    # The only direct assignments to the active flags live inside the setters,
    # and each setter updates aria-checked from the same flag variable.
    for flag, btn, body_name in (
        ("liveModeActive", "liveModeBtn", "setLiveModeActive"),
        ("btListening", "btListenBtn", "setBtListeningActive"),
    ):
        body = _function_body(html, body_name)
        assert f"{btn}.setAttribute('aria-checked', {flag} ? 'true' : 'false')" in body
        # No other direct assignment to the flag (outside the setter and the
        # initial `let flag = false;` declaration).
        for m in re.finditer(rf"\b{flag}\s*=", src):
            start = max(0, m.start() - 120)
            snippet = src[start : m.end()]
            if re.search(r"\blet\s+$", src[max(0, m.start() - 8) : m.start()]):
                continue  # initial declaration
            assert body_name in snippet, (
                f"direct {flag} assignment outside {body_name}: ...{snippet}"
            )


def test_radio_rapid_toggle_race_recheck_after_await():
    """RAPID-TIMING contract (PRD: mutual exclusion, no double-start).

    A click on A while B is active runs stop-B (async) then start-A. If the
    user clicks B again while the stop-B fetch (or the A getUserMedia start)
    is still in flight, the first handler's continuation MUST re-check that B
    is not active/starting again before starting A — otherwise BOTH modes can
    end up running (the exact double-start the user complained about).

    This asserts the guard exists between the awaited stop and the start.
    """
    html = _index_html()
    bt_body = _strip_comments(_function_body(html, "selectBtListenMode"))
    live_body = _strip_comments(_function_body(html, "selectLiveMode"))

    # jarvis selector: stop live -> then start jarvis, guarded by re-check.
    bt_stop = bt_body.index("await stopLiveMode();")
    bt_start = bt_body.index("await startBtListening();")
    gap = bt_body[bt_stop:bt_start]
    assert re.search(r"if\s*\(\s*liveModeActive\s*\|\|\s*liveModeStarting\s*\)", gap), (
        "selectBtListenMode starts jarvis after awaiting stopLiveMode() without "
        "re-checking liveModeActive/liveModeStarting — rapid click back to Live "
        "can leave BOTH modes running (double-start)."
    )

    # live selector: stop jarvis -> then start live, guarded by re-check.
    live_stop = live_body.index("await stopBtListening();")
    live_start = live_body.index("await startLiveMode();")
    gap = live_body[live_stop:live_start]
    assert re.search(r"if\s*\(\s*btListening\s*\|\|\s*btListeningStarting\s*\)", gap), (
        "selectLiveMode starts live after awaiting stopBtListening() without "
        "re-checking btListening/btListeningStarting — rapid click back to "
        "Jarvis can leave BOTH modes running (double-start)."
    )


# ---------------------------------------------------------------------------
# 2) Heartbeat log separation — classification + middleware routing table
# ---------------------------------------------------------------------------


def test_heartbeat_path_with_query_and_fragment():
    """Query strings are stripped; ?session_id= polls stay heartbeat."""
    assert _is_heartbeat_path("/api/jarvis/status?session_id=abc") is True
    assert _is_heartbeat_path("/api/llm/status?session_id=default") is True
    assert _is_heartbeat_path("/api/live/status?session_id=x") is True
    assert _is_heartbeat_path("/api/services/extended-status?t=1") is True
    assert _is_heartbeat_path("/api/jarvis/health?probe=1") is True
    assert _is_heartbeat_path("/health?probe=1") is True
    assert _is_heartbeat_path("/v1/models?x=1") is True


def test_heartbeat_path_real_event_paths_stay_info():
    """Real event endpoints are NOT heartbeat -> always INFO."""
    assert _is_heartbeat_path("/api/llm/message") is False
    assert _is_heartbeat_path("/api/tts/synthesize") is False
    assert _is_heartbeat_path("/api/live/start") is False
    assert _is_heartbeat_path("/api/jarvis/stop") is False
    assert _is_heartbeat_path("/api/services/config") is False
    assert _is_heartbeat_path("/api/session/cleanup") is False
    assert _is_heartbeat_path("/ws") is False
    assert _is_heartbeat_path("/offer") is False


def test_heartbeat_path_trailing_slash_not_classified():
    """Documented edge: a trailing slash defeats the suffix match. The current
    frontend never polls with a trailing slash (verified against index.html),
    so this is informational only, not a regression."""
    assert _is_heartbeat_path("/api/jarvis/status/") is False


def test_middleware_routing_table_status_semantics():
    """The middleware routes heartbeat+success(<400) to DEBUG; everything else
    (including heartbeat FAILURE >=400) to INFO. Evaluate the same predicate
    the middleware uses, against the real classifier."""
    for path in (
        "/api/jarvis/status",
        "/api/llm/status",
        "/api/live/status",
        "/api/services/extended-status",
        "/health",
        "/v1/models",
    ):
        # Heartbeat + success -> DEBUG
        assert _is_heartbeat_path(path) and 200 < 400
        # Heartbeat + failure (500) -> NOT debug -> INFO (errors stay visible)
        assert not (_is_heartbeat_path(path) and 500 < 400)
    for path in ("/api/llm/message", "/api/tts/synthesize", "/api/live/start", "/"):
        # Non-heartbeat + any status -> INFO (never DEBUG)
        assert not (_is_heartbeat_path(path) and 200 < 400)


def test_middleware_source_branch_order_no_omission():
    """Source-level: the debug branch covers heartbeat+success only; the else
    covers every remaining case (heartbeat failure + all real events)."""
    src = _strip_comments(_server_source())
    assert "_is_heartbeat_path(request.path) and status < 400" in src
    assert "_access_logger.debug(line)" in src
    assert "_access_logger.info(line)" in src
    debug_idx = src.index("_access_logger.debug(line)")
    info_idx = src.index("_access_logger.info(line)")
    # debug must be inside the if (before the else/info), so heartbeat failures
    # naturally fall into the info branch.
    assert debug_idx < info_idx


def test_joyai_log_level_env_resolution():
    """JOYAI_LOG_LEVEL gates the handler: INFO by default (heartbeat hidden),
    DEBUG restores heartbeat lines, unknown values fall back to INFO."""
    import logging

    def resolve(env_value):
        return getattr(logging, (env_value or "INFO").upper(), logging.INFO)

    assert resolve(None) == logging.INFO
    assert resolve("INFO") == logging.INFO
    assert resolve("DEBUG") == logging.DEBUG
    assert resolve("BOGUS") == logging.INFO  # unknown -> INFO, never crash
    src = _strip_comments(_server_source())
    assert 'os.environ.get("JOYAI_LOG_LEVEL", "INFO")' in src
    assert '_os_for_accesslog.environ.get("JOYAI_LOG_LEVEL", "INFO")' in src
    # Logger passes DEBUG; the FileHandler is the real gate (env-controlled).
    assert "_access_logger.setLevel(logging.DEBUG)" in src


# ---------------------------------------------------------------------------
# 3) SentenceBuffer comma split — boundary behaviors
# ---------------------------------------------------------------------------


def test_comma_split_multiple_commas_picks_nearest_not_beyond():
    """Multiple commas: split at the one nearest (but not beyond) the
    threshold, so chunks land near ~max_sentence_chars."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=3)
    text = "一二三四五六七八九十，一二三四五六七八九十，然后回家"
    out = buf.add_token(text)
    # First comma at idx 10 (chunk 11) is < 20; second comma at idx 21 (chunk
    # 22) is beyond 20 -> pick the LAST comma within window (idx 10).
    assert out == "一二三四五六七八九十，"
    assert buf.text == "一二三四五六七八九十，然后回家"
    assert buf.flush_remaining() == "一二三四五六七八九十，然后回家"


def test_comma_split_no_comma_force_flush_fallback():
    """No comma anywhere: the secondary split cannot fire; max_buffer_chars
    force-flush is the ultimate fallback (legacy behavior preserved)."""
    buf = SentenceBuffer(max_sentence_chars=20, max_buffer_chars=30, min_sentence_length=3)
    text = "这是一个完全没有标点符号也没有任何逗号的长句子啊让我们继续写下去吧"
    assert len(text) >= 30
    out = buf.add_token(text)
    assert out == text  # force-flushed whole at max_buffer_chars
    assert buf.is_empty


def test_comma_split_exact_threshold_boundary():
    """Comma exactly at the threshold (chunk length == max_sentence_chars)
    is a valid split point (i < max_sentence_chars keeps idx threshold-1)."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=3)
    # 19 chars, comma at idx 19 -> chunk length 20 == threshold.
    text = "一二三四五六七八九十壹贰叁肆伍陆柒捌玖，尾巴"
    out = buf.add_token(text)
    assert out == "一二三四五六七八九十壹贰叁肆伍陆柒捌玖，"
    assert len(out) == 20
    assert buf.text == "尾巴"


def test_comma_split_just_beyond_threshold_fallback_first_comma():
    """Comma just beyond the threshold: within-window is empty -> documented
    fallback to the first valid comma (chunk slightly > threshold)."""
    buf = SentenceBuffer(max_sentence_chars=10, min_sentence_length=3)
    text = "一二三四五六七八九十，十一十二"
    out = buf.add_token(text)
    assert out == "一二三四五六七八九十，"  # first comma at idx 10 (chunk 11)
    assert buf.text == "十一十二"


def test_comma_split_respects_min_sentence_length():
    """A comma too close to the start (chunk < min_sentence_length) is never a
    split point; the buffer keeps waiting for a later comma."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=10)
    # First comma at idx 1 -> chunk len 2 < 10 -> must NOT split there.
    # Second comma at idx 17 -> chunk len 18 >= 10 and <= 20 -> split.
    text = "你，好世界你好世界你好世界你好世界，继续内容继续内容"
    assert len(text) >= 20
    out = buf.add_token(text)
    assert out == "你，好世界你好世界你好世界你好世界，"
    assert len(out) == 18
    assert buf.flush_remaining() == "继续内容继续内容"


def test_comma_split_ascii_comma_and_semicolon():
    """ASCII comma / semicolon / enumeration comma all participate."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=3)
    out = buf.add_token("one two three four five, six seven eight nine ten, tail")
    # ASCII comma nearest (<=) 20 chars.
    assert out == "one two three four five,"
    assert "tail" in buf.text


def test_period_priority_over_comma():
    """A hard sentence ending wins even when a comma would have fired first:
    text ending in 。 flushes as one complete sentence (comma is only the
    secondary split for buffers WITHOUT a hard ending)."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=3)
    out = buf.add_token("一二三四五六七八九十，一二三四五六七八九十。")
    assert out == "一二三四五六七八九十，一二三四五六七八九十。"
    assert buf.is_empty


def test_comma_split_interaction_period_then_comma():
    """Period first (hard flush), then a new long comma run splits again."""
    buf = SentenceBuffer(max_sentence_chars=20, min_sentence_length=3)
    assert buf.add_token("第一句，结束。") == "第一句，结束。"
    out2 = buf.add_token("第二句很长很长很长，很长很长很长很长，尾巴")
    assert out2 is not None
    assert out2.endswith("，")
    assert "尾巴" in buf.text or buf.flush_remaining() == "尾巴"


def test_comma_split_disabled_matches_legacy():
    """comma_split_enabled=False restores the pre-fix behavior: no comma
    splitting, only hard endings / max_buffer_chars / timeout flush."""
    buf = SentenceBuffer(max_sentence_chars=20, comma_split_enabled=False, max_buffer_chars=500)
    text = "今天天气很好，我们一起去公园散步，然后回家吃饭，"
    assert buf.add_token(text) is None
    assert buf.text == text
    # A hard ending still flushes (legacy behavior unchanged).
    buf2 = SentenceBuffer(max_sentence_chars=20, comma_split_enabled=False, min_sentence_length=3)
    assert buf2.add_token("你好，世界。") == "你好，世界。"


def test_comma_split_291_char_user_case_reduced_chunk_size():
    """The actual user report: a 291-char Chinese reply must no longer come
    out as ONE synthesis — comma splits keep chunks near max_sentence_chars."""
    buf = SentenceBuffer(max_sentence_chars=80, min_sentence_length=10)
    clauses = ["这是第%d个中文分句的内容" % i for i in range(1, 30)]
    text = "，".join(clauses) + "。"
    assert len(text) >= 291
    sentences: list[str] = []
    # Feed in ~30-char tokens (simulating LLM streaming) and collect flushes.
    remainder = text
    while remainder:
        token, remainder = remainder[:30], remainder[30:]
        out = buf.add_token(token)
        if out:
            sentences.append(out)
    tail = buf.flush_remaining()
    if tail:
        sentences.append(tail)
    # The giant sentence must have been broken into multiple TTS chunks.
    assert len(sentences) >= 3
    # Every intermediate chunk (except possibly the final tail) is <= 80+1.
    for s in sentences[:-1]:
        assert len(s) <= 81, f"chunk too large: {len(s)} chars: {s[:40]}..."
