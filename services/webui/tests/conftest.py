"""webui/tests conftest: importable webinfer modules + a sandboxed event stream.

The e2e test in ``test_jarvis_webinfer_e2e.py`` spins up a real
``live_adapter`` aiohttp app. ``live_adapter.py`` does
``from memory_summarizer import SummarizerModel`` /
``from memory_store_client import MemoryStoreClient`` at module-load
time, so both modules must be on sys.path **before** any test module is
collected.

Second responsibility (#156): **keep the test suite out of the real event
stream.** Several suites drive the live path for real, and the ADR-0014 emitter
resolves ``logs/events/<service>-<UTC day>.jsonl`` at import time — so without
this fixture every full run appends rows to the repo's own evidence file
(measured: 36 rows per run, 24 of them ``live_decision``). That file is
gitignored but it is still *evidence*: production rows and pytest rows became
indistinguishable, which quietly corrupts exactly the corpus the decision
evaluator reads.
"""

import sys
from pathlib import Path

import pytest

# conftest.py lives at services/webui/tests/conftest.py
# parents[0] = services/webui/tests
# parents[1] = services/webui
# parents[2] = services
# parents[3] = repo root
_REPO = Path(__file__).resolve().parents[3]
_WEBINFER = _REPO / "services" / "webinfer"
_WEBUI_SRC = _REPO / "services" / "webui" / "src"

for _p in (str(_WEBUI_SRC), str(_WEBINFER), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _sandbox_event_stream(tmp_path_factory, monkeypatch):
    """Redirect the shared ADR-0014 emitter at a per-test directory.

    Autouse and per-test so a suite that drives the live path for real (or
    deliberately breaks the sink) cannot reach the repository's ``logs/events``.
    Tests that need to inspect what was written redirect this themselves; this
    only guarantees the *default* is never the real tree.

    The emitter caches one logger per (service, UTC-day), so both the directory
    and that cache must be reset or a cached handler would keep writing to the
    original path.
    """
    try:
        import event_json
    except ImportError:  # pragma: no cover - emitter absent in stripped checkouts
        yield
        return

    sandbox = tmp_path_factory.mktemp("events")
    monkeypatch.setattr(event_json, "_EVENTS_DIR", str(sandbox), raising=False)
    monkeypatch.setattr(event_json, "_LOGGERS", {}, raising=False)
    yield sandbox
    # Close anything the test opened, so a stale handler cannot outlive it.
    for logger in list(event_json._LOGGERS.values()):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except OSError:  # cleanup only — never fail a test over this
                pass
    event_json._LOGGERS.clear()
