# ruff: noqa: RUF002
"""决策事件**读侧**的契约测试（工单 #156，spec #154）.

Spec: #154 §一「决策事件流必须有人读」+ §二「决策词的歧义必须在读侧消解」。

上游写入侧已落地（#146：`live_llm.finish_llm_turn` 与
`live_proactive.send_proactive_prompt` 写 `live_decision`），但**全仓没有读取方**。
本测试文件钉住读侧的四条契约：

  1. **唯一的聚合入口**（模块级，无第二个脚本各读各的）；
  2. 按**会话 × 轮次**输出 决策 / 时间 / 延迟 / 帧数；
  3. ★ **「判定沉默」与「空输出」可分辨** —— 判据是**输出长度**，不是 content；
  4. **用户轮与主动轮可区分**。

以及三条负控（防止判据退化为恒真）：

  * ★ 去掉延迟赋值 ⇒ 聚合结果的延迟字段**缺失**（读取方真的在用它）；
  * 断言 `0` 不被当成有效延迟；
  * 断言坏行 / 非决策行**被计数**而不是被静默吞掉。

外加一条「判据有效性」自检：**把夹具里的一次真实沉默判定改成零长度输出，
分级必须随之改变** —— 若读数对输入不敏感，本文件的其余断言都是空的。

本文件只依赖**冻结的事件夹具**（`tests/fixtures/live_decision_events.jsonl`），
**不需要真机在跑**（#156 验收项）。

Run: cd services/webinfer && python -m pytest tests/test_decision_events.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import decision_events as de
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "live_decision_events.jsonl"


def _load_fixture() -> de.ParseResult:
    """Load the frozen fixture through the public reader entry point."""
    return de.load_events(FIXTURE)


def _aggregate_fixture() -> dict:
    loaded = _load_fixture()
    return de.aggregate(
        loaded.rounds,
        skipped_other_events=loaded.skipped_other_events,
        malformed=loaded.malformed,
    )


def _session(agg: dict, session_id: str) -> dict:
    return next(s for s in agg["sessions"] if s["session_id"] == session_id)


# ---------------------------------------------------------------------------
# 1. the reader exists and reads the frozen fixture (no live machine needed)
# ---------------------------------------------------------------------------


def test_reader_loads_frozen_fixture_without_a_running_machine():
    """#156 AC: 读取方对冻结事件夹具可测，不依赖真机在跑."""
    assert FIXTURE.is_file(), "the frozen event fixture must ship in the repo"
    loaded = _load_fixture()
    assert len(loaded.rounds) == 9, f"expected 9 decision rounds, got {len(loaded.rounds)}"


def test_non_decision_events_are_counted_not_swallowed():
    """A `config.services.patch` line lives in the same file — it must be tallied.

    Silently skipping it would make "the file has more lines than rounds" look
    like nothing happened.
    """
    loaded = _load_fixture()
    assert loaded.skipped_other_events == 1


def test_malformed_lines_are_reported_not_swallowed():
    """★ Silent drops make "events are missing" indistinguishable from "none existed"."""
    loaded = _load_fixture()
    assert len(loaded.malformed) == 2, loaded.malformed
    reasons = " ".join(why for _where, why in loaded.malformed)
    assert "invalid JSON" in reasons, "the non-JSON line must be reported as such"
    assert "extra.decision" in reasons, "a live_decision without a decision must be reported"


# ---------------------------------------------------------------------------
# 2. session x round: decision / time / latency / frames
# ---------------------------------------------------------------------------


def test_aggregate_groups_by_session_and_round():
    agg = _aggregate_fixture()
    assert agg["n_sessions"] == 3, "unattributed rounds form their own group"
    assert agg["totals"]["n_rounds"] == 9

    alpha = _session(agg, "sess-alpha")
    assert alpha["n_rounds"] == 6
    assert [r["seq"] for r in alpha["rounds"]] == [1, 2, 3, 4, 5, 6], (
        "seq must be 1-based per session"
    )
    assert [r["decision"] for r in alpha["rounds"]] == [
        "response",
        "delegation",
        "silence",
        "not-for-me",
        "response",
        "silence",
    ]
    # time is carried verbatim (ISO-8601 UTC, sortable)
    assert alpha["rounds"][0]["ts"] == "2026-09-22T10:00:00.100Z"
    assert alpha["first_ts"] < alpha["last_ts"]


def test_aggregate_reports_latency_and_frames_per_round():
    agg = _aggregate_fixture()
    alpha = _session(agg, "sess-alpha")
    assert [r["latency_ms"] for r in alpha["rounds"]] == [742, 388, 305, 901, 654, 402]
    assert [r["frames_n"] for r in alpha["rounds"]] == [2, 1, 3, 1, 1, 1]
    assert alpha["latency_ms"]["median"] == pytest.approx(528.0)
    assert alpha["frames_n"]["total_frames"] == 9


def test_round_ids_are_stable_and_session_qualified():
    agg = _aggregate_fixture()
    ids = [r["id"] for r in _session(agg, "sess-alpha")["rounds"]]
    assert ids[0] == "sess-alpha#1"
    assert ids[-1] == "sess-alpha#6"


def test_unattributed_rounds_are_visible_not_merged_away():
    """A round without session_id must be its own group, never silently dropped."""
    agg = _aggregate_fixture()
    assert agg["totals"]["n_unattributed_session"] == 1
    orphan = _session(agg, de.UNATTRIBUTED)
    assert orphan["n_rounds"] == 1
    assert any("没有 session_id" in note for note in agg["caveats"]), agg["caveats"]


# ---------------------------------------------------------------------------
# 3. ★ "judged quiet" vs "emitted nothing" — the length criterion, not content
# ---------------------------------------------------------------------------


def test_empty_output_is_distinguishable_from_judged_silence():
    """★ #156 AC: 「判定沉默」与「空输出」在聚合结果里可分辨（用输出长度，不看 content）.

    The fixture contains both, and they are NOT the same shape:
      * alpha#3  silence + raw_text_len 0  -> bare quiet (ambiguous, see below)
      * alpha#5  response + raw_text_len 0 -> EMPTY OUTPUT (the failure mode)
      * alpha#4  not-for-me + raw_text_len 41 -> a real judgement that left a trace
    """
    agg = _aggregate_fixture()
    alpha = _session(agg, "sess-alpha")
    by_seq = {r["seq"]: r for r in alpha["rounds"]}

    assert by_seq[5]["decision"] == "response"
    assert by_seq[5]["output_state"] == de.OUTPUT_EMPTY, (
        "a response decision with a zero-length output is a FAILED output, not a silence judgement"
    )
    assert by_seq[3]["output_state"] == de.OUTPUT_QUIET_BARE
    assert by_seq[4]["output_state"] == de.OUTPUT_QUIET_TRACED, (
        "not-for-me carried 41 chars of raw output — that is a traced judgement, "
        "not an empty output"
    )
    assert by_seq[1]["output_state"] == de.OUTPUT_SPOKE

    assert agg["empty_output_rounds"] == ["sess-alpha#5"]


def test_empty_output_is_not_inferred_from_the_body_alone():
    """★ The criterion is the OUTPUT length, never "is content empty".

    `delegation` rounds have an empty BODY by construction (the delegated
    question is not spoken), yet they are emphatically not failed outputs.
    A reader keying on the body would misclassify every delegation.
    """
    agg = _aggregate_fixture()
    alpha = _session(agg, "sess-alpha")
    delegation = next(r for r in alpha["rounds"] if r["decision"] == "delegation")
    assert delegation["response_chars"] == 0
    assert delegation["raw_text_len"] == 39
    assert delegation["output_state"] == de.OUTPUT_SPOKE
    assert delegation["output_state"] != de.OUTPUT_EMPTY


def test_bare_quiet_is_labelled_as_unattributable():
    """★ Honest residual ambiguity: a bare </silence> and a truly empty output
    are the same shape at the content layer. The reader must SAY SO rather than
    pretend it resolved it (#157 owns the token-level criterion)."""
    agg = _aggregate_fixture()
    assert "sess-alpha#3" in agg["ambiguous_quiet_rounds"]
    assert any("不可归因" in note for note in agg["caveats"]), agg["caveats"]


@pytest.mark.parametrize(
    ("decision", "raw_len", "expected"),
    [
        ("response", 10, de.OUTPUT_SPOKE),
        ("delegation", 39, de.OUTPUT_SPOKE),
        ("response", 0, de.OUTPUT_EMPTY),
        ("delegation", 0, de.OUTPUT_EMPTY),
        ("silence", 0, de.OUTPUT_QUIET_BARE),
        ("not-for-me", 0, de.OUTPUT_QUIET_BARE),
        ("not-for-me", 41, de.OUTPUT_QUIET_TRACED),
        ("silence", 5, de.OUTPUT_QUIET_TRACED),
        # ★ A MISSING length is "unknown", not "the model said nothing".
        # Reporting unknown as a failed output is the same false-alarm class as
        # reporting a spoken round as one; the miss is counted separately
        # (``n_missing_raw_text_len``) so it is not quietly dropped either.
        ("response", None, de.OUTPUT_QUIET_BARE),
        ("delegation", None, de.OUTPUT_QUIET_BARE),
    ],
)
def test_output_state_matrix(decision, raw_len, expected):
    """The whole classification table, pinned."""
    assert de.output_state(decision, raw_len) == expected


def test_missing_raw_text_len_is_counted_and_not_called_a_failed_output():
    """Control for the row above: the miss must still be *visible*."""
    agg = de.aggregate(
        [
            de.Round(
                session_id="s",
                round_kind="user",
                seq=0,
                ts="2026-09-22T10:00:00.000Z",
                decision="response",
                latency_ms=10,
                frames_n=None,
                raw_text_len=None,  # field missing (legacy record)
                response_chars=None,
                user_text_len=1,
                delegation_question_len=0,
                interaction_mode="live",
                source="<test>",
            )
        ]
    )
    assert agg["totals"]["n_missing_raw_text_len"] == 1
    assert agg["empty_output_rounds"] == [], "unknown length filed as a failed output"
    assert any("缺 raw_text_len" in note for note in agg["caveats"]), agg["caveats"]


# ---------------------------------------------------------------------------
# 4. user vs proactive rounds are distinguishable
# ---------------------------------------------------------------------------


def test_proactive_and_user_rounds_are_distinguishable():
    """★ #156 AC: proactive 轮与用户轮在聚合结果里可区分."""
    agg = _aggregate_fixture()
    kinds = agg["totals"]["by_round_kind"]
    assert kinds["user"]["n_rounds"] == 7
    assert kinds["proactive"]["n_rounds"] == 2
    assert kinds["user"]["by_decision"]["response"] == 2
    assert kinds["proactive"]["by_output_state"][de.OUTPUT_EMPTY] == 1, (
        "the proactive empty output must be visible in the proactive bucket"
    )

    alpha = _session(agg, "sess-alpha")
    assert alpha["by_round_kind"]["proactive"]["n_rounds"] == 2
    proactive = [r for r in alpha["rounds"] if r["round_kind"] == "proactive"]
    assert [r["seq"] for r in proactive] == [5, 6]


def test_unknown_round_kind_is_surfaced_not_folded_into_user():
    """A future third round kind must not be silently counted as a user round."""
    rounds = de.order_rounds(
        [
            de.Round(
                session_id="s",
                round_kind="something-new",
                seq=0,
                ts="2026-09-22T10:00:00.000Z",
                decision="silence",
                latency_ms=1,
                frames_n=None,
                raw_text_len=0,
                response_chars=0,
                user_text_len=0,
                delegation_question_len=0,
                interaction_mode="live",
                source="<test>",
            )
        ]
    )
    agg = de.aggregate(rounds)
    assert agg["totals"]["by_round_kind"]["user"]["n_rounds"] == 0
    assert agg["totals"]["by_round_kind"]["other"]["n_rounds"] == 1
    assert any("round_kind" in note for note in agg["caveats"])


# ---------------------------------------------------------------------------
# 5. ★ negative control: remove the latency assignment -> the reader notices
# ---------------------------------------------------------------------------


def test_negative_control_removing_latency_breaks_the_latency_fields():
    """★ #156 负控：去掉延迟赋值 → 聚合结果的延迟字段缺失或异常.

    This is exactly the pre-#156 state of `finish_llm_turn` / `send_proactive_prompt`
    (`latency_ms=None`). The reader must go RED in a way a caller can act on —
    not fall back to a plausible-looking number.
    """
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    mutated = []
    for line in lines:
        if '"live_decision"' not in line:
            mutated.append(line)
            continue
        event = json.loads(line)
        event["latency_ms"] = None  # ← the mutation: the writer stopped stamping
        mutated.append(json.dumps(event, ensure_ascii=False))

    loaded = de.parse_lines(mutated, source="mutated")
    agg = de.aggregate(loaded.rounds, malformed=loaded.malformed)
    latency = agg["totals"]["latency_ms"]

    assert latency["n_present"] == 0
    assert latency["n_missing"] == 9
    assert latency["median"] is None, "a missing latency must NOT become a number"
    assert latency["p90"] is None
    assert latency["min"] is None and latency["max"] is None
    assert latency["missing_rounds"] == [r["id"] for s in agg["sessions"] for r in s["rounds"]]
    assert any("没有 latency_ms" in note for note in agg["caveats"]), agg["caveats"]


def test_a_zero_latency_is_flagged_as_suspicious():
    """0 ms is not a plausible round trip — the reader must call it out.

    A writer that defaults to 0 would otherwise look perfectly healthy.
    """
    agg = de.aggregate(
        [
            de.Round(
                session_id="s",
                round_kind="user",
                seq=0,
                ts="2026-09-22T10:00:00.000Z",
                decision="response",
                latency_ms=0,
                frames_n=None,
                raw_text_len=5,
                response_chars=5,
                user_text_len=1,
                delegation_question_len=0,
                interaction_mode="live",
                source="<test>",
            )
        ]
    )
    assert agg["totals"]["latency_ms"]["n_zero"] == 1
    assert any("0 不是有效耗时" in note for note in agg["caveats"]), agg["caveats"]


# ---------------------------------------------------------------------------
# 5b. ★ self-contradictory records: raw_text_len < response_chars
# ---------------------------------------------------------------------------


def test_the_reader_flags_self_contradictory_records():
    """★ ``raw_text_len < response_chars`` is structurally impossible, so such a
    record is corrupt — and must NOT be reported as a failed output.

    Real evidence: 24 such rows existed in ``logs/events/webui-2026-09-21.jsonl``
    because the writer fingerprinted the cleaned body until #156 fixed it. Those
    rounds had *spoken*; filing them under ``empty_output`` is a worse signal
    than a missing one. The reader must say "this record contradicts itself".
    """
    loaded = de.parse_lines(
        [
            json.dumps(
                {
                    "ts": "2026-09-22T10:00:00.000Z",
                    "service": "webui",
                    "event": "live_decision",
                    "session_id": "s",
                    "latency_ms": 100,
                    "extra": {
                        "decision": "response",
                        "raw_text_len": 1,  # ← shorter than its own body
                        "response_chars": 11,
                    },
                }
            )
        ]
    )
    agg = de.aggregate(loaded.rounds)
    round_ = agg["sessions"][0]["rounds"][0]

    assert round_["output_state"] != de.OUTPUT_EMPTY, (
        "a self-contradictory record was filed as a FAILED OUTPUT — a spoken "
        "round would be counted as the model saying nothing"
    )
    assert round_["output_state"] == de.OUTPUT_QUIET_BARE
    assert agg["totals"]["inconsistent_length_rounds"] == ["s#1"]
    assert round_["id"] not in agg["empty_output_rounds"]
    assert any("自相矛盾" in note for note in agg["caveats"]), agg["caveats"]


def test_reader_accepts_a_consistent_record_without_crying_wolf():
    """Control for the check above: a consistent record must NOT be flagged."""
    agg = de.aggregate(
        [
            de.Round(
                session_id="s",
                round_kind="user",
                seq=0,
                ts="2026-09-22T10:00:00.000Z",
                decision="response",
                latency_ms=100,
                frames_n=None,
                raw_text_len=20,
                response_chars=11,
                user_text_len=2,
                delegation_question_len=0,
                interaction_mode="live",
                source="<test>",
            )
        ]
    )
    assert agg["totals"]["inconsistent_length_rounds"] == []
    assert not any("自相矛盾" in note for note in agg["caveats"])


def test_cli_require_latency_goes_red_on_zero_latency(tmp_path, capsys):
    """★ The gate must act on the reader's OWN stated judgement.

    The report already prints "0 不是有效耗时，按缺失看待" and counts ``n_zero``.
    Exiting 0 while printing that would be a gate contradicting its own
    criterion — and on real data 102/180 rows are 0, so it is not theoretical.
    """
    target = tmp_path / "webui-2026-09-22.jsonl"
    target.write_text(
        json.dumps(
            {
                "ts": "2026-09-22T10:00:00.000Z",
                "service": "webui",
                "event": "live_decision",
                "session_id": "s",
                "latency_ms": 0,
                "extra": {"decision": "response", "raw_text_len": 5, "response_chars": 5},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert de.main(["--events-dir", str(target), "--require-latency"]) == 1
    assert "0 不是有效耗时" in capsys.readouterr().err


def test_cli_require_latency_goes_red_on_contradictory_records(tmp_path, capsys):
    """The same gate must also refuse a corrupt record."""
    target = tmp_path / "webui-2026-09-22.jsonl"
    target.write_text(
        json.dumps(
            {
                "ts": "2026-09-22T10:00:00.000Z",
                "service": "webui",
                "event": "live_decision",
                "session_id": "s",
                "latency_ms": 250,
                "extra": {"decision": "response", "raw_text_len": 0, "response_chars": 7},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert de.main(["--events-dir", str(target), "--require-latency"]) == 1
    assert "raw_text_len < response_chars" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 6. the reading is sensitive to its input (the criterion is not vacuous)
# ---------------------------------------------------------------------------


def test_mutating_a_real_silence_into_a_zero_length_output_changes_the_verdict():
    """★ Criterion validity check: if the reader's verdict does not move when the
    input moves, every other assertion in this file is vacuous.

    Take the traced `not-for-me` round (raw_text_len 41) and blank its output —
    it must fall out of `quiet_traced` and into `ambiguous_quiet`.
    """
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    mutated = []
    for line in lines:
        if '"live_decision"' not in line:
            mutated.append(line)
            continue
        event = json.loads(line)
        if (
            event.get("extra", {}).get("decision") == "not-for-me"
            and event.get("session_id") == "sess-alpha"
        ):
            event["extra"]["raw_text_len"] = 0
        mutated.append(json.dumps(event, ensure_ascii=False))

    loaded = de.parse_lines(mutated, source="mutated")
    agg = de.aggregate(loaded.rounds, malformed=loaded.malformed)
    alpha = _session(agg, "sess-alpha")
    by_seq = {r["seq"]: r for r in alpha["rounds"]}

    assert by_seq[4]["output_state"] == de.OUTPUT_QUIET_BARE, (
        "the verdict did not move with the input — the criterion is vacuous"
    )
    assert "sess-alpha#4" in agg["ambiguous_quiet_rounds"]
    assert "sess-alpha#4" not in alpha["quiet_traced_rounds"]


# ---------------------------------------------------------------------------
# 7. typing / robustness
# ---------------------------------------------------------------------------


def test_non_numeric_latency_is_treated_as_missing_not_zero():
    loaded = de.parse_lines(
        [
            json.dumps(
                {
                    "ts": "2026-09-22T10:00:00.000Z",
                    "service": "webui",
                    "event": "live_decision",
                    "session_id": "s",
                    "latency_ms": "fast",
                    "extra": {"decision": "silence", "raw_text_len": 0},
                }
            )
        ]
    )
    assert loaded.rounds[0].latency_ms is None
    assert loaded.malformed == []


def test_boolean_is_not_accepted_as_a_number():
    """`True` is an `int` in Python — the coercion must not accept it."""
    assert de._as_int(True) is None
    assert de._as_int(False) is None
    assert de._as_int(3.9) == 3
    assert de._as_int(None) is None


def test_empty_input_is_reported_as_empty_not_as_success():
    agg = de.aggregate([])
    assert agg["totals"]["n_rounds"] == 0
    assert agg["n_sessions"] == 0


def test_iter_event_files_only_matches_the_service_day_files(tmp_path):
    (tmp_path / "webui-2026-09-22.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "webinfer-2026-09-22.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    names = [p.name for p in de.iter_event_files(tmp_path)]
    assert names == ["webui-2026-09-22.jsonl"]


def test_missing_directory_yields_no_files_rather_than_raising(tmp_path):
    assert de.iter_event_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# 8. human report + CLI exit codes (the regression gate #159 will reuse)
# ---------------------------------------------------------------------------


def test_report_renders_the_key_facts():
    report = de.format_report(_aggregate_fixture())
    assert "sess-alpha" in report
    assert "sess-beta" in report
    assert de.OUTPUT_EMPTY in report
    assert "latency_ms:" in report
    assert "frames_n:" in report
    # the unattributed round must be visible in the human view too
    assert de.UNATTRIBUTED in report


def test_cli_json_output_matches_the_aggregate(tmp_path, capsys):
    target = tmp_path / "webui-2026-09-22.jsonl"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    rc = de.main(["--events-dir", str(target), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["n_rounds"] == 9


def test_cli_fails_closed_when_no_events_exist(tmp_path, capsys):
    """★ 缺数据不得判绿 —— 「没测」与「通过」必须分开（#159 会复用这条）."""
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    rc = de.main(["--events-dir", str(empty)])
    assert rc == 1
    assert "缺失不等于通过" in capsys.readouterr().err


def test_cli_require_latency_fails_red_when_latency_is_missing(tmp_path, capsys):
    """★ Negative control at the CLI boundary: the missing-latency state must
    exit non-zero so a future gate cannot pass on it.

    NOTE: the repo fixture is **deliberately dirty** (it carries one missing
    latency and two malformed lines so the reader's error paths are exercised),
    so ``--require-latency`` is expected to go RED on it. That is the intended
    pairing with ``test_cli_require_latency_is_green_on_a_healthy_round_trip``:
    the gate must distinguish clean input from dirty input, not merely always
    pass or always fail.
    """
    target = tmp_path / "webui-2026-09-22.jsonl"
    lines = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if '"live_decision"' not in line:
            continue
        event = json.loads(line)
        event["latency_ms"] = None
        lines.append(json.dumps(event, ensure_ascii=False))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert de.main(["--events-dir", str(target), "--require-latency"]) == 1
    assert "判红" in capsys.readouterr().err
