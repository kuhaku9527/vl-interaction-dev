# ruff: noqa: RUF001
"""Benchmark: the PRODUCTION live system prompt (``LIVE_SYSTEM_PROMPT_EN``).

Why this script exists
----------------------
``benchmark_4state_notforme.py`` measured five *script-embedded variants*
(A_live3_prod / A_live3_clean / B_live4_prod_append / B2_live4_prod_append_rich /
C_live4_reframe).  None of them is the prompt the deployed stack actually sends.
The production route is:

    ``prompt_assembly._resolve_base_system_prompt``
        interaction_mode == "live"  ->  ``LIVE_SYSTEM_PROMPT_EN`` (four-state)

so the number that matters for the shipped product was never measured. This
script closes that gap: it imports the *real* ``LIVE_SYSTEM_PROMPT_EN`` from
``services/webinfer/prompt_constants.py`` (no copy, no edit) and runs it over
the *same* 51-case test set, imported from ``benchmark_4state_notforme``
(``from benchmark_4state_notforme import TEST_SET``) — no test-set duplication.

Variants
--------
  * ``P_live4_prod_prompt``           — bare ``LIVE_SYSTEM_PROMPT_EN``.
  * ``P2_live4_prod_prompt_profile``  — ``LIVE_SYSTEM_PROMPT_EN`` wrapped in the
    production character-profile composition (``<character_profile>`` block +
    in-character tail via ``compose_system_prompt``), i.e. the byte-level
    prompt the live adapter assembles.

Request shape, decoding parameters (MAX_TOKENS=1024, TEMPERATURE=0.8,
TOP_P=0.9, TOP_K=40) and the four-state decision parser are re-used verbatim
from the existing benchmark so the numbers are directly comparable.

Token-level instrumentation (this script only)
----------------------------------------------
``</silence>`` and ``</response>`` are SINGLE special tokens in this model
(ids 151669 / 151670) and llama-server strips them from ``message.content``.
``</not-for-me>`` is NOT a vocabulary token — it is five literal tokens
(``</`` + ``not`` + ``-`` + ``for`` + ``-me>``) and therefore SURVIVES in
content.  Consequence: an empty body means the model emitted ``</silence>``,
which production treats as a legitimate decision distinct from ``</not-for-me>``
(``</silence>`` = "addressed to me, nothing worth saying").  We therefore
request ``logprobs`` so the actual emitted token id is recorded for every row,
making the silence-vs-not-for-me distinction auditable instead of guessed.
``logprobs`` does not alter sampling.

Output: ``doc/research/data/benchmark_production_live_prompt_results.json``
re-using the original ``stats`` field names (baseline_mis_response_rate_pct /
not_for_me_precision_pct / not_for_me_recall_pct / directed_miss_rate_pct /
matrix), plus the historical variants loaded from the earlier results file for
a one-glance comparison.

Judgment (spec addressee-detection.md §4.3): not-for-me precision >= 80%.

Run: D:/AI/envs/joyai-main/python.exe services/scripts/benchmark_production_live_prompt.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import sys

# ``_REPO_ROOT`` mirrors the existing benchmark: parents[2] of
# services/scripts/<this file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBINFER_DIR = _REPO_ROOT / "services" / "webinfer"
_SCRIPTS_DIR = _REPO_ROOT / "services" / "scripts"
for _dir in (_WEBINFER_DIR, _SCRIPTS_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

# --- production prompt + production assembly helper (read-only import) ------
from prompt_constants import LIVE_SYSTEM_PROMPT_EN  # noqa: E402
from system_prompts import compose_system_prompt, load_character_prompts  # noqa: E402

# --- reuse the existing benchmark's test set, parser, metrics --------------
import benchmark_4state_notforme as base_bench  # noqa: E402
from benchmark_4state_notforme import (  # noqa: E402
    TEST_SET,
    call_llm,
    parse_decision_4state,
    print_summary,
    summarize,
)

# Language used by the production live route for the in-character tail.
PROD_LANGUAGE = "en"

# Single special tokens in this model (from /tokenize); stripped from content.
TOKEN_ID_SILENCE = 151669
TOKEN_ID_RESPONSE = 151670

_HISTORICAL_PATH = (
    _REPO_ROOT / "doc" / "research" / "data" / "benchmark_4state_notforme_results.json"
)
_OUT_PATH = (
    _REPO_ROOT / "doc" / "research" / "data" / "benchmark_production_live_prompt_results.json"
)
# Optional override so a repeat run (temperature=0.8 is stochastic) can be
# written beside the primary result and diffed against it.
_OUT_PATH = Path(os.environ.get("BENCH_PROD_OUT", str(_OUT_PATH)))

_HISTORICAL_VARIANTS = (
    "A_live3_prod",
    "A_live3_clean",
    "B_live4_prod_append",
    "B2_live4_prod_append_rich",
    "C_live4_reframe",
)


def build_production_prompt(include_profile: bool) -> str:
    """Return the production live system prompt, with or without persona.

    ``include_profile=True`` reproduces what the live adapter actually sends:
    ``compose_system_prompt(LIVE_SYSTEM_PROMPT_EN, <prompts/bt-7274.txt>, "en")``.
    """
    if not include_profile:
        return LIVE_SYSTEM_PROMPT_EN
    profiles = load_character_prompts()
    return compose_system_prompt(LIVE_SYSTEM_PROMPT_EN, profiles, PROD_LANGUAGE)


def call_llm_with_token_ids(system_prompt: str, user_text: str) -> dict:
    """Identical request to ``benchmark_4state_notforme.call_llm`` + logprobs.

    Same URL, body shape, decoding parameters and timeout as the shared
    ``call_llm``; the only addition is ``logprobs``/``top_logprobs`` so the
    emitted token ids are recoverable (they carry no sampling effect).
    Returns ``{"raw", "latency_s", "token_ids"}``.
    """
    body = {
        "model": base_bench.LLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": base_bench.MAX_TOKENS,
        "temperature": base_bench.TEMPERATURE,
        "top_p": base_bench.TOP_P,
        "top_k": base_bench.TOP_K,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "skip_special_tokens": False,
        "greedy": False,
        "stream": False,
        "logprobs": True,
        "top_logprobs": 3,
    }
    req = urllib.request.Request(  # noqa: S310 - local llama-server benchmark
        base_bench.LLAMA_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=base_bench.REQUEST_TIMEOUT_S) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    elapsed = time.perf_counter() - t0
    content = ""
    token_ids: list[int] = []
    if data.get("choices"):
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content") or ""
        for entry in (choice.get("logprobs") or {}).get("content") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                token_ids.append(entry["id"])
    return {"raw": content, "latency_s": round(elapsed, 2), "token_ids": token_ids}


def classify_emission(decision: str, token_ids: list[int]) -> str:
    """Explain HOW a decision was emitted (token-level provenance)."""
    if not token_ids:
        return "empty_no_tokens"
    first = token_ids[0]
    if decision == "not-for-me":
        return "literal_not_for_me_tokens"
    if first == TOKEN_ID_SILENCE:
        return "silence_special_token_151669"
    if first == TOKEN_ID_RESPONSE:
        return "response_special_token_151670"
    if decision == "delegation":
        return "literal_delegation_tokens"
    return "literal_text_tokens"


def run_variant_with_tokens(name: str, system_prompt: str) -> list[dict]:
    """Like ``run_variant`` but also records emitted token ids + provenance."""
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
            result = call_llm_with_token_ids(system_prompt, text)
            raw = result["raw"]
            decision, clean = parse_decision_4state(raw)
            token_ids = result["token_ids"]
            row.update(
                {
                    "ok": True,
                    "decision": decision,
                    "raw": raw,
                    "clean": clean[:200],
                    "latency_s": result["latency_s"],
                    "emitted_token_ids": token_ids,
                    "emission": classify_emission(decision, token_ids),
                    "correct": (decision == "not-for-me") if expected == "nondirected"
                    else (decision != "not-for-me"),
                }
            )
            print(
                f"      -> {decision} [{row['emission']}] | raw={raw[:80]!r} "
                f"| {result['latency_s']}s",
                flush=True,
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"      -> FAIL {row['error']}", flush=True)
        rows.append(row)
        time.sleep(0.2)
    return rows


def emission_breakdown(rows: list[dict]) -> dict:
    """Per-expected-class tally of decision x emission provenance."""
    out: dict[str, dict[str, int]] = {}
    for expected in ("directed", "nondirected"):
        tally: dict[str, int] = {}
        for row in rows:
            if row["expected"] != expected or not row.get("ok"):
                continue
            key = f"{row['decision']}|{row['emission']}"
            tally[key] = tally.get(key, 0) + 1
        out[expected] = dict(sorted(tally.items()))
    return out


def category_breakdown(rows: list[dict]) -> dict:
    """Accuracy per test-set category (for the failure analysis)."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        cat = row["category"]
        slot = out.setdefault(cat, {"n": 0, "correct": 0})
        slot["n"] += 1
        if row.get("correct"):
            slot["correct"] += 1
    return dict(sorted(out.items()))


def main() -> None:
    """Run the production live prompt over the shared 51-case test set."""
    print("[prod-bench] production live prompt benchmark (LIVE_SYSTEM_PROMPT_EN)")
    print(f"[prod-bench] test set size={len(TEST_SET)} "
          f"(directed={sum(1 for r in TEST_SET if r[2] == 'directed')}, "
          f"nondirected={sum(1 for r in TEST_SET if r[2] == 'nondirected')})")

    variants = [
        ("P_live4_prod_prompt", build_production_prompt(include_profile=False)),
        ("P2_live4_prod_prompt_profile", build_production_prompt(include_profile=True)),
    ]
    for vname, vprompt in variants:
        print(f"[prod-bench] variant {vname} system-prompt length={len(vprompt)}")

    results: dict[str, dict] = {}
    for vname, vprompt in variants:
        print(f"\n[prod-bench] === variant {vname} (system prompt len={len(vprompt)}) ===")
        rows = run_variant_with_tokens(vname, vprompt)
        stats = summarize(rows)
        print_summary(vname, stats)
        breakdown = emission_breakdown(rows)
        print("  emission provenance:")
        for expected, tally in breakdown.items():
            print(f"    {expected}: {tally}")
        results[vname] = {
            "stats": stats,
            "emission_breakdown": breakdown,
            "category_breakdown": category_breakdown(rows),
            "rows": rows,
        }

    # --- comparison block: historical variants (recorded, not re-measured) ---
    comparison: dict[str, dict] = {}
    if _HISTORICAL_PATH.exists():
        historical = json.loads(_HISTORICAL_PATH.read_text(encoding="utf-8"))
        for key in _HISTORICAL_VARIANTS:
            entry = historical.get("results", {}).get(key)
            if entry:
                comparison[key] = entry["stats"]
    for ename, payload in results.items():
        comparison[ename] = payload["stats"]

    print("\n===== production live prompt vs historical variants =====")
    print("variant".ljust(30) + "mis_resp%".rjust(10) + "nfm_prec%".rjust(11)
          + "nfm_recall%".rjust(13) + "dir_miss%".rjust(11))
    for key in (*_HISTORICAL_VARIANTS, *[n for n, _ in variants]):
        stats = comparison.get(key)
        if not stats:
            continue
        print(
            key.ljust(30)
            + f"{stats['baseline_mis_response_rate_pct']}".rjust(10)
            + f"{stats['not_for_me_precision_pct']}".rjust(11)
            + f"{stats['not_for_me_recall_pct']}".rjust(13)
            + f"{stats['directed_miss_rate_pct']}".rjust(11)
        )

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(
        json.dumps(
            {
                "model": base_bench.LLAMA_MODEL,
                "test_set_size": len(TEST_SET),
                "prompt_source": "services/webinfer/prompt_constants.py::LIVE_SYSTEM_PROMPT_EN",
                "prompt_route": "prompt_assembly._resolve_base_system_prompt (interaction_mode=live)",
                "prompt_length_bare": len(LIVE_SYSTEM_PROMPT_EN),
                "prompt_length_with_profile": len(build_production_prompt(True)),
                "judgment": "not-for-me precision >= 80%",
                "decoding": {
                    "max_tokens": base_bench.MAX_TOKENS,
                    "temperature": base_bench.TEMPERATURE,
                    "top_p": base_bench.TOP_P,
                    "top_k": base_bench.TOP_K,
                },
                "results": results,
                "comparison_stats": comparison,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[prod-bench] results written to {_OUT_PATH}")


if __name__ == "__main__":
    main()
