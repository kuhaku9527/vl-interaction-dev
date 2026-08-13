"""Jarvis dialog pure logic (extracted from ``jarvis_mode.py``).

Moved from ``jarvis_mode.py`` (spec codebase-map-2026-08-13.md §2.1,
priority 5): the DIALOG_ACTIVE turn's pure decisions — the exit-word
predicate (shared by the legacy and turn-delegate handlers) and the endpoint
commit verdict (garbage-drop / smart-turn-defer / send). ``JarvisStateMachine``
keeps the deep self-state orchestration (``_handle_dialog`` /
``_handle_dialog_legacy`` / ``_handle_dialog_delegated`` /
``_handle_dialog_commit``) in the class and delegates to these helpers;
behavior is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

from .jarvis_config import EXIT_WORDS


def exit_word_detected(text: str) -> bool:
    """True when a partial ASR text ends with one of the exit words.

    Moved verbatim from the inline check in
    ``JarvisStateMachine._handle_dialog_legacy`` /
    ``_handle_dialog_delegated`` (``stripped = text.strip().lower()`` then
    ``any(stripped.endswith(w) for w in EXIT_WORDS)``).
    """
    stripped = text.strip().lower()
    return any(stripped.endswith(w) for w in EXIT_WORDS)


def commit_verdict(
    utterance: str,
    is_garbage: Callable[[str], bool],
    smart_turn_allows_send: Callable[[str], bool],
) -> str:
    """Endpoint commit verdict: ``"garbage"`` | ``"deferred"`` | ``"sent"``.

    Moved verbatim from the decision block in
    ``JarvisStateMachine._handle_dialog_commit``. The two predicates are
    callables so the short-circuit order is preserved exactly: garbage is
    checked FIRST (the Smart Turn model is never invoked for noise), then
    the semantic gate, then the send path.
    """
    if is_garbage(utterance):
        return "garbage"
    if not smart_turn_allows_send(utterance):
        return "deferred"
    return "sent"


__all__ = [
    "commit_verdict",
    "exit_word_detected",
]
