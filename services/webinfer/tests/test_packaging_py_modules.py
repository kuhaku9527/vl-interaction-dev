"""Guard: `[tool.setuptools] py-modules` must match the package root exactly.

This adapter ships as a flat set of top-level modules (no package directory),
so every module the shipped code imports must be named explicitly in
`py-modules`. A module that is imported but *not* listed is silently absent
from the built wheel and `import live_adapter` fails outright for anyone
installing it.

That is exactly what happened in the batch-2/batch-3 refactors: splitting
`infer_loop.py` added `frame_parsing`, `chat_payload`, `stream_protocol` and
`silence_control`, the list was not updated, and the wheel went out missing
four modules that `infer_loop`/`adapter_core` import at module top-level.
The failure is invisible in a dev checkout (the files are on disk) and was
only caught by the `package-smoke` CI job, which installs the built wheel.

`package-smoke` is a necessary gate but not a sufficient one: it only
catches a missing module if something imports it *eagerly*. A module
imported lazily (inside a function, or behind a feature flag) would still
slip through, because the import would not run during the smoke test.

These tests close that hole structurally, with no build step:

  1. Every root-level `*.py` file is listed in `py-modules` (nothing can be
     omitted from the wheel, however it is imported).
  2. Every listed name still has a corresponding file (a rename or delete
     cannot leave a dead/empty entry behind).
  3. The count in the `pyproject.toml` comment matches reality (that comment
     already went stale once, silently, before this bug).
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PACKAGE_ROOT / "pyproject.toml"

_COUNT_COMMENT_RE = re.compile(
    r"^#\s*All\s+(\d+)\s+top-level modules shipped by this adapter package\.",
    re.MULTILINE,
)


def _declared_py_modules() -> list[str]:
    """Return the `[tool.setuptools] py-modules` list from pyproject.toml."""
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["py-modules"]


def _actual_root_modules() -> list[str]:
    """Return the stems of every `*.py` file at the package root."""
    return sorted(path.stem for path in PACKAGE_ROOT.glob("*.py"))


def test_every_root_module_is_declared_in_py_modules() -> None:
    """A root module missing from the list is missing from the wheel."""
    missing = sorted(set(_actual_root_modules()) - set(_declared_py_modules()))
    assert not missing, (
        f"root modules not declared in [tool.setuptools] py-modules: {missing}. "
        "They will be absent from the built wheel. Add them to "
        "services/webinfer/pyproject.toml and update the count comment."
    )


def test_every_declared_module_still_has_a_file() -> None:
    """A declared name with no file is a stale entry (rename/delete drift)."""
    stale = sorted(set(_declared_py_modules()) - set(_actual_root_modules()))
    assert not stale, (
        f"[tool.setuptools] py-modules names with no corresponding file: {stale}. "
        "Remove them from services/webinfer/pyproject.toml."
    )


def test_declared_modules_have_no_duplicates() -> None:
    """setuptools would silently tolerate a duplicate; keep the list clean."""
    declared = _declared_py_modules()
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    assert not duplicates, f"duplicate entries in py-modules: {duplicates}"


def test_count_comment_matches_declared_modules() -> None:
    """The prose count in pyproject.toml must not go stale silently."""
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = _COUNT_COMMENT_RE.search(text)
    assert match is not None, (
        "the 'All <N> top-level modules ...' comment above py-modules is gone; "
        "restore it (it is asserted here) or update this test."
    )
    assert int(match.group(1)) == len(_declared_py_modules()), (
        f"pyproject.toml comment says {match.group(1)} modules but py-modules "
        f"lists {len(_declared_py_modules())}."
    )
