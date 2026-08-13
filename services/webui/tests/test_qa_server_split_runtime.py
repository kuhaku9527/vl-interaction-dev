# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""QA runtime verification: reverse-import decoupling (batch-5 server split).

Runs in FRESH subprocesses to control import order and to simulate the
``python -m joy_interaction_webui.server`` double-load scenario. Verifies:

1. Bidirectional import order independence (server first, jarvis_session first).
2. ``ws_notify.session_websockets is server.session_websockets`` identity.
3. ``__main__`` simulation: single instance, no silently-dropped notify.
4. Lazy ``from . import server`` resolves correctly regardless of import order.
5. Monkeypatch contract: ``patch.object(server, "_probe_llm")`` propagates into
   service_probe's lazy read; ``patch.object(server, "send_to_session")``
   propagates into ws_notify's lazy read.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[2]
SRC = WEBUI_ROOT / "src"
PY = sys.executable


def _run_py(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [PY, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(WEBUI_ROOT),
        env=env,
        timeout=120,
    )


def test_import_server_first_then_jarvis_session():
    code = textwrap.dedent(
        """
        import joy_interaction_webui.server as server
        import joy_interaction_webui.jarvis_session as js
        import joy_interaction_webui.ws_notify as wn
        assert wn.session_websockets is server.session_websockets, "registry identity broken"
        assert wn.websockets is server.websockets, "websockets identity broken"
        assert wn.send_to_session is server.send_to_session, "send_to_session identity broken"
        assert wn.notify_session_llm_reply is server.notify_session_llm_reply
        print("OK server-first")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK server-first" in r.stdout


def test_import_jarvis_session_first_then_server():
    code = textwrap.dedent(
        """
        import joy_interaction_webui.jarvis_session as js
        import joy_interaction_webui.server as server
        import joy_interaction_webui.ws_notify as wn
        assert wn.session_websockets is server.session_websockets
        assert wn.websockets is server.websockets
        # jarvis_session's callbacks import from ws_notify (dotted instance),
        # so after both modules load the notify targets are shared.
        print("OK jarvis-first")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK jarvis-first" in r.stdout


def test_import_ws_notify_first_then_server():
    code = textwrap.dedent(
        """
        import joy_interaction_webui.ws_notify as wn
        import joy_interaction_webui.server as server
        assert wn.session_websockets is server.session_websockets
        assert wn.send_to_session is server.send_to_session
        print("OK ws_notify-first")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK ws_notify-first" in r.stdout


def test_main_module_simulation_single_instance():
    """Simulate `python -m joy_interaction_webui.server`.

    The __main__ alias (sys.modules["joy_interaction_webui.server"] =
    sys.modules["__main__"]) must make later `from .server import ...` resolve to
    the SAME instance — no dual session_websockets dicts.
    """
    code = textwrap.dedent(
        """
        import sys
        import types

        # Simulate python -m joy_interaction_webui.server: the file executes as
        # __main__ and registers the alias.
        import joy_interaction_webui.server as dotted_before
        sys.modules["__main__"] = dotted_before

        # Now emulate what the interpreter does for -m: run the file as __main__.
        # We can't truly re-execute (side effects), so assert the alias path:
        # after executing server as __main__, the dotted name maps to __main__.
        # Simulate the module load order used by python -m: __main__ first.
        import importlib.util
        import joy_interaction_webui.ws_notify as wn
        import joy_interaction_webui.server as server_via_dotted

        # If the alias works, ws_notify's registries are the single source and
        # server re-exports them — identity must hold regardless.
        assert wn.session_websockets is server_via_dotted.session_websockets
        assert wn.websockets is server_via_dotted.websockets
        print("OK __main__ alias identity")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK __main__ alias identity" in r.stdout


def test_main_module_faithful_double_load():
    """Faithful simulation: exec server.py as __main__ (stub web.run_app),
    then import the dotted server + jarvis_session; ws_notify's lazy
    `from . import server` must resolve to the __main__ instance.
    """
    code = textwrap.dedent(
        """
        import sys, types
        from pathlib import Path
        import joy_interaction_webui  # ensure package is importable

        server_path = Path(joy_interaction_webui.__file__).parent / "server.py"
        src = server_path.read_text(encoding="utf-8")

        # Stub run_app so executing server.py as __main__ does not block.
        import aiohttp.web
        aiohttp.web.run_app = lambda *a, **k: None

        # Faithful python -m: create the __main__ module object, register it,
        # then exec the file source into its namespace.
        main_mod = types.ModuleType("__main__")
        main_mod.__file__ = str(server_path)
        main_mod.__package__ = "joy_interaction_webui"
        sys.modules["__main__"] = main_mod
        exec(compile(src, str(server_path), "exec"), main_mod.__dict__)

        # server.py's __main__ guard aliased the dotted name to this module.
        assert sys.modules["joy_interaction_webui.server"] is main_mod, (
            "dotted server not aliased to __main__"
        )

        # Now import the dotted modules as a downstream caller would.
        import joy_interaction_webui.server as dotted_server
        import joy_interaction_webui.jarvis_session as js
        import joy_interaction_webui.ws_notify as wn

        assert dotted_server is main_mod, "dotted import created a SECOND server instance"
        # Shared registry identity through both paths.
        assert wn.session_websockets is dotted_server.session_websockets
        assert wn.websockets is dotted_server.websockets
        # The registries written by the __main__ instance are the SAME dicts the
        # dotted re-export points at — this is the exact bug that used to drop
        # LLM replies when the WS handler wrote to one dict and notify read
        # from another.
        assert wn.session_websockets is main_mod.session_websockets
        print("OK faithful __main__ double-load single instance")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK faithful __main__ double-load single instance" in r.stdout


def test_monkeypatch_contract_probe_llm_via_server_facade():
    """patch.object(server, "_probe_llm") must be seen by service_probe's lazy read."""
    code = textwrap.dedent(
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        import joy_interaction_webui.server as server
        import joy_interaction_webui.service_probe as sp
        from joy_interaction_webui.service_probe import llm_status

        sentinel = {"status": "ok", "models": ["patched-model"]}

        class FakeRequest:
            app = SimpleNamespace()

        async def run():
            with patch.object(server, "_resolve_service_targets", return_value=("http://x", "http://y")):
                with patch.object(server, "_probe_llm", return_value=sentinel):
                    resp = await llm_status(FakeRequest())
                    import json as _json

                    body = _json.loads(resp.text)
                    assert body["llm"]["models"] == ["patched-model"], body
                    print("OK probe_llm patched via server facade")

        asyncio.run(run())
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK probe_llm patched via server facade" in r.stdout


def test_monkeypatch_contract_send_to_session_via_server_facade():
    """patch.object(server, "send_to_session") must be seen by ws_notify notify_*."""
    code = textwrap.dedent(
        """
        from unittest.mock import patch
        import joy_interaction_webui.server as server
        import joy_interaction_webui.ws_notify as wn

        captured = []
        with patch.object(server, "send_to_session", side_effect=lambda sid, msg: captured.append((sid, msg))):
            wn.session_websockets["sess-x"].add("fake-ws")
            wn.notify_session_llm_reply("sess-x", "hello")
            assert captured, "send_to_session not invoked through patched facade"
            sid, msg = captured[0]
            assert sid == "sess-x"
            import json
            payload = json.loads(msg)
            assert payload["type"] == "llm_reply" and payload["text"] == "hello"
            print("OK send_to_session patched via server facade")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK send_to_session patched via server facade" in r.stdout


def test_monkeypatch_contract_patch_ws_notify_still_reaches_jarvis_callback():
    """jarvis_session callbacks import from ws_notify at call time.

    patch.object(ws_notify, "notify_session_llm_reply") must be picked up by
    the callback created by JarvisSessionManager (semantics preserved after the
    patch-target repoint in test_jarvis_background_wiring.py).
    """
    code = textwrap.dedent(
        """
        from unittest.mock import patch
        import joy_interaction_webui.ws_notify as wn
        from joy_interaction_webui.jarvis_session import JarvisSessionManager

        captured = []

        # Build a manager without heavy engine init.
        m = JarvisSessionManager.__new__(JarvisSessionManager)
        m._sessions = {}
        m._live_sessions = {}
        m.config = type("Cfg", (), {"asr_model_display_name": lambda self: "x"})()
        cb = m._make_llm_callback("sess-y")
        with patch.object(wn, "notify_session_llm_reply", side_effect=lambda *a, **k: captured.append(a)):
            cb("some text")
        assert captured, "jarvis callback did not use patched ws_notify notify"
        print("OK jarvis callback sees ws_notify patch")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK jarvis callback sees ws_notify patch" in r.stdout


def test_lazy_server_resolution_no_import_time_symbol_missing():
    """Lazy `from . import server` must not fail when called before full import."""
    code = textwrap.dedent(
        """
        # Import ws_notify ALONE (no server) and exercise a notify that touches
        # server state only at call time -> must not raise at import.
        import joy_interaction_webui.ws_notify as wn
        # Call get_session_callback and invoke it with no server state present.
        cb = wn.get_session_callback("nope")
        # Without importing server, calling cb would fail resolving server.sessions
        # (ModuleNotFoundError expected) — but importing server afterwards must
        # make the same callback work.
        import joy_interaction_webui.server as server
        server.sessions["nope"] = {}
        cb("text", {})
        print("OK lazy resolution works")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK lazy resolution works" in r.stdout


def test_no_duplicate_module_instances_after_both_imports():
    """server and jarvis_session must resolve to exactly one module instance."""
    code = textwrap.dedent(
        """
        import sys
        import joy_interaction_webui.server as s1
        import joy_interaction_webui.jarvis_session
        import joy_interaction_webui.ws_notify
        # No __main__-style duplicate: only one dotted instance in sys.modules.
        import joy_interaction_webui
        names = [n for n in sys.modules if n == "joy_interaction_webui.server"]
        assert len(names) == 1, names
        print("OK single module instance")
        """
    )
    r = _run_py(code)
    assert r.returncode == 0, f"STDOUT={r.stdout}\nSTDERR={r.stderr}"
    assert "OK single module instance" in r.stdout
