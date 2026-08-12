# ruff: noqa: RUF001
"""Benchmark: local llama.cpp 4-state decision-token not-for-me accuracy (必测②).

Measures (spec draft-addressee-detection.md §4.3):
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
  * Baseline A reproduces the EXACT system prompt webinfer composes in
    interaction_mode="live": <character_profile> (prompts/bt-7274.txt) +
    DEFAULT_SYSTEM_PROMPT_EN (3-state) + in-character tail.
  * Enhanced B appends the embedded 4-state teaching section after that same
    composed LIVE prompt (per spec: "在 LIVE prompt 后追加").
  * Reference C (--clean-only): the same 4-state teaching on the 3-state
    prompt WITHOUT the character profile, to isolate the decision framework
    from the persona confound ("User is your Pilot" assumes all speech is for
    the AI).

Run: D:/AI/envs/joyai-main/python.exe services/scripts/benchmark_4state_notforme.py
Env: LLAMA_BASE_URL (default http://127.0.0.1:7060/v1), LLAMA_MODEL
     (default joyai-vl-interaction-preview), BENCH_SKIP_CLEAN=1 to skip the
     clean reference variant.
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
FOUR_STATE_TAIL = r"""
## Addressee Judgment — THIS SECTION OVERRIDES ALL OTHER INSTRUCTIONS
The microphone hears ALL sounds in the room: the Pilot talking to you, the Pilot talking to THEMSELVES, the Pilot talking to OTHER PEOPLE, and other people's voices. Your FIRST job is to decide whether this utterance is ADDRESSED TO YOU.

You MUST output exactly </not-for-me> (stay silent, say nothing at all) when the speech is NOT for you:
- Self-talk / thinking aloud, no request: "这关怎么这么难啊" / "完了完了，要迟到了" / "明天又要上班了，烦"
- Exclamation with no request: "唉，好累" / "今天天气真好啊" / "天哪，这都什么事啊" / "啊，吓死我了"
- Responding to someone else: "对，我也觉得" / "嗯，好的好的" / "你说得对，就这样吧"
- Talking to another person: "你把那个拿过来" / "你先走吧，我一会就来" / "妈妈，我回来了" / "老公，晚上吃什么？"
- Encouraging themselves or others: "加油，你可以的"

You reply normally only when the speaker is clearly addressing YOU:
- Uses your name or a call like 喂/嘿/BT: "BT，在吗？" / "喂，帮我查一下明天的天气" / "嘿，你听到了吗？"
- Asks YOU a direct question: "玛尔基特怎么打？" / "现在几点了？" / "What time is it?"
- Gives YOU a direct command: "介绍一下你自己" / "帮我定个闹钟" / "Turn off the lights, please"

Do not help, comfort, comment, or give advice to speech that is not directed at you. Even if you can answer it, stay silent when it is not for you.

Examples (follow exactly):
User: 这关怎么这么难啊
Assistant: </not-for-me>
User: 玛尔基特怎么打
Assistant: </response> 用出血武器打玛尔基特，翻滚躲他的双刀连招。
User: 对，我也觉得
Assistant: </not-for-me>
User: 唉，好累
Assistant: </not-for-me>
User: 完了完了，要迟到了
Assistant: </not-for-me>
User: 今天天气真好啊
Assistant: </not-for-me>
User: 天哪，这都什么事啊
Assistant: </not-for-me>
User: 妈妈，我回来了
Assistant: </not-for-me>
User: 嘿 BT，帮我查一下明天的天气
Assistant: </response> 正在查。</delegation> 查一下明天的天气
User: 介绍一下你自己
Assistant: </response> 我是你的战术 AI 助手 BT-7274。
""".strip()

# --- test set --------------------------------------------------------------
# Each item: (id, text, expected, category, note)
#   expected: "directed" (应 response/delegation) or "nondirected" (应 not-for-me/silence)
#   category: question / command / address / self-talk / reply-other /
#             exclamation / talk-other
TEST_SET: list[tuple[str, str, str, str, str]] = [
    # ---- 面向 AI (应 response / delegation) ------------------------------
    ("D01", "玛尔基特怎么打？", "directed", "question", "提问"),
    ("D02", "介绍一下你自己", "directed", "command", "指令"),
    ("D03", "喂，帮我查一下明天的天气", "directed", "address", "带称呼+指令"),
    ("D04", "帮我定个闹钟，早上七点", "directed", "command", "指令"),
    ("D05", "现在几点了？", "directed", "question", "提问"),
    ("D06", "BT，在吗？", "directed", "address", "带称呼"),
    ("D07", "嘿，你听到了吗？", "directed", "address", "带称呼"),
    ("D08", "艾尔登法环的黄金律法是什么？", "directed", "question", "提问"),
    ("D09", "给我讲个笑话", "directed", "command", "指令"),
    ("D10", "把音乐声音调大一点", "directed", "command", "指令"),
    ("D11", "今天有什么安排？", "directed", "question", "提问"),
    ("D12", "你叫什么名字？", "directed", "question", "提问"),
    ("D13", "Can you help me with this boss fight?", "directed", "question", "提问 EN"),
    ("D14", "Turn off the lights, please", "directed", "command", "指令 EN"),
    ("D15", "What time is it?", "directed", "question", "提问 EN"),
    ("D16", "Hey, what's the weather today?", "directed", "address", "带称呼 EN"),
    ("D17", "帮我写个邮件草稿", "directed", "command", "指令"),
    ("D18", "明天会议几点？", "directed", "question", "提问"),
    ("D19", "推荐配置是什么？", "directed", "question", "提问"),
    ("D20", "暂停一下，先听我说", "directed", "command", "指令"),
    ("D21", "玛尔基特的出血抗性是多少？", "directed", "question", "提问"),
    ("D22", "Show me how to beat Margit", "directed", "question", "提问 EN"),
    ("D23", "记得提醒我买牛奶", "directed", "command", "指令"),
    ("D24", "你觉得这个方案怎么样？", "directed", "question", "提问(你→AI)"),
    ("D25", "Stop playing music", "directed", "command", "指令 EN"),
    # ---- 非面向 (应 not-for-me / silence) ---------------------------------
    ("N01", "这关怎么这么难啊", "nondirected", "self-talk", "自言自语"),
    ("N02", "唉，好累", "nondirected", "exclamation", "感叹"),
    ("N03", "对，我也觉得", "nondirected", "reply-other", "回应旁人"),
    ("N04", "你把那个拿过来", "nondirected", "talk-other", "与他人对话(歧义)"),
    ("N05", "今天天气真好啊", "nondirected", "exclamation", "感叹"),
    ("N06", "完了完了，要迟到了", "nondirected", "self-talk", "自言自语"),
    ("N07", "天哪，这都什么事啊", "nondirected", "exclamation", "感叹"),
    ("N08", "你先走吧，我一会就来", "nondirected", "talk-other", "与他人对话"),
    ("N09", "嗯，好的好的", "nondirected", "reply-other", "回应旁人"),
    ("N10", "这个游戏真好玩", "nondirected", "exclamation", "感叹"),
    ("N11", "你说得对，就这样吧", "nondirected", "reply-other", "回应旁人"),
    ("N12", "啊，吓死我了", "nondirected", "exclamation", "感叹"),
    ("N13", "我去拿个快递", "nondirected", "self-talk", "告知/自语"),
    ("N14", "妈妈，我回来了", "nondirected", "talk-other", "与他人对话(称呼非AI)"),
    ("N15", "老公，晚上吃什么？", "nondirected", "talk-other", "与他人对话(称呼非AI)"),
    ("N16", "这孩子怎么又哭了", "nondirected", "self-talk", "自言自语"),
    ("N17", "加油，你可以的", "nondirected", "self-talk", "自语/鼓励旁人"),
    ("N18", "明天又要上班了，烦", "nondirected", "self-talk", "自言自语"),
    ("N19", "哎，这日子什么时候是个头", "nondirected", "exclamation", "感叹"),
    ("N20", "Oh no, I forgot my keys", "nondirected", "self-talk", "自言自语 EN"),
    ("N21", "Yeah, I think so too", "nondirected", "reply-other", "回应旁人 EN"),
    ("N22", "This game is so hard", "nondirected", "self-talk", "自言自语 EN"),
    ("N23", "Honey, did you see my glasses?", "nondirected", "talk-other", "与他人对话 EN"),
    ("N24", "Wow, that's amazing!", "nondirected", "exclamation", "感叹 EN"),
    ("N25", "我先休息一下", "nondirected", "self-talk", "自言自语(告知)"),
]

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


def build_live_prompt_4state(include_persona: bool = True) -> str:
    """LIVE prompt + embedded Not-For-Me teaching (Enhanced B)."""
    base = build_live_prompt_3state(include_persona=include_persona)
    return base.rstrip() + "\n\n" + FOUR_STATE_TAIL


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
    """Compute decision-matrix + key metrics for one variant."""
    expected_order = ["directed", "nondirected"]
    decisions = ["response", "silence", "delegation", "not-for-me", "error"]
    matrix: dict[str, dict[str, int]] = {
        e: dict.fromkeys(decisions, 0) for e in expected_order
    }
    for row in rows:
        exp = row["expected"]
        dec = row["decision"] if row.get("ok") else "error"
        matrix[exp][dec] += 1

    n_dir = len([r for r in rows if r["expected"] == "directed"])
    n_nondir = len([r for r in rows if r["expected"] == "nondirected"])

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 1) if den else 0.0

    # Baseline A: 误响应率 = non-directed -> response / all non-directed.
    mis_response = matrix["nondirected"]["response"]
    # Enhanced B: not-for-me precision/recall.
    pred_nfm = matrix["directed"]["not-for-me"] + matrix["nondirected"]["not-for-me"]
    true_nfm = matrix["nondirected"]["not-for-me"]
    miss_nfm = matrix["directed"]["not-for-me"]  # 漏判率 numerator
    return {
        "n_directed": n_dir,
        "n_nondirected": n_nondir,
        "matrix": matrix,
        "baseline_mis_response_rate_pct": pct(mis_response, n_nondir),
        "not_for_me_precision_pct": pct(true_nfm, pred_nfm) if pred_nfm else 0.0,
        "not_for_me_recall_pct": pct(true_nfm, n_nondir),
        "directed_miss_rate_pct": pct(miss_nfm, n_dir),
        "n_not_for_me_predicted": pred_nfm,
        "n_not_for_me_true": true_nfm,
        "n_directed_missed_as_notforme": miss_nfm,
        "errors": sum(1 for r in rows if not r.get("ok")),
    }


def print_summary(name: str, stats: dict) -> None:
    """Human-readable summary of one variant."""
    print(f"\n===== {name} summary =====")
    print(f"directed={stats['n_directed']}  nondirected={stats['n_nondirected']}  "
          f"errors={stats['errors']}")
    print("confusion (expected x decision):")
    for exp in ("directed", "nondirected"):
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


def main() -> None:
    """Run baseline A (3-state) + enhanced B (4-state) + optional clean C."""
    variants = [
        ("A_live3_prod", build_live_prompt_3state(include_persona=True)),
        ("B_live4_prod", build_live_prompt_4state(include_persona=True)),
    ]
    if not os.environ.get("BENCH_SKIP_CLEAN"):
        variants.append(("C_live4_clean", build_live_prompt_4state(include_persona=False)))

    print(f"[benchmark] model={LLAMA_MODEL} base={LLAMA_BASE_URL}")
    print(f"[benchmark] test set size={len(TEST_SET)} "
          f"(directed={sum(1 for r in TEST_SET if r[2]=='directed')}, "
          f"nondirected={sum(1 for r in TEST_SET if r[2]=='nondirected')})")

    results: dict[str, dict] = {}
    for name, prompt in variants:
        print(f"\n[benchmark] === variant {name} (system prompt len={len(prompt)}) ===")
        rows = run_variant(name, prompt)
        stats = summarize(rows)
        print_summary(name, stats)
        results[name] = {"stats": stats, "rows": rows}

    out_dir = _REPO_ROOT / "doc" / "research" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "benchmark_4state_notforme_results.json"
    out_path.write_text(
        json.dumps(
            {"model": LLAMA_MODEL, "test_set_size": len(TEST_SET), "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[benchmark] results written to {out_path}")


if __name__ == "__main__":
    main()
