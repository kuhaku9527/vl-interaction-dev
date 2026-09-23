# ruff: noqa: RUF001
# (RUF001 = ambiguous fullwidth punctuation. This script's prose is Chinese and
# quotes real test sentences; same convention as the sibling
# benchmark_4state_notforme.py.)
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
the shared test set (``from benchmark_4state_notforme import TEST_SET``, which
re-exports the frozen asset) — no test-set duplication. The set grew from 51 to
56 cases in #155 when the ``delegate`` ground-truth group was added.

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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ``_REPO_ROOT`` mirrors the existing benchmark: parents[2] of
# services/scripts/<this file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBINFER_DIR = _REPO_ROOT / "services" / "webinfer"
_SCRIPTS_DIR = _REPO_ROOT / "services" / "scripts"
for _dir in (_WEBINFER_DIR, _SCRIPTS_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

# --- reuse the existing benchmark's test set, parser, metrics --------------
import benchmark_4state_notforme as base_bench  # noqa: E402
from benchmark_4state_notforme import (  # noqa: E402
    TEST_SET,
    parse_decision_4state,
    print_subset_breakdown,
    print_summary,
    subset_breakdown,
    summarize,
)

# ★ #165: multi-round median + dispersion. The aggregator lives in
# services/webinfer (CI-visible) so it is unit-tested offline — this script
# only *runs* the rounds and feeds the per-round stats in. Single-round runs
# are the defect this closes: at temperature=0.8, 12-14 of the 26
# non-directed cases flip between two rounds, so a one-round number cannot
# tell "stable" from "lucky".
from decision_eval_rounds import (  # noqa: E402
    DEFAULT_ROUNDS,
    build_rounds_report,
    diff_reports,
    historical_comparable_view,
    open_book_report,
    print_report_text,
)

# ★ #155: the test set now has THREE ground-truth groups (directed /
# nondirected / delegate). The scorer — including the three-way ``correct``
# rule — lives in services/webinfer/decision_eval_score.py, which IS in the CI
# pytest matrix, so it is unit-tested (this file's own directory is not).
# We import it rather than keep a second copy.
from decision_eval_score import is_correct  # noqa: E402

# NOTE on comparability: this ``correct`` is NOT the same rule the historical
# results file used. The old rule was two-way and scored "silence" as WRONG
# for a non-directed utterance; the new one treats silence as a correct
# non-response. Rows therefore differ from the stored artifact's ``correct``
# field. See decision_eval_score.LEGACY_CORRECT_SEMANTICS.
from decision_eval_set import (  # noqa: E402
    GROUP_DELEGATE,
    GROUP_DIRECTED,
    GROUP_NONDIRECTED,
    GROUPS,
    HISTORICAL_DENOMINATOR_NOTE,
    group_counts,
)
from prompt_constants import LIVE_SYSTEM_PROMPT_EN  # noqa: E402
from system_prompts import compose_system_prompt, load_character_prompts  # noqa: E402

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

# ★ #165: how many rounds to run. Default 3 (> 2 as the ticket requires);
# 2 rounds can only say "agree/disagree" — 3 is the first round count with a
# real middle value. Override with BENCH_ROUNDS.
#
# ★ Fail loud instead of clamping: an earlier version used
# `max(2, int(...))`, so `BENCH_ROUNDS=1` silently ran 2 rounds. Someone
# asking for 1 round and being given 2 cannot tell — and if they later read
# the dispersion, they are reading a number produced by a configuration they
# never chose. A bad value must be an error, not a quiet correction.
_rounds_raw = os.environ.get("BENCH_ROUNDS", str(DEFAULT_ROUNDS))
try:
    ROUNDS = int(_rounds_raw)
except ValueError:
    raise SystemExit(f"BENCH_ROUNDS 必须是整数，收到 {_rounds_raw!r}") from None
if ROUNDS < 2:
    raise SystemExit(
        f"BENCH_ROUNDS={ROUNDS} 无效：单轮算不出离散度，"
        "而「多轮取中位」正是本 benchmark 的存在理由。请给 >= 2（建议 3）。"
    )
# Optional path to a previous multi-round results file: when set, the script
# prints the per-metric diff at the end (AC#3 "differences visible at a glance").
_DIFF_AGAINST = os.environ.get("BENCH_DIFF_AGAINST", "")

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
            f"[{name}] {index}/{total} {sid} {text!r} (expected={expected}, {note})",
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
                    "correct": is_correct(expected, decision),
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
    # ★ #155: iterate every ground-truth group (the old pair hid ``delegate``
    # rows from the provenance tally entirely).
    for expected in (GROUP_DIRECTED, GROUP_NONDIRECTED, GROUP_DELEGATE):
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


def print_rounds_block(name: str, report: dict, stability: dict) -> None:
    """Print the multi-round median + dispersion table for one variant (#165).

    ★ Delegates the table itself to ``decision_eval_rounds.print_report``
    rather than re-printing it here. The two were near-identical copies (same
    columns, same degenerate-denominator warning prose, same cost line), and a
    copy drifts: the canonical one is unit-tested, so this path now prints
    exactly what the tests assert instead of a lookalike.

    ``stability`` is accepted for call-site symmetry but is read from the
    report — ``print_report`` prints the same block from the same source.
    """
    print(f"  --- {name}: {report['rounds_completed']} 轮中位数 + 离散度 ---")
    # Indent the shared view so it nests under the variant heading. The banner
    # is suppressed because the heading above already says the same thing.
    for line in print_report_text(report, include_header=False).splitlines():
        print(f"  {line}")
    _ = stability  # stability is part of `report`; kept for call-site clarity


def _decision_only_rows(rows: list[dict]) -> list[dict]:
    """Strip a round down to the fields re-aggregation actually reads.

    Keeping every round's *full* rows made the committed artifact 484 KB,
    because each round re-stores the model's raw response text, token ids and
    latency. Re-aggregation needs only ``id`` / ``expected`` / ``decision``
    (plus ``ok``, so a failed row stays distinguishable from a decision), which
    is ~13 KB instead of ~70 KB for the same three rounds.

    ★ #157: **``first_token_id`` + ``n_tokens`` are part of that minimum**, not
    extra. Without them the *committed* artifact cannot tell "the model decided
    to stay silent" (``</silence>``, single special token 151669, stripped from
    content) from "the model emitted nothing at all" — the distinction #157
    exists to make. Storing the whole ``emitted_token_ids`` list would re-inflate
    the file for no benefit: the axis only ever reads the count and the first id.

    Kept as an explicit projection rather than storing nothing: the whole point
    of the multi-round evidence is that it can be **re-read** without a re-run.
    """
    projected = []
    for row in rows:
        tokens = row.get("emitted_token_ids")
        entry = {
            "id": row["id"],
            "expected": row["expected"],
            "decision": row.get("decision", ""),
            "ok": bool(row.get("ok", True)),
            # ★ None (not 0, not omitted-when-unknown) when the run produced no
            #   token list: the read side must be able to say "no evidence"
            #   rather than mis-report it as a zero-token empty output.
            "n_tokens": len(tokens) if isinstance(tokens, list) else None,
            "first_token_id": (
                tokens[0] if isinstance(tokens, list) and tokens else None
            ),
        }
        projected.append(entry)
    return projected


def variant_result_payload(
    *,
    rows: list[dict],
    per_round_stats: list[dict],
    per_round_rows: list[list[dict]],
    per_round_wall: list[float],
    prompt: str,
    rounds_requested: int,
) -> dict:
    """Assemble one variant's result block (pure; no I/O, no model calls).

    ★ Extracted so the *shape* of the result is testable. This exact code path
    crashed a full real-machine run during #165: the assembly was inline in
    ``main()``, a refactor renamed a key, and the mismatch only surfaced after
    all 336 inferences had already been spent — ``services/scripts`` is not in
    the CI pytest matrix, so nothing guarded it.

    Now the assembly is a pure function and
    ``services/webinfer/tests/test_benchmark_multiround_contract.py`` asserts
    the keys the printing/writing code reads actually exist, offline.
    """
    stats = per_round_stats[-1]
    breakdown = emission_breakdown(rows)
    subsets = subset_breakdown(rows, prompt)
    # ★ #165: the deliverable — median + single-round values + dispersion.
    report = build_rounds_report(
        per_round_stats,
        per_round_rows,
        prompt=prompt,
        round_case_ids=[[r["id"] for r in rws] for rws in per_round_rows],
        round_wall_seconds=per_round_wall,
        llm_calls=len(rows) * len(per_round_rows),
        rounds_requested=rounds_requested,
    )
    return {
        "stats": stats,
        "subsets": subsets,
        "emission_breakdown": breakdown,
        "category_breakdown": category_breakdown(rows),
        "rows": rows,
        # ★ #165: every round's decisions, so the artifact is
        # **self-sufficient**. Without this the file keeps only the last round
        # and ``decision_eval_rounds --from-results`` cannot re-aggregate it
        # (it would refuse with "fewer than 2 rounds" even though the file
        # visibly holds three). Re-analysis must not require a re-run: the
        # whole point of multi-round evidence is that it can be re-read.
        "per_round_rows": [_decision_only_rows(rws) for rws in per_round_rows],
        # ★ #165: the multi-round block. ``median`` sits next to ``per_round``
        # and ``dispersion``; the single-round-only fields above come from the
        # LAST round.
        "rounds_report": report,
        "historical_comparable_stats": summarize(historical_comparable_view(rows)),
    }


def main() -> None:
    """Run the production live prompt over the shared frozen test set.

    ★ #165: every variant is run ``ROUNDS`` times and the report carries the
    **median side by side with the single-round values and the dispersion**.
    A single round cannot distinguish "stable" from "lucky" — at
    temperature=0.8, 12-14 of the 26 non-directed cases were observed to flip
    between two rounds.
    """
    print("[prod-bench] production live prompt benchmark (LIVE_SYSTEM_PROMPT_EN)")
    # Counts come from the asset (single source), not an inline re-count.
    _counts = group_counts()
    print(
        f"[prod-bench] test set size={len(TEST_SET)} "
        + "  ".join(f"{g}={_counts[g]}" for g in GROUPS)
    )
    print(f"[prod-bench] rounds={ROUNDS} (multi-round median + dispersion, #165)")
    _ob = open_book_report(build_production_prompt(include_profile=True))
    print(
        f"[prod-bench] open-book exam: {_ob['is_open_book']}  "
        f"逐字重叠 {_ob['n_overlapping']} 句（非面向 {_ob['n_overlapping_nondirected']}）"
    )

    variants = [
        ("P_live4_prod_prompt", build_production_prompt(include_profile=False)),
        ("P2_live4_prod_prompt_profile", build_production_prompt(include_profile=True)),
    ]
    for vname, vprompt in variants:
        print(f"[prod-bench] variant {vname} system-prompt length={len(vprompt)}")

    results: dict[str, dict] = {}
    for vname, vprompt in variants:
        print(f"\n[prod-bench] === variant {vname} (system prompt len={len(vprompt)}) ===")
        per_round_stats: list[dict] = []
        per_round_rows: list[list[dict]] = []
        per_round_wall: list[float] = []
        for round_index in range(1, ROUNDS + 1):
            print(f"[prod-bench] --- round {round_index}/{ROUNDS} ---")
            round_started = time.perf_counter()
            rows = run_variant_with_tokens(f"{vname}#r{round_index}", vprompt)
            per_round_wall.append(round(time.perf_counter() - round_started, 2))
            per_round_stats.append(summarize(rows))
            per_round_rows.append(rows)
            print_summary(f"{vname} round {round_index}", per_round_stats[-1])

        # The last round's rows give the representative single-round views
        # (emission / category / subset breakdowns); the multi-round block
        # inside ``variant_result_payload`` is what carries the conclusion.
        payload = variant_result_payload(
            rows=per_round_rows[-1],
            per_round_stats=per_round_stats,
            per_round_rows=per_round_rows,
            per_round_wall=per_round_wall,
            prompt=vprompt,
            rounds_requested=ROUNDS,
        )
        print("  emission provenance (末轮):")
        for expected, tally in payload["emission_breakdown"].items():
            print(f"    {expected}: {tally}")
        print_subset_breakdown(vname, payload["subsets"])
        print_rounds_block(
            vname, payload["rounds_report"], payload["rounds_report"]["case_stability"]
        )
        comparable = payload["historical_comparable_stats"]
        print("  ★ 同口径历史可比视图（统一到 25/25，排除 delegate）：")
        print(
            f"    directed={comparable['n_directed']}  nondirected={comparable['n_nondirected']}  "
            f"mis_resp={comparable['baseline_mis_response_rate_pct']}%  "
            f"nfm_recall={comparable['not_for_me_recall_pct']}%  "
            f"directed_miss={comparable['directed_miss_rate_pct']}%"
        )
        results[vname] = payload

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
    print(
        "variant".ljust(30)
        + "mis_resp%".rjust(10)
        + "nfm_prec%".rjust(11)
        + "nfm_recall%".rjust(13)
        + "dir_miss%".rjust(11)
    )
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
    print(
        "  ⚠️ 上表的历史行取自 doc/research/data/benchmark_4state_notforme_results.json，"
        "其分母是 25/25 且无 delegate 组；"
        "本轮的**同口径**数字见各 variant 的 historical_comparable_stats。"
    )

    # ★ #165 AC#6: cost of N rounds, so N can be decided from data not taste.
    for ename, payload in results.items():
        cost = payload["rounds_report"]["cost"]
        print(
            f"[prod-bench] cost {ename}: rounds={cost['rounds']}  "
            f"total={cost['wall_seconds_total']}s  "
            f"mean/round={cost['wall_seconds_per_round_mean']}s  "
            f"calls={cost['llm_calls']}  s/call={cost['seconds_per_llm_call']}"
        )

    payload_out = {
        "model": base_bench.LLAMA_MODEL,
        "test_set_size": len(TEST_SET),
        "prompt_source": "services/webinfer/prompt_constants.py::LIVE_SYSTEM_PROMPT_EN",
        "prompt_route": "prompt_assembly._resolve_base_system_prompt (interaction_mode=live)",
        "prompt_length_bare": len(LIVE_SYSTEM_PROMPT_EN),
        "prompt_length_with_profile": len(build_production_prompt(True)),
        "judgment": "not-for-me precision >= 80%",
        # ★ #165: how many rounds produced these numbers. A reader who does
        # not know this cannot tell a stable score from a lucky one.
        "rounds_per_variant": ROUNDS,
        "rounds_semantics": (
            "每个 variant 跑 ROUNDS 轮。多轮结论在 "
            "results[<variant>].rounds_report 里："
            "rounds_report.metrics[<指标>] 给 {median, single_round_first, per_round, "
            "dispersion{stdev,range,mad,relative_stdev_pct}} —— 中位数与单轮值**并列**。"
            "逐句稳定性在 rounds_report.case_stability，"
            "分母在 rounds_report.denominator，开卷标注在 rounds_report.open_book，"
            "成本在 rounds_report.cost。"
            "仅有单轮值的字段（rows / emission_breakdown / subsets / category_breakdown）"
            "取自**末轮**；每轮原始行另存于 results[<variant>].per_round_rows，"
            "使本文件可被离线重新聚合（decision_eval_rounds --from-results）。"
        ),
        "decoding": {
            "max_tokens": base_bench.MAX_TOKENS,
            "temperature": base_bench.TEMPERATURE,
            "top_p": base_bench.TOP_P,
            "top_k": base_bench.TOP_K,
        },
        "denominator": {
            "note": HISTORICAL_DENOMINATOR_NOTE,
            "canonical": {group: _counts[group] for group in GROUPS},
            "historical_comparable": "见各 variant 的 historical_comparable_stats（25/25，无 delegate）",
        },
        "open_book": open_book_report(build_production_prompt(include_profile=True)),
        "results": results,
        "comparison_stats": comparison,
    }
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # ★ newline="\n" is load-bearing (AGENTS.md 字节核验): the default text mode
    # on Windows translates every "\n" to "\r\n", so the committed artifact
    # came out CRLF while every other tracked file is LF — and
    # `git diff --numstat` disagreed with `--ignore-cr-at-eol`, which AGENTS.md
    # defines as "the whole file's endings were rewritten". Make LF explicit.
    _OUT_PATH.write_text(
        json.dumps(payload_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n[prod-bench] results written to {_OUT_PATH}")

    # ★ #165 AC#3: the diff between two runs, one line per moved metric.
    if _DIFF_AGAINST:
        previous_path = Path(_DIFF_AGAINST)
        if not previous_path.exists():
            print(f"[prod-bench] ⚠️ BENCH_DIFF_AGAINST 指向的文件不存在：{previous_path}")
            return
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        print(f"\n===== diff vs {previous_path} =====")
        for ename, payload in results.items():
            old = (previous.get("results") or {}).get(ename)
            if not old or "rounds_report" not in old:
                print(f"[{ename}] 对照文件里没有多轮块（旧格式？）—— 无法 diff")
                continue
            print(f"[{ename}]")
            print(diff_reports(old["rounds_report"], payload["rounds_report"]))


if __name__ == "__main__":
    main()
