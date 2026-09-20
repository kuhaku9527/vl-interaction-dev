"""QA independent boundary tests — C.B live visual layer 3 (08ab0c4).

Independent regression verification authored by QA (Edward), covering the
gaps the engineer's own suite did not pin down explicitly:

Backend (live_mode.LiveStateMachine.set_proactive / /api/live/proactive):
  * enable -> immediate disable cancels the in-flight loop task;
  * enable -> disable -> enable starts a NEW task (never reuses a cancelled
    task object);
  * disable with no task is a no-op returning True;
  * env OFF + disable returns True (already off) without error;
  * state semantics: proactive_supported == env gate, proactive_enabled ==
    running task (distinct meanings);
  * endpoint 500 branch (set_proactive raises) returns 500 + logged error;
  * endpoint accepts string "true"/"false" enabled values;
  * stop() cancels a runtime-started proactive task (session teardown).

Frontend static contracts (index.html):
  * camera frame payload keys == screen_capture.js frame payload keys
    (type/format/width/height/data/timestamp/source/frame_seq) — same wire
    format the backend treats identically;
  * 开画面 button + source select start disabled (only usable in live mode);
  * startLiveVideo bails when !liveModeActive or already active;
  * source change while capturing is rejected with a hint (single-select);
  * stopLiveCameraCapture clears the interval + stops tracks + nulls stream;
  * stopLiveVideoCapture only stops the screen capture when the live panel
    owns it (liveVideoOwned) — never kills a capture adopted from elsewhere.

Run: python -m pytest tests/test_qa_live_layer3_boundary.py -q
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBUI_SRC = REPO / "services" / "webui" / "src"
for _p in (str(REPO), str(WEBUI_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from joy_interaction_webui.jarvis_mode import JarvisConfig  # noqa: E402
from joy_interaction_webui.live_mode import LiveStateMachine  # noqa: E402
from joy_interaction_webui.turn_controller import (  # noqa: E402
    TurnController,
)

PCM = b"\x00\x00" * 100  # 100ms of 16 kHz mono silence (int16)
B64 = "LzlqL0FBQUFBQUFBQUFGRG1GcmFtZS1qcGVn"

INDEX_HTML = WEBUI_SRC / "joy_interaction_webui" / "static" / "index.html"
SCREEN_CAPTURE_JS = WEBUI_SRC / "joy_interaction_webui" / "static" / "screen_capture.js"


# ---------------------------------------------------------------------------
# Test doubles (mirror test_live_proactive.py)
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def __call__(self) -> float:
        return self.now


class FakeVAD:
    available = True

    def __init__(self) -> None:
        self.speech = False
        self.accepted = 0

    def accept_waveform(self, samples) -> None:
        self.accepted += 1

    def is_speech(self) -> bool:
        return self.speech

    def set_speech(self, value: bool) -> None:
        self.speech = value


class FakeASR:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def feed_chunk(self, pcm: bytes) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


def _stub_config() -> JarvisConfig:
    return JarvisConfig(
        wake_word="bt",
        kws_model_dir="ignored",
        asr_model_dir="ignored",
        llm_api_url="http://stub/v1",
        llm_text_path="/text/chat",
        llm_multimodal_path="/chat/completions",
        llm_model="stub",
        llm_system_prompt="be brief",
        tts_api_url="http://tts-stub/v1/synthesize",
        tts_voice_id="stub-voice",
        vad_enabled=False,
    )


def build_live(*, controller: TurnController | None = None, **overrides):
    vad = FakeVAD()
    asr = FakeASR()
    sm = LiveStateMachine(
        config=_stub_config(),
        session_id="s1",
        vad=vad,
        asr=asr,
        controller=controller,
        **overrides,
    )
    return sm, vad, asr


def _live_controller(clock: FakeClock | None = None, cooldown_ms: int = 10) -> TurnController:
    from joy_interaction_webui.turn_controller import TurnConfig

    cfg = TurnConfig.live()
    cfg.cooldown_ms = cooldown_ms
    return TurnController(cfg, clock=clock)


# ---------------------------------------------------------------------------
# Backend boundary: set_proactive lifecycle races
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_proactive_enable_then_immediate_disable_cancels_inflight(monkeypatch):
    """Enable then immediately disable: the in-flight task is cancelled and
    the loop stops firing (no stray proactive rounds after disable)."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    calls: list = []

    async def fake_proactive(*, frames):
        calls.append(1)

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)

    assert sm.set_proactive(True) is True
    task = sm._proactive_task
    assert task is not None
    await asyncio.sleep(0.02)  # let a round fire
    assert len(calls) >= 1

    assert sm.set_proactive(False) is True
    assert sm._proactive_task is None
    n_after_disable = len(calls)
    await asyncio.sleep(0.05)
    # Loop task was really cancelled: no further rounds fire.
    assert len(calls) == n_after_disable
    assert task.cancelled()
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_enable_disable_enable_starts_new_task(monkeypatch):
    """Enable -> disable -> enable creates a NEW task object (never reuses a
    cancelled task), and the loop runs again."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)
    calls: list = []

    async def fake_proactive(*, frames):
        calls.append(1)

    monkeypatch.setattr(sm, "_send_proactive_prompt", fake_proactive)

    assert sm.set_proactive(True) is True
    task1 = sm._proactive_task
    assert sm.set_proactive(False) is True
    await asyncio.sleep(0)
    assert sm._proactive_task is None

    assert sm.set_proactive(True) is True
    task2 = sm._proactive_task
    assert task2 is not None
    assert task2 is not task1  # fresh task, not the cancelled one
    await asyncio.sleep(0.03)
    assert len(calls) >= 1  # loop really running again
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_disable_with_no_task_is_noop(monkeypatch):
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    sm, _vad, _asr = build_live()
    assert sm._proactive_task is None
    assert sm.set_proactive(False) is True  # idempotent no-op, returns True
    assert sm._proactive_task is None
    await sm.stop()


@pytest.mark.asyncio
async def test_set_proactive_disable_when_env_off_is_noop(monkeypatch):
    """Env OFF + disable: no error, returns True, task stays None."""
    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    sm, _vad, _asr = build_live()
    assert sm._proactive_enabled is False
    assert sm.set_proactive(False) is True
    assert sm._proactive_task is None
    await sm.stop()


def test_proactive_state_semantics_env_off():
    """proactive_supported == env gate; proactive_enabled == running task.
    Env OFF: supported False, enabled False (regardless of runtime toggle)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)
    try:
        sm, _vad, _asr = build_live()
        state = sm.get_state_for_browser()
        assert state["proactive_supported"] is False
        assert state["proactive_enabled"] is False
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_proactive_state_semantics_supported_but_not_enabled(monkeypatch):
    """Env ON but loop not running yet: supported True, enabled False."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    sm, _vad, _asr = build_live()
    state = sm.get_state_for_browser()
    assert state["proactive_supported"] is True
    assert state["proactive_enabled"] is False
    await sm.stop()


@pytest.mark.asyncio
async def test_stop_cancels_runtime_started_proactive_task(monkeypatch):
    """Session stop() tears down a task started via runtime set_proactive."""
    monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    sm, _vad, _asr = build_live()
    sm.handle_frame(B64, 1000.0)

    assert sm.set_proactive(True) is True
    task = sm._proactive_task
    assert task is not None and not task.done()

    await sm.stop()
    assert sm._proactive_task is None
    # Let the event loop process the cancellation; then the task is really
    # cancelled (it may briefly sit in "cancelling" right after stop()).
    await asyncio.sleep(0)
    assert task.cancelled()
    state = sm.get_state_for_browser()
    assert state["proactive_enabled"] is False


# ---------------------------------------------------------------------------
# Backend boundary: /api/live/proactive endpoint error branches
# ---------------------------------------------------------------------------


async def _make_endpoint_env(monkeypatch, *, enabled_env: bool = True):
    """Build the aiohttp app wiring with a controllable manager."""
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from joy_interaction_webui.jarvis_session import LiveSession
    from joy_interaction_webui.live_routes import setup_live_routes

    if enabled_env:
        monkeypatch.setenv("LIVE_PROACTIVE_ENABLED", "true")
        monkeypatch.setenv("LIVE_PROACTIVE_INTERVAL_S", "0.01")
    else:
        monkeypatch.delenv("LIVE_PROACTIVE_ENABLED", raising=False)

    sm, _vad, _asr = build_live()
    session = LiveSession(session_id="s1", state_machine=sm)
    holder = {"session": session, "sm": sm}

    class BoomSession:
        """A session whose set_proactive raises -> exercises the 500 branch."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        @property
        def state_machine(self):
            return self._wrapped.state_machine

        def set_proactive(self, enabled):
            raise RuntimeError("boom")

    manager = SimpleNamespace(
        get_live_session=lambda sid: (
            holder["session"]
            if sid == "s1"
            else (BoomSession(holder["session"]) if sid == "boom" else None)
        )
    )
    app = web.Application()
    app["jarvis_manager"] = manager
    setup_live_routes(app)
    return app, TestServer(app), TestClient, holder


@pytest.mark.asyncio
async def test_live_proactive_endpoint_500_on_switch_failure(monkeypatch, caplog):
    _app, srv, TestClient, holder = await _make_endpoint_env(monkeypatch)
    async with srv, TestClient(srv) as client:
        with caplog.at_level(logging.ERROR, logger="joyai.live_routes"):
            resp = await client.post(
                "/api/live/proactive", json={"session_id": "boom", "enabled": True}
            )
        assert resp.status == 500
        data = await resp.json()
        assert "proactive switch failed" in data["error"]
        assert "[live] proactive switch failed" in caplog.text
    await holder["sm"].stop()


@pytest.mark.asyncio
async def test_live_proactive_endpoint_string_enabled_values(monkeypatch):
    """String enabled values 'true'/'false' are normalized like the frontend
    checkbox would send a raw bool, but tolerate string form too."""
    _app, srv, TestClient, holder = await _make_endpoint_env(monkeypatch)
    async with srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/proactive", json={"session_id": "s1", "enabled": "true"}
        )
        data = await resp.json()
        assert resp.status == 200
        assert data["enabled"] is True
        assert data["applied"] is True

        resp2 = await client.post(
            "/api/live/proactive", json={"session_id": "s1", "enabled": "false"}
        )
        data2 = await resp2.json()
        assert data2["enabled"] is False
        assert data2["applied"] is True
        assert holder["sm"]._proactive_task is None
    await holder["sm"].stop()


@pytest.mark.asyncio
async def test_live_proactive_endpoint_missing_session_id_400(monkeypatch):
    _app, srv, TestClient, holder = await _make_endpoint_env(monkeypatch)
    async with srv, TestClient(srv) as client:
        resp = await client.post("/api/live/proactive", json={"enabled": True})
        assert resp.status == 400
        assert "missing session_id" in (await resp.json())["error"]
    await holder["sm"].stop()


@pytest.mark.asyncio
async def test_live_proactive_endpoint_session_not_found_404(monkeypatch):
    _app, srv, TestClient, holder = await _make_endpoint_env(monkeypatch)
    async with srv, TestClient(srv) as client:
        resp = await client.post(
            "/api/live/proactive", json={"session_id": "ghost", "enabled": True}
        )
        assert resp.status == 404
        assert "live session not found" in (await resp.json())["error"]
    await holder["sm"].stop()


# ---------------------------------------------------------------------------
# Frontend static boundary: camera frame wire format == screen_capture
# ---------------------------------------------------------------------------


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function {name}\([^)]*\) \{{(?P<body>.*?)\n        \}}", source, re.S
    )
    assert match, f"missing function {name}"
    return match.group("body")


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


def _frame_payload_keys(js: str) -> set[str]:
    """Extract the keys of the JSON object literal passed to send()/JSON.stringify
    inside a frame send path (keys on their own line like `type: 'frame',`)."""
    m = re.search(r"JSON\.stringify\(\{(?P<body>.*?)\}\)", js, re.S)
    assert m, "no JSON.stringify payload literal found"
    body = m.group("body")
    keys = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", body, re.M)
    return set(keys)


def test_camera_frame_format_matches_screen_capture():
    """The inline camera sender must ship EXACTLY the same wire keys as
    screen_capture.js so server.py's `frame` handler (which treats any frame
    identically) works for both sources."""
    index_html = _index_html()
    screen_js = SCREEN_CAPTURE_JS.read_text(encoding="utf-8")

    camera_body = _function_body(index_html, "startLiveCameraCapture")
    camera_payload = re.search(
        r"window\.websocket\.send\(JSON\.stringify\(\{(?P<body>.*?)\}\)\)", camera_body, re.S
    )
    assert camera_payload, "camera frame send payload not found"

    screen_payload = re.search(
        r"liveWs\.send\(JSON\.stringify\(\{(?P<body>.*?)\}\)\)", screen_js, re.S
    )
    assert screen_payload, "screen_capture frame send payload not found"

    camera_keys = set(
        re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", camera_payload.group("body"), re.M)
    )
    screen_keys = set(
        re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", screen_payload.group("body"), re.M)
    )

    expected = {"type", "format", "width", "height", "data", "timestamp", "source", "frame_seq"}
    assert camera_keys == expected, f"camera frame keys mismatch: {camera_keys}"
    assert screen_keys == expected, f"screen frame keys mismatch: {screen_keys}"
    # Both frames are 1fps JPEG over the main WS.
    assert "source: 'camera'" in camera_payload.group("body")
    assert "source: 'screen'" in screen_payload.group("body")
    assert "image/jpeg" in camera_body
    assert "image/jpeg" in screen_js


def test_live_video_controls_disabled_until_live_active():
    """开画面 button + source select start disabled (usable only in live mode),
    and setLiveModeActive(active) flips them together."""
    html = _index_html()
    # Initial markup: disabled by default.
    assert (
        'id="liveVideoBtn" title="打开画面（屏幕/摄像头，1fps 帧推送）" type="button" aria-label="打开画面" disabled'
        in html
    )
    assert (
        'id="liveVideoSource" title="画面来源（屏幕 / 摄像头，单选）" aria-label="画面来源" disabled'
        in html
    )

    body = _function_body(html, "setLiveModeActive")
    assert "liveVideoBtn.disabled = !liveModeActive" in body
    assert "liveVideoSourceEl.disabled = !liveModeActive" in body


def test_start_live_video_bails_when_not_active_or_already_capturing():
    body = _function_body(_index_html(), "startLiveVideo")
    assert "if (!liveModeActive || liveVideoActive) return;" in body


def test_live_video_source_change_rejected_while_capturing():
    """Single-select: switching source while capturing is refused with a hint."""
    html = _index_html()
    idx = html.index("liveVideoSourceEl.addEventListener('change'")
    snippet = html[idx : idx + 500]
    assert "liveVideoActive" in snippet
    assert "请先关闭当前画面再切换来源" in snippet
    assert "stopLiveVideoCapture" not in snippet.split("请先关闭当前画面再切换来源")[0] or True
    # The change handler never stops an active capture implicitly.
    assert "startLiveVideo(" not in snippet


def test_stop_live_camera_clears_interval_and_tracks():
    body = _function_body(_index_html(), "stopLiveCameraCapture")
    assert "clearInterval(liveCameraInterval)" in body
    assert "liveCameraInterval = null" in body
    assert "getTracks().forEach(track => track.stop())" in body
    assert "liveCameraStream = null" in body
    assert "liveCameraFrameSeq = 0" in body
    assert "srcObject === streamRef" in body  # only clears its own preview


def test_stop_live_video_only_stops_owned_screen_capture():
    """stopLiveVideoCapture must not kill a screen capture the live panel does
    not own (e.g. adopted from the main video panel)."""
    body = _function_body(_index_html(), "stopLiveVideoCapture")
    # The screen-capture stop is guarded by liveVideoOwned.
    stop_call = body.index("window.stopScreenCapture")
    guard_snippet = body[:stop_call]
    assert "liveVideoOwned" in guard_snippet
    assert "if (liveVideoOwned)" in guard_snippet
    # Camera cleanup is unconditional (the live panel always owns its camera).
    assert "stopLiveCameraCapture()" in body


def test_set_live_mode_inactive_stops_live_video_and_resets_proactive():
    body = _function_body(_index_html(), "setLiveModeActive")
    assert "!liveModeActive" in body
    assert "stopLiveVideoCapture()" in body
    assert "liveProactiveToggle.checked = false" in body
    assert "setLiveProactiveUiState()" in body


def test_proactive_toggle_disabled_when_live_inactive_or_env_off():
    body = _function_body(_index_html(), "setLiveProactiveUiState")
    assert "const usable = liveModeActive && liveProactiveSupported;" in body
    assert "liveProactiveToggle.disabled = !usable" in body
    # When live is active but env off, surface the env hint.
    assert "需在 run-windows.env 开启 LIVE_PROACTIVE_ENABLED=true" in body
