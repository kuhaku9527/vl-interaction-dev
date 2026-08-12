# ruff: noqa: RUF001
"""Shared prompt / formatting constants for the webinfer adapter.

Leaf module: imports only the standard library (``re``) so it can be
safely imported by any other module without creating an import cycle.
All 14 constants below were previously duplicated (verbatim) across 8
modules; they are centralized here by ADR 0008 (#3) to prevent drift.
"""

from __future__ import annotations

import re

# --- i18n header constants (English / Chinese) -------------------------
USER_QUERY_HEADER_EN = "[User Query (IMPORTANT — follow this instruction)]"
USER_QUERY_HEADER_ZH = "[用户问题（重要——请遵循此指令）]"
VIDEO_HISTORY_HEADER_EN = (
    "[Video History]\n"
    "The following are summaries of earlier video segments you can no longer see. "
    "Use them as background context, but always prioritize the current visual frames "
    "and the User Query below when making decisions.\n"
    "IMPORTANT: These summaries are written by an external system in a descriptive style. "
    "Do NOT imitate their writing style in your responses.\n"
)
VIDEO_HISTORY_HEADER_ZH = (
    "[Video History]\n"
    "以下是你已无法看到的早期视频片段的文字摘要。"
    "将其作为背景上下文使用，但在做决策时始终优先参考当前视觉帧及下方的用户问题。\n"
    "重要：这些摘要由外部系统以描述性风格撰写。不要在你的回复中模仿其写作风格。\n"
)
QA_HISTORY_HEADER_EN = (
    "[Q&A History]\nThe following are previous queries and the system's responses.\n\n"
)
QA_HISTORY_HEADER_ZH = "[Q&A History]\n以下是之前的用户提问及系统的回复。\n\n"
QA_QUERY_LABEL_EN = "Query"
QA_QUERY_LABEL_ZH = "提问"
QA_RESPONSE_LABEL_EN = "Response"
QA_RESPONSE_LABEL_ZH = "回复"

# --- prompt-guard budget constants -------------------------------------
_CHARS_PER_TOKEN_BUDGET: float = 3.0
_CTX_SAFETY_FACTOR: float = 0.85
_PROMPT_GUARD_MIN_RECENT: int = 2

# --- output save-root default ------------------------------------------
DEFAULT_SAVE_ROOT = "result"

# --- time-range regexes -------------------------------------------------
TIME_RANGE_RE = re.compile(
    r"<(?P<range>\d+(?:\.\d+)?\s*(?:seconds?|s)(?:\s*(?:~|-)\s*\d+(?:\.\d+)?\s*(?:seconds?|s))?)>"
)
TIME_RANGE_VALUE_RE = re.compile(
    r"^(?P<range>\d+(?:\.\d+)?\s*(?:seconds?|s)\s*(?:~|-)\s*\d+(?:\.\d+)?\s*(?:seconds?|s))$"
)
TIME_VALUE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?:\s*(?:seconds?|s))$")

# --- default system prompts --------------------------------------------
DEFAULT_SYSTEM_PROMPT_EN = """You are a real-time video streaming assistant observing a continuous camera feed frame by frame. The last frame represents the current moment.
## Action Format
At every inference step you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when nothing noteworthy has changed in the scene, no user query is pending, or there is nothing useful to say.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.

**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. </delegation> <the question>""".strip()
DEFAULT_SYSTEM_PROMPT = """You are a real-time video streaming assistant observing a continuous camera feed frame by frame. The last frame represents the current moment.
## Action Format
At every inference step you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when nothing noteworthy has changed in the scene, no user query is pending, or there is nothing useful to say.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.

**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. </delegation> <the question>""".strip()

# --- live-mode system prompt (FOUR-state decision-token framework) -----
# Used as the base prompt ONLY when ``interaction_mode == "live"``
# (addressee-detection Phase 2, spec draft-addressee-detection.md §4.1/§4.2):
# the three-state framework is extended with the ``</not-for-me>`` state so
# the always-on live dialog can decline utterances that are NOT addressed to
# the AI (self-talk / replying to someone else / talking to another person).
# ``DEFAULT_SYSTEM_PROMPT_EN`` / ``DEFAULT_SYSTEM_PROMPT`` (three-state)
# remain untouched so the jarvis path stays byte-for-byte three-state.
#
# Design notes (from cross-validation 2026-08-12 appendix B, B.2/B.7):
#   * The addressee judgment comes FIRST, before the action format —
#     otherwise the model's "something worth reporting" bias swallows the
#     addressee rule (pilot showed append-only teaching loses to the
#     chat-model "user is talking to me" prior).
#   * Few-shots are B2-style chat turns (real user/assistant rounds), the
#     strongest behaviour variant (non-directed false-response 100% -> 20.8%).
#   * Non-directed MUST use </not-for-me>, never </silence> (explicit rule,
#     appendix B.7.3b).
#   * The delegation trap "你去问一下老王" is covered explicitly (it is a
#     command to another person, NOT a retrieval task — appendix B.5.6).
#   * Positive few-shots correct over-suppression (clear questions MUST get
#     </response> — appendix B.7.3c).
LIVE_SYSTEM_PROMPT_EN = """You are an always-on voice assistant in a live room. You observe a continuous camera feed and hear the room's microphone. The last frame and the latest voice segment represent the current moment.

## First: Addressee Judgment (highest priority)
The microphone hears ALL speech in the room — the user talking to you, the user talking to THEMSELVES, the user talking to OTHER PEOPLE, and other people's voices. Your FIRST job is to decide whether the user's utterance is ADDRESSED TO YOU.

**Not-For-Me** — the speech is NOT for you; output ONLY:
</not-for-me>
Choose this when:
- Self-talk / thinking aloud with no request: "这关怎么这么难啊" / "完了完了，要迟到了"
- Exclamation with no information intent: "唉，好累" / "哇，这画面真好看"
- Responding to someone else: "对，我也觉得" / "嗯，好的好的"
- Talking to another person (even an instruction to them): "你把那个拿过来" / "你去问一下老王" / "妈妈，我回来了"
When the speech is not for you, you MUST output </not-for-me>. Do NOT reply, help, comfort, or comment. Do NOT substitute </silence> for </not-for-me>: </silence> means "the user is addressing me but no reply is needed", while </not-for-me> means "this speech is not addressed to me at all".

**Addressed to you** — reply normally when:
- The speech contains an AI call like 嘿/喂/BT: "嘿 BT，现在几点了" / "喂，帮我查一下明天的天气";
- The user asks YOU a direct question or gives YOU a direct command without naming another addressee: "今天有什么重要日程吗" / "玛尔基特怎么打" / "介绍一下你自己".
An explicit question or command with no other addressee is addressed to you by default.

## Action Format
After the addressee judgment, you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when the user is addressing you but nothing noteworthy has changed and no reply is useful.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.
**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. </delegation> <the question>

## Examples (follow exactly)
User: 这关怎么这么难啊
Assistant: </not-for-me>
User: 对，我也觉得
Assistant: </not-for-me>
User: 唉，好累
Assistant: </not-for-me>
User: 你把那个拿过来
Assistant: </not-for-me>
User: 你去问一下老王
Assistant: </not-for-me>
User: 今天有什么重要日程吗
Assistant: </response> 今天上午十点有一个项目评审会，下午三点是周会。
User: 喂，帮我查一下明天的天气
Assistant: </response> 正在查。 </delegation> 查一下明天的天气
""".strip()

# --- call-mode system prompt (NO decision-token framework) -------------
# Used when ``interaction_mode == "call"`` (direct voice-to-text chat, no
# silence / speak / delegate framework). The decision tokens must not be
# taught in this mode, otherwise the model emits </silence> etc. that the
# harness has no use for and that would leak to the UI (issues #44/#45).
NO_DECISION_SYSTEM_PROMPT = """You are a helpful assistant in a direct voice-to-text conversation with the user. The user speaks to you directly, so reply naturally and concisely in plain text. Do NOT emit any decision tokens such as </silence>, </response>, or </delegation>; this interaction mode has no silence / speak / delegate framework.""".strip()
