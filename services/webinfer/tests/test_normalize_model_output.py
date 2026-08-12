"""Tests for :func:`normalize_model_output` (multi-line reply preservation).

Root-cause fix: ``normalize_model_output`` previously collapsed the response
body to its FIRST line only, silently dropping every subsequent line of a
multi-line reply from the display ``generated_text`` / ``content`` (user saw
"model only said one sentence"). The body after ``</response>`` is now kept
in full (whitespace-stripped), while the decision-token semantics are
unchanged:

  * empty output              -> ``</silence>``
  * contains ``</silence>``   -> ``</silence>``
  * ``</response>`` + nothing -> ``</response>``
  * ``</response>`` + body    -> ``</response> <full body>``
  * no decision token at all  -> ``</response> <full body>`` when non-empty

``parse_model_decision`` consumes the RAW text independently and is not
affected by this function.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from response_format import extract_response_payload, normalize_model_output  # noqa: E402


def test_multi_line_reply_is_fully_preserved() -> None:
    """All lines after ``</response>`` survive (the root-cause regression)."""
    raw = "</response> 第一步: 先打开设置。\n第二步: 再选择网络。\n第三步: 重启设备。"
    assert normalize_model_output(raw) == (
        "</response> 第一步: 先打开设置。\n第二步: 再选择网络。\n第三步: 重启设备。"
    )


def test_single_line_reply() -> None:
    """A single-line reply keeps the existing one-line behaviour."""
    assert normalize_model_output("</response> 你好") == "</response> 你好"


def test_empty_reply_returns_silence() -> None:
    """Empty/whitespace-only output maps to ``</silence>`` (unchanged)."""
    assert normalize_model_output("") == "</silence>"
    assert normalize_model_output("   \n  ") == "</silence>"


def test_silence_token_returns_silence() -> None:
    """``</silence>`` anywhere maps to ``</silence>`` (unchanged)."""
    assert normalize_model_output("</silence>") == "</silence>"
    assert normalize_model_output("  </silence>  ") == "</silence>"


def test_response_token_with_no_body() -> None:
    """``</response>`` with no trailing content stays ``</response>``."""
    assert normalize_model_output("</response>") == "</response>"
    assert normalize_model_output("</response>   ") == "</response>"


def test_no_token_plain_text_returns_response() -> None:
    """Text without any decision token is wrapped as a response (unchanged)."""
    assert normalize_model_output("just talking") == "</response> just talking"


def test_multi_line_with_leading_trailing_whitespace() -> None:
    """Leading/trailing blank lines are stripped; inner lines are kept."""
    raw = "\n  \n</response>\n\n  第一行  \n  第二行  \n\n  "
    assert normalize_model_output(raw) == "</response> 第一行  \n  第二行"


def test_crlf_and_cr_are_normalized() -> None:
    """CRLF/CR line endings are converted to LF before body extraction."""
    raw = "</response> line one\r\nline two\rline three"
    assert normalize_model_output(raw) == "</response> line one\nline two\nline three"


def test_earliest_marker_wins() -> None:
    """The earliest of ``</response>``/``</silence>`` decides the outcome."""
    assert normalize_model_output("</silence> </response> note") == "</silence>"
    assert normalize_model_output("</response> note </silence>") == "</response> note </silence>"


def test_extract_response_payload_keeps_multi_line() -> None:
    """Payload extraction over normalized output preserves the full body."""
    normalized = normalize_model_output("</response> 第一行\n第二行\n第三行")
    assert extract_response_payload(normalized) == "第一行\n第二行\n第三行"


def test_extract_response_payload_empty_after_normalization() -> None:
    """Payload extraction returns ``None`` for silence / empty bodies."""
    assert extract_response_payload("</silence>") is None
    assert extract_response_payload("</response>") is None
