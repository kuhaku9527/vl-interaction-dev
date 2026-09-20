"""Single source of truth for the front-end "static contract" corpus.

Why this module exists
----------------------

A dozen contract tests in this directory read the front-end as **text** and
assert that specific constructs exist (e.g. ``assert "let liveModeActive = false"
in html``).  They do not render anything -- they grep a merged source blob built
from ``index.html`` plus the standalone JS modules that the "batch-3 split"
extracted out of it.

That module list used to be **hardcoded** in all twelve files.  It went stale
twice, and each time it failed the same way: the code had merely *moved*, so the
assertions lost sight of it and reported a failure that was not real (see
``doc/standards/webui-design-standards.md`` §9.7, "搬家协会破坏静态契约测试").

* First staleness: the inline script was extracted into 14 modules, but the
  list still named the old inline block.
* Second staleness: ``joy_state.js`` / ``radio_silence.js`` were added as new
  modules and never appended to the tuple, so the tests were searching a corpus
  that was missing two real, loaded files.

Deriving the list from ``index.html`` (rather than listing names) makes a third
staleness impossible: any module the page loads is in scope automatically.

Preserving the original semantics
---------------------------------

The corpus was never "index.html + every .js file" -- it was "index.html + the
modules split *out of* index.html".  A naive ``glob('*.js')`` would silently
widen it with modules that were already standalone ``<script src>`` files before
the split, changing what the assertions search and inviting both false passes
and false failures.  So the derivation is:

    split modules = (scripts loaded by index.html, in load order)
                    MINUS PRE_EXISTING_MODULES

``PRE_EXISTING_MODULES`` is the frozen set of modules that predate the split.
The result is deterministic (``index.html`` load order), and it grows only by
modules someone actually wired into the page.

Fail-closed
-----------

If ``index.html`` references a script that is missing on disk, or if the
derivation yields an empty set, importing this module raises.  Silently
shrinking the corpus is exactly the failure mode this module exists to prevent,
so it must never degrade quietly.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = WEBUI_ROOT / "src" / "joy_interaction_webui" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
STYLES_CSS = STATIC_DIR / "styles.css"

# Modules that were already standalone <script src> files BEFORE the batch-3
# inline-script split.  They were never part of the concatenated corpus these
# assertions were written against, so they stay excluded -- this is what keeps
# the corpus semantically identical to the historical 14-name tuple.
PRE_EXISTING_MODULES = frozenset(
    {
        "screen_capture.js",
        "capture_webcam.js",
        "capture_rtsp.js",
        "render_markdown.js",
        "sanitize_static_html.js",
        "config_services.js",
        "wiki_frontend.js",
        "joy_ws.js",
        "i18n_device_label.js",
    }
)

_SRC_RE = re.compile(r"<script[^>]*\ssrc=\"\./([^\"]+\.js)\"")


def _derive_split_js() -> tuple[str, ...]:
    if not INDEX_HTML.is_file():
        raise RuntimeError(f"front-end index.html not found at {INDEX_HTML}")

    html = INDEX_HTML.read_text(encoding="utf-8")

    # Load order, de-duplicated: deterministic and independent of the filesystem.
    loaded: list[str] = []
    for name in _SRC_RE.findall(html):
        if name not in loaded:
            loaded.append(name)

    if not loaded:
        raise RuntimeError(
            f'no local <script src="./*.js"> found in {INDEX_HTML}; refusing to '
            "silently scan an empty corpus"
        )

    missing = [name for name in loaded if not (STATIC_DIR / name).is_file()]
    if missing:
        raise RuntimeError(
            f"{INDEX_HTML.name} references scripts that do not exist: "
            f"{', '.join(missing)}; refusing to silently shrink the corpus"
        )

    split = tuple(name for name in loaded if name not in PRE_EXISTING_MODULES)
    if not split:
        raise RuntimeError(
            "derived split-module set is empty; refusing to silently shrink the corpus"
        )
    return split


#: The split-out modules, in ``index.html`` load order.
SPLIT_JS = _derive_split_js()


def index_html_plus_split_js() -> str:
    """``index.html`` followed by every split-out module, as one searchable blob."""
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    parts.extend((STATIC_DIR / name).read_text(encoding="utf-8") for name in SPLIT_JS)
    return "\n".join(parts)


def styles_css() -> str:
    """The linked stylesheet (CSS was extracted out of index.html)."""
    return STYLES_CSS.read_text(encoding="utf-8")
