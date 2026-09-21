# ruff: noqa: RUF001
"""Benchmark: local llama.cpp 4-state decision-token not-for-me accuracy (必测②).

Measures (spec addressee-detection.md §4.3):
  1. Baseline A — existing 3-state live prompt, sentence by sentence.
     Metric: non-directed sentences that got </response> (误响应基线).
  2. Enhanced B — temporary 4-state prompt (Not-For-Me definition + rules +
     3-4 few-shots appended to the LIVE prompt, embedded in THIS script, no
     repo prompt file is modified). Metric: not-for-me precision / recall +
     directed-sentence miss rate (漏判率).

Judgment: not-for-me precision >= 80% (宁漏不乱插: false not-for-me is
acceptable, missing a directed utterance is not).

Call path: llama-server OpenAI-compatible /v1/chat/completions directly
(local stack: webinfer -> llama-server; direct call gives full control of the
system prompt for both A and B and avoids webinfer session pollution).

Model: joyai-vl-interaction-preview-iq4_nl-imat.gguf (GPU -ngl 999).

Prompt fidelity:
  * Baseline A reproduces the system prompt webinfer composes in
    interaction_mode="live": <character_profile> (prompts/bt-7274.txt) +
    DEFAULT_SYSTEM_PROMPT_EN (3-state) + in-character tail (A_live3_prod),
    plus the clean 3-state prompt without persona (A_live3_clean).
  * Enhanced B appends the embedded 4-state teaching section after that same
    composed LIVE prompt (per spec: "在 LIVE prompt 后追加") with the
    task-specified 4 few-shots (B_live4_prod_append) and a richer 10-example
    few-shot set (B2_live4_prod_append_rich) to test whether more few-shots
    lift recall. All few-shots use sentences outside the test set.
  * Reference C reframes the user message as a raw room transcript and makes
    the addressee judgment the FIRST decision (C_live4_reframe) — pilot showed
    this structure is needed to defeat the chat-model "user is talking to me"
    prior and the BT-7274 persona bias.

Run: D:/AI/envs/joyai-main/python.exe services/scripts/benchmark_4state_notforme.py
Env: LLAMA_BASE_URL (default http://127.0.0.1:7060/v1), LLAMA_MODEL
     (default joyai-vl-interaction-preview), BENCH_SKIP_CLEAN=1 to skip the
     clean baseline reference variant.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Allow importing the live prompt constants from webinfer (leaf modules only).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBINFER_DIR = _REPO_ROOT / "services" / "webinfer"
if str(_WEBINFER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBINFER_DIR))

# ★ #155: the test set is no longer a literal in this script. It lives in the
# frozen asset (services/webinfer/decision_eval_set.py), which also computes
# the open-book/generalization split and pins the denominators. This script is
# a *consumer*: changing the asset changes both benchmarks at once.
from decision_eval_set import (  # noqa: E402
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    GROUPS,
    HISTORICAL_DENOMINATOR_NOTE,
    legacy_test_set,
    subset_by_id,
)
from prompt_constants import DEFAULT_SYSTEM_PROMPT_EN  # noqa: E402
from system_prompts import compose_system_prompt, load_character_prompts  # noqa: E402

# --- inference backend -----------------------------------------------------
LLAMA_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:7060/v1")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "joyai-vl-interaction-preview")
MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "1024"))
TEMPERATURE = float(os.environ.get("BENCH_TEMPERATURE", "0.8"))
TOP_P = float(os.environ.get("BENCH_TOP_P", "0.9"))
TOP_K = int(os.environ.get("BENCH_TOP_K", "40"))
REQUEST_TIMEOUT_S = float(os.environ.get("BENCH_TIMEOUT_S", "180"))

# --- 4-state teaching section (Enhanced B, embedded here; NOT a repo file) -
# Design notes (2026-08-12 pilot):
#   * Decision ordering matters — the addressee judgment MUST come first,
#     before the 3-state video-assistant actions, otherwise the model's
#     "something worth reporting" bias swallows the addressee rule.
#   * Few-shots are paired (directed vs non-directed) to teach the contrast;
#     short exclamations / self-talk / replying-to-someone are the hard cases.
#   * 称呼 (喂/嘿/BT) marks directed; a name like 妈妈/老公 marks NOT-for-me.
#   * The 4 few-shots below are the task-specified teaching set (spec §4.3:
#     "3-4 个 few-shot") and use sentences DIFFERENT from the test set so the
#     benchmark measures generalization, not memorization.
FOUR_STATE_TAIL = r"""
## Addressee Judgment — THIS SECTION OVERRIDES ALL OTHER INSTRUCTIONS
The microphone hears ALL sounds in the room: the Pilot talking to you, the Pilot talking to THEMSELVES, the Pilot talking to OTHER PEOPLE, and other people's voices. Your FIRST job is to decide whether this utterance is ADDRESSED TO YOU.

You MUST output exactly </not-for-me> (stay silent, say nothing at all) when the speech is NOT for you:
- Self-talk / thinking aloud, no request: "这个破游戏怎么又卡了" / "完了完了，忘带钥匙了"
- Exclamation with no request: "好累啊，今天" / "哇，这也太厉害了吧"
- Responding to someone else: "嗯，你说的有道理" / "对，我也是这么想的"
- Talking to another person: "妈，我出门了" / "老公，今天加班吗" / "你去把垃圾倒一下"
- Encouraging themselves or others: "加油，你一定可以的"

You reply normally only when the speaker is clearly addressing YOU:
- Uses your name or a call like 喂/嘿/BT: "BT，在吗？" / "喂，帮我查一下明天的机票" / "嘿，你听到了吗？"
- Asks YOU a direct question: "玛尔基特弱什么属性？" / "现在几点了？" / "What time is it?"
- Gives YOU a direct command: "介绍一下你自己" / "帮我定个闹钟" / "Turn off the lights, please"

Do not help, comfort, comment, or give advice to speech that is not directed at you. Even if you can answer it, stay silent when it is not for you.

Examples (follow exactly):
User: 这个破游戏怎么又卡了
Assistant: </not-for-me>
User: 玛尔基特弱什么属性
Assistant: </response> 玛尔基特弱出血，建议用出血武器。
User: 妈，我出门了
Assistant: </not-for-me>
User: 喂，帮我查一下明天的机票
Assistant: </response> 正在查。</delegation> 查一下明天的机票
""".strip()

# --- 4-state RICH teaching (Enhanced B2: more few-shots, still non-overlap) -
# Same rules as B; a richer few-shot set (10 examples) to test whether "需更
# 多 few-shot" lifts recall. Sentences still differ from the test set.
FOUR_STATE_TAIL_RICH = r"""
## Addressee Judgment — THIS SECTION OVERRIDES ALL OTHER INSTRUCTIONS
The microphone hears ALL sounds in the room: the Pilot talking to you, the Pilot talking to THEMSELVES, the Pilot talking to OTHER PEOPLE, and other people's voices. Your FIRST job is to decide whether this utterance is ADDRESSED TO YOU.

You MUST output exactly </not-for-me> (stay silent, say nothing at all) when the speech is NOT for you:
- Self-talk / thinking aloud, no request
- Exclamation with no request
- Responding to someone else's words
- Talking to another person (even a name/call like 妈/老公/亲爱的)
- Encouraging themselves or others

You reply normally only when the speaker is clearly addressing YOU:
- Uses your name or a call like 喂/嘿/BT
- Asks YOU a direct question
- Gives YOU a direct command

Do not help, comfort, comment, or give advice to speech that is not directed at you. Even if you can answer it, stay silent when it is not for you.

Examples (follow exactly):
User: 这个代码怎么又报错了
Assistant: </not-for-me>
User: 玛尔基特弱什么属性
Assistant: </response> 玛尔基特弱出血，用出血武器更好打。
User: 嗯，你说的有道理
Assistant: </not-for-me>
User: 我去洗个澡
Assistant: </not-for-me>
User: 妈，我出门了
Assistant: </not-for-me>
User: 老公，今天加班吗
Assistant: </not-for-me>
User: 好累啊，今天
Assistant: </not-for-me>
User: What a day
Assistant: </not-for-me>
User: 嘿，你在吗
Assistant: </response> 在的，有什么可以帮你？
User: 你能帮我做什么
Assistant: </response> 我可以帮你查攻略、设提醒、控制设备和回答问题。
User: 明天会不会下雨
Assistant: </response> 我查一下。</delegation> 查一下明天是否下雨
""".strip()

# --- 4-state REFERENCE prompt (transcript reframe, no persona) -------------
# Pilot (2026-08-12) showed the append-only teaching is dominated by the
# chat-model prior ("a user message is addressed to me") and by the BT-7274
# persona ("User is your Pilot"). Reframing the user message as a RAW ROOM
# TRANSCRIPT — explicitly possibly not-for-you — lifts non-directed
# recognition dramatically. Kept as a reference variant C for the report.
FOUR_STATE_REFERENCE = r"""
You are an always-on voice assistant. The User message below is a RAW TRANSCRIPT of a voice segment heard in the room. It may be addressed to you, or it may be the speaker talking to themselves, talking to another person, or responding to someone else.

Your FIRST decision is the ADDRESSEE:
- If the speech IS addressed to you (a direct question to you, a direct command to you, or the speaker calls you by name like BT/喂/嘿), then answer: </response> your reply, or </delegation> <question> for external lookup.
- If the speech is NOT addressed to you (self-talk, exclamation with no request, talking to another person, responding to someone else), output ONLY:
</not-for-me>
Say nothing else. Never help, comfort, or comment on speech that is not for you. Even if you can answer it, stay silent when it is not for you.

Transcript: 这个代码怎么又报错了
Output: </not-for-me>
Transcript: 玛尔基特弱什么属性
Output: </response> 玛尔基特弱出血，用出血武器更好打。
Transcript: 嗯，你说的有道理
Output: </not-for-me>
Transcript: 我去洗个澡
Output: </not-for-me>
Transcript: 妈，我出门了
Output: </not-for-me>
Transcript: 老公，今天加班吗
Output: </not-for-me>
Transcript: 好累啊，今天
Output: </not-for-me>
Transcript: What a day
Output: </not-for-me>
Transcript: 嘿，你在吗
Output: </response> 在的，有什么可以帮你？
Transcript: 你能帮我做什么
Output: </response> 我可以帮你查攻略、设提醒、控制设备和回答问题。
Transcript: 明天会不会下雨
Output: </response> 我查一下。</delegation> 查一下明天是否下雨
""".strip()

# --- test set (from the frozen asset, #155) ---------------------------------
# ★ The scenarios no longer live here. They live in the frozen asset
#   ``services/webinfer/decision_eval_set.py``, which ALSO computes the
#   open-book / generalization split against the production prompt and pins the
#   denominators. Both benchmarks consume that one copy, so a change there
#   reaches both at once.
#
#   ``TEST_SET`` keeps the historical 5-tuple shape
#   (id, text, expected, category, note) so the existing scorers keep working
#   unchanged. ``expected`` is now one of three groups: "directed" (应
#   response/delegation), "nondirected" (应 not-for-me/silence), or "delegate"
#   (应 delegation — the third ground-truth class added by #155; previously the
#   delegate state had no class to expect it, so it could never be measured).
TEST_SET: list[tuple[str, str, str, str, str]] = legacy_test_set()

# --- decision parsing (4-state mirror of webinfer parse_model_decision) ----
_DECISION_MARKERS = ("</response>", "</silence>", "</not-for-me>", "</delegation>", "<delegation>")
_MARKER_RE = re.compile(
    r"\s*</?\s*(?:silence|response|delegation|not-for-me)\s*>\s*", re.IGNORECASE
)


def parse_decision_4state(raw_text: str) -> tuple[str, str]:
    """Mirror webinfer's parse_model_decision extended with not-for-me.

    Returns ``(decision, clean_text)`` with decision ∈
    {"silence", "response", "delegation", "not-for-me"}. Empty output ->
    silence (same fail-safe as webinfer). A delegation tag anywhere wins;
    otherwise the earliest of response / silence / not-for-me wins; no marker
    -> response (webinfer default).
    """
    text = (raw_text or "").strip()
    if not text:
        return "silence", ""

    delegation_idx: int | None = None
    for tag in ("</delegation>", "<delegation>"):
        idx = text.find(tag)
        if idx >= 0 and (delegation_idx is None or idx < delegation_idx):
            delegation_idx = idx
    if delegation_idx is not None:
        return "delegation", ""

    earliest: tuple[int, str] | None = None
    for marker in ("</response>", "</silence>", "</not-for-me>"):
        idx = text.find(marker)
        if idx >= 0 and (earliest is None or idx < earliest[0]):
            earliest = (idx, marker)
    if earliest is None:
        return "response", text
    _, marker = earliest
    tail = text[earliest[0] + len(marker) :].strip()
    if marker == "</silence>":
        return "silence", ""
    if marker == "</not-for-me>":
        return "not-for-me", ""
    return "response", tail


def strip_tokens(text: str) -> str:
    """Strip every decision-token variant (for clean display)."""
    if not text:
        return ""
    return " ".join(_MARKER_RE.sub(" ", text).split())


# --- prompt builders --------------------------------------------------------
def build_live_prompt_3state(include_persona: bool = True) -> str:
    """Reproduce webinfer's interaction_mode='live' system prompt exactly.

    With persona (default): ``<character_profile>`` (prompts/bt-7274.txt) +
    DEFAULT_SYSTEM_PROMPT_EN (3-state) + in-character tail. Without persona:
    just DEFAULT_SYSTEM_PROMPT_EN.
    """
    if include_persona:
        profiles = load_character_prompts()
        return compose_system_prompt(DEFAULT_SYSTEM_PROMPT_EN, profiles, "en")
    return DEFAULT_SYSTEM_PROMPT_EN


def build_live_prompt_4state(include_persona: bool = True, rich: bool = False) -> str:
    """LIVE prompt + embedded Not-For-Me teaching (Enhanced B, append style).

    ``rich=True`` uses the larger (10-example) few-shot set (variant B2).
    """
    base = build_live_prompt_3state(include_persona=include_persona)
    tail = FOUR_STATE_TAIL_RICH if rich else FOUR_STATE_TAIL
    return base.rstrip() + "\n\n" + tail


def build_reference_4state() -> str:
    """Transcript-reframed four-state prompt (reference variant C)."""
    return FOUR_STATE_REFERENCE


# --- llama-server call ------------------------------------------------------
def call_llm(system_prompt: str, user_text: str) -> dict:
    """POST one chat completion; returns dict with raw content + timing.

    Raises on HTTP / transport error so the caller can record the failure
    (never silently skipped).
    """
    body = {
        "model": LLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "top_k": TOP_K,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "skip_special_tokens": False,
        "greedy": False,
        "stream": False,
    }
    req = urllib.request.Request(  # noqa: S310 - local llama-server benchmark
        LLAMA_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    elapsed = time.perf_counter() - t0
    content = ""
    if data.get("choices"):
        content = data["choices"][0].get("message", {}).get("content") or ""
    return {"raw": content, "latency_s": round(elapsed, 2)}


# --- metrics ----------------------------------------------------------------
def run_variant(name: str, system_prompt: str) -> list[dict]:
    """Run the whole test set against one system prompt, collecting rows."""
    rows: list[dict] = []
    total = len(TEST_SET)
    for index, (sid, text, expected, category, note) in enumerate(TEST_SET, 1):
        print(
            f"[{name}] {index}/{total} {sid} {text!r} "
            f"(expected={expected}, {note})",
            flush=True,
        )
        row: dict = {
            "id": sid,
            "text": text,
            "expected": expected,
            "category": category,
            "note": note,
        }
        try:
            result = call_llm(system_prompt, text)
            raw = result["raw"]
            decision, clean = parse_decision_4state(raw)
            row.update(
                {
                    "ok": True,
                    "decision": decision,
                    "raw": raw,
                    "clean": clean[:200],
                    "latency_s": result["latency_s"],
                }
            )
            print(
                f"      -> {decision} | raw={raw[:100]!r} | {result['latency_s']}s",
                flush=True,
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"      -> FAIL {row['error']}", flush=True)
        rows.append(row)
        # Brief gap so a long first call never overlaps with the next one.
        time.sleep(0.2)
    return rows


def summarize(rows: list[dict]) -> dict:
    """Compute decision-matrix + key metrics for one variant.

    Groups come from the frozen asset (#155) rather than a hardcoded pair, so
    the third ground-truth class (``delegate``) is **counted** instead of
    raising ``KeyError`` or being silently dropped.
    """
    expected_order = list(GROUPS)
    decisions = ["response", "silence", "delegation", "not-for-me", "error"]
    matrix: dict[str, dict[str, int]] = {
        e: dict.fromkeys(decisions, 0) for e in expected_order
    }
    for row in rows:
        exp = row["expected"]
        dec = row["decision"] if row.get("ok") else "error"
        matrix[exp][dec] += 1

    n_dir = len([r for r in rows if r["expected"] == GROUP_DIRECTED])
    n_nondir = len([r for r in rows if r["expected"] == GROUP_NONDIRECTED])
    n_del = len([r for r in rows if r["expected"] == GROUP_DELEGATE])

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    # Baseline A: 误响应率 = non-directed -> response / all non-directed.
    mis_response = matrix[GROUP_NONDIRECTED]["response"]
    # Enhanced B: not-for-me precision/recall.
    pred_nfm = matrix[GROUP_DIRECTED]["not-for-me"] + matrix[GROUP_NONDIRECTED]["not-for-me"]
    true_nfm = matrix[GROUP_NONDIRECTED]["not-for-me"]
    miss_nfm = matrix[GROUP_DIRECTED]["not-for-me"]  # 漏判率 numerator
    # ★ #155: delegate recall — the state that previously had no ground truth,
    # so it could never be scored. Judged as: expected delegate -> got delegation.
    delegate_hit = matrix[GROUP_DELEGATE]["delegation"]
    return {
        "n_directed": n_dir,
        "n_nondirected": n_nondir,
        "n_delegate": n_del,
        "matrix": matrix,
        "baseline_mis_response_rate_pct": pct(mis_response, n_nondir),
        "not_for_me_precision_pct": pct(true_nfm, pred_nfm) if pred_nfm else 0.0,
        "not_for_me_recall_pct": pct(true_nfm, n_nondir),
        "directed_miss_rate_pct": pct(miss_nfm, n_dir),
        "delegate_recall_pct": pct(delegate_hit, n_del),
        "n_not_for_me_predicted": pred_nfm,
        "n_not_for_me_true": true_nfm,
        "n_directed_missed_as_notforme": miss_nfm,
        "n_delegate_hit": delegate_hit,
        "errors": sum(1 for r in rows if not r.get("ok")),
    }


def subset_breakdown(rows: list[dict], prompt: str) -> dict[str, dict]:
    """Score the same rows split by open-book / generalization (#155).

    A single blended number hides the fact that the production prompt is an
    **open-book exam**: 10 of the test sentences appear verbatim in it. This
    split is what makes the memorization effect visible instead of assumed.
    """
    mapping = subset_by_id(prompt)
    out: dict[str, dict] = {}
    for subset in ("generalization", "open-book"):
        picked = [r for r in rows if mapping.get(r["id"]) == subset]
        if not picked:
            out[subset] = {"n": 0}
            continue
        out[subset] = {"n": len(picked), **summarize(picked)}
    return out


def print_subset_breakdown(name: str, breakdown: dict) -> None:
    """Print the per-subset scores (the anti-open-book view)."""
    print(f"  --- {name}: 子集分列（开卷 vs 泛化）---")
    for subset, stats in breakdown.items():
        if not stats.get("n"):
            print(f"    {subset}: (空)")
            continue
        print(
            f"    {subset:15s} n={stats['n']:3d}  "
            f"误响应={stats['baseline_mis_response_rate_pct']}%  "
            f"nfm_recall={stats['not_for_me_recall_pct']}%  "
            f"漏判={stats['directed_miss_rate_pct']}%"
        )


def print_summary(name: str, stats: dict) -> None:
    """Human-readable summary of one variant."""
    print(f"\n===== {name} summary =====")
    print(f"directed={stats['n_directed']}  nondirected={stats['n_nondirected']}  "
          f"delegate={stats['n_delegate']}  errors={stats['errors']}")
    print("confusion (expected x decision):")
    for exp in GROUPS:
        row = stats["matrix"][exp]
        print(
            f"  {exp:12s} "
            + "  ".join(f"{d}:{row[d]}" for d in ("response", "silence", "delegation", "not-for-me", "error"))
        )
    if name.startswith("A"):
        print(f"  误响应率 (nondirected->response): {stats['baseline_mis_response_rate_pct']}%")
    else:
        print(f"  not-for-me precision: {stats['not_for_me_precision_pct']}%  "
              f"(target >= 80%)")
        print(f"  not-for-me recall:    {stats['not_for_me_recall_pct']}%")
        print(f"  面向句漏判率 (directed->not-for-me): {stats['directed_miss_rate_pct']}%")
    # delegate previously had no ground-truth class at all, so this line could
    # not exist; it is the visible payoff of #155.
    print(f"  delegate recall (delegate->delegation): {stats['delegate_recall_pct']}%")


def main() -> None:
    """Run baseline A (3-state) + enhanced B (4-state) + reference variants."""
    variants = [
        ("A_live3_prod", build_live_prompt_3state(include_persona=True)),
        ("B_live4_prod_append", build_live_prompt_4state(include_persona=True, rich=False)),
        ("B2_live4_prod_append_rich", build_live_prompt_4state(include_persona=True, rich=True)),
        ("C_live4_reframe", build_reference_4state()),
    ]
    if not os.environ.get("BENCH_SKIP_CLEAN"):
        variants.append(("A_live3_clean", build_live_prompt_3state(include_persona=False)))

    print(f"[benchmark] model={LLAMA_MODEL} base={LLAMA_BASE_URL}")
    print(f"[benchmark] test set size={len(TEST_SET)} "
          + "  ".join(f"{g}={sum(1 for r in TEST_SET if r[2] == g)}" for g in GROUPS))

    results: dict[str, dict] = {}
    for name, prompt in variants:
        print(f"\n[benchmark] === variant {name} (system prompt len={len(prompt)}) ===")
        rows = run_variant(name, prompt)
        stats = summarize(rows)
        print_summary(name, stats)
        breakdown = subset_breakdown(rows, prompt)
        print_subset_breakdown(name, breakdown)
        results[name] = {"stats": stats, "subsets": breakdown, "rows": rows}

    out_dir = _REPO_ROOT / "doc" / "research" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "benchmark_4state_notforme_results.json"
    out_path.write_text(
        json.dumps(
            {
                "model": LLAMA_MODEL,
                "test_set_size": len(TEST_SET),
                "test_set_source": "services/webinfer/decision_eval_set.py::CASES",
                "denominator_note": HISTORICAL_DENOMINATOR_NOTE,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[benchmark] results written to {out_path}")


if __name__ == "__main__":
    main()
