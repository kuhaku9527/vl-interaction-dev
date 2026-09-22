# ruff: noqa: RUF001, RUF002, RUF003
"""验证仪式运行器 —— 行为测试（工单 #162）.

★ 为什么这个文件存在
--------------------
运行器的输出**会变成台账证据**。一条假的绿色行，比没有台账更坏 ——
本项目已有实证：`audit-frontend-residue.mjs` 在静态服务器服务错目录时报
**139 处死引用**，实测真值 **0**，而脚本不报错。差一个数量级、方向相反。

因此本文件把四件事钉成可执行断言，而不是靠人手演示一次：

1. **前置缺失 ⇒ 无法测量，且命令根本不执行**（139 假数字的根因）；
2. **判据按解析出的数字判，不按退出码判** —— 残留审计**永远退出 0**
   （`process.exit(0)` 在 main 末尾无条件执行），故「rc==0 即绿」会把
   139 读成 PASS；
3. **负控**：真机项失败必须判 FAIL 并体现在行里，不得静默跳过、不得整体报绿；
4. **四要素**：命令 / 结果 / 测量时间 / 真机或离线，缺一不可。

Run: python -m pytest scripts/tests/test_verify_ritual.py -q
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_ritual as vr  # noqa: E402

ROOT = SCRIPTS.parent
NOW = datetime(2026, 9, 22, 15, 30, 0, tzinfo=timezone.utc)

# 真实工具输出的形状（从本机实测输出抄下来的片段，不是编造的）。
OUT_STACK_GREEN = "=== verify-services.py ===\n[OK]   7060 llama-server\nALL GREEN\n"
OUT_STACK_DEAD = "=== verify-services.py ===\n[FAIL] 8099 webui\n      status=0\n4 FAILURES\n"
OUT_RESIDUE_0 = "index.html 中的 id: 294 ／ 运行时 DOM 中的 id: 294\n死引用 0 ／ 显式隐藏 73\n"
OUT_RESIDUE_139 = "index.html 中的 id: 294 ／ 运行时 DOM 中的 id: 0\n死引用 139 ／ 显式隐藏 0\n"
OUT_DRIFT_OK = "# ran_at=2026-09-22T07:30:00+00:00  total=1  block_fail=0  warn_fail=0\n[OK]\n"
OUT_DRIFT_BLOCK = "# ran_at=x total=1 block_fail=1 warn_fail=0\n[BLOCK] vlm-n_ctx\n"
OUT_CONTRACT_OK = (
    "前端引用的路由 30 个 ／ 后端注册的路由 45 个\n【BROKEN】前端在调、后端无此路由 —— 0 个\n"
)
OUT_CONTRACT_BROKEN = "【BROKEN】前端在调、后端无此路由 —— 2 个\n  ❌ /api/nope\n"
OUT_EVENTS_OK = "=== 决策事件聚合（live_decision @ webui）  sessions=3  rounds=4 ===\n"
OUT_RECALL_OK = "\nrecall@5 = 24 / 24 = 1.000\n"
OUT_RECALL_DROP = "\nrecall@5 = 23 / 24 = 0.958\n"
OUT_RECALL_DENOM = "\nrecall@5 = 25 / 25 = 1.000\n"


def _items() -> tuple[vr.Item, ...]:
    return vr.build_items(ROOT)


def _item(item_id: str) -> vr.Item:
    for it in _items():
        if it.item_id == item_id:
            return it
    raise AssertionError(f"no such ritual item: {item_id}")


def _verdict(text: str) -> str | None:
    """取汇总里的**结论行**（✅ / ❌ / 🟡 开头那行）.

    ★ 为什么不直接 `"ALL GREEN" in text`：服务栈那一项的**测量值**恰好就叫
    `ALL GREEN`（`verify-services.py` 自己的输出）。拿整篇文本判「有没有报绿」，
    一跑真机就会因为那一项而误判。故只判结论行 —— 判据要落在**结论**上。
    """
    for line in text.splitlines():
        if line[:1] in {"✅", "❌", "🟡"}:
            return line
    return None


def _run(items=None, *, probe, execute=None, now=None, full_ritual_ids=None):
    """跑一轮仪式的测试便捷入口.

    ★ 默认就把 `full_ritual_ids` 填成**完整仪式**的 id —— 因为生产代码里
    `run_ritual` 的这个参数是**必填**的（不给默认值，防止漏传导致子集被报成全绿）。
    要测「子集」语义的用例显式传 `full_ritual_ids`。
    """
    all_items = list(_items())
    picked = list(items) if items is not None else all_items
    kwargs = {
        "probe": probe,
        "full_ritual_ids": (
            full_ritual_ids if full_ritual_ids is not None else [it.item_id for it in all_items]
        ),
    }
    if execute is not None:
        kwargs["execute"] = execute
    if now is not None:
        kwargs["now"] = now
    return vr.run_ritual(picked, **kwargs)


def _all_ok(_claim: str) -> tuple[bool, str]:
    return True, "probe-ok"


class _Recorder:
    """记录哪些命令真被执行过的假执行器。"""

    def __init__(self, responses: dict[str, tuple[int, str]] | None = None, default=None):
        self.calls: list[vr.Command] = []
        self.responses = responses or {}
        self.default = default or (lambda cmd: (0, ""))

    def __call__(self, cmd: vr.Command) -> tuple[int, str]:
        self.calls.append(cmd)
        for key, resp in self.responses.items():
            if key in cmd.display:
                return resp
        return self.default(cmd)


# ---------------------------------------------------------------------------
# 1. 仪式项本身：规格 §4.2 的六步都要在，且真机/离线标记明确
# ---------------------------------------------------------------------------


def test_ritual_covers_the_six_spec_steps():
    """规格 §4.2 表里的六步必须都在运行器里（少一步就是「仪式不完整」）。"""
    ids = {it.item_id for it in _items()}
    assert {
        "stack",
        "drift-runtime",
        "residue",
        "api-contract",
        "decision-events",
        "golden-recall",
    } <= ids


def test_modes_are_marked_and_offline_items_need_no_services():
    """真机/离线必须显式标记，且离线项不得带「服务在跑」这类前置。"""
    modes = {it.item_id: it.mode for it in _items()}
    assert modes["stack"] == vr.MODE_LIVE
    assert modes["drift-runtime"] == vr.MODE_LIVE
    assert modes["residue"] == vr.MODE_LIVE
    assert modes["api-contract"] == vr.MODE_OFFLINE
    assert modes["decision-events"] == vr.MODE_OFFLINE
    assert modes["golden-recall"] == vr.MODE_OFFLINE

    offline_claims = {
        c.claim_id for it in _items() if it.mode == vr.MODE_OFFLINE for c in it.preconditions
    }
    assert not (offline_claims & {vr.CLAIM_LLAMA, vr.CLAIM_STATIC_SERVER})


def test_every_item_declares_a_copyable_command():
    """每项都要有一条可复制的命令 —— 台账的「命令」列不能是空的。"""
    for it in _items():
        assert it.command.argv, it.item_id
        assert it.command.display.strip(), it.item_id


# ---------------------------------------------------------------------------
# 2. ★ 前置缺失 ⇒ 无法测量；且命令根本不执行（139 假数字的根因）
# ---------------------------------------------------------------------------


def test_missing_static_server_is_unmeasurable_and_never_runs_the_probe():
    """★ 核心断言：静态服务器不在位时，残留审计项**不得给出数字**。

    139 vs 0 的根因就是「让脚本跑了」—— 它会在空白页面上把每个 id 都判死。
    故这里断言两件事同时成立：判「无法测量」，且**命令压根没被执行**。
    """
    rec = _Recorder({"audit-frontend-residue": (0, OUT_RESIDUE_139)})

    def probe(claim: str) -> tuple[bool, str]:
        if claim == vr.CLAIM_STATIC_SERVER:
            return False, "8123 无服务监听"
        return True, "ok"

    results = _run(_items(), probe=probe, execute=rec, now=lambda: NOW)
    residue = vr.result_for(results, "residue")

    assert residue.status == vr.STATUS_UNMEASURABLE
    assert residue.measurement is None
    assert not any("audit-frontend-residue" in c.display for c in rec.calls), (
        "★ 前置缺失时不得执行命令"
    )
    assert "139" not in vr.format_ledger_rows(results), "★ 假数字不得出现在台账行里"


def test_static_server_wrong_directory_is_also_unmeasurable():
    """8123 有服务、但服务的不是本仓库 static 目录 —— 同样是「无法测量」。

    这正是历史事故的形态（残留 http.server 占着端口服务错目录 → 404 → DOM=0）。
    只看「端口是否在听」是不够的：那种情况下端口是通的。
    """

    def probe(claim: str) -> tuple[bool, str]:
        if claim == vr.CLAIM_STATIC_SERVER:
            return (
                False,
                "8123 在跑，但 index.html 内容 hash 与本仓库 static/index.html 不符（服务错目录）",
            )
        return True, "ok"

    rec = _Recorder({"audit-frontend-residue": (0, OUT_RESIDUE_139)})
    results = _run(_items(), probe=probe, execute=rec, now=lambda: NOW)
    residue = vr.result_for(results, "residue")

    assert residue.status == vr.STATUS_UNMEASURABLE
    assert "hash" in residue.detail or "错目录" in residue.detail
    assert not any("audit-frontend-residue" in c.display for c in rec.calls), (
        "★ 服务错目录时同样不得执行审计命令"
    )


def test_unmeasurable_row_is_not_a_number_and_says_why():
    """「无法测量」行必须显式说明原因，且结果列不得是数字。"""

    def probe(claim: str) -> tuple[bool, str]:
        if claim == vr.CLAIM_STATIC_SERVER:
            return False, "8123 无服务监听"
        return True, "ok"

    results = _run(_items(), probe=probe, execute=_Recorder(), now=lambda: NOW)
    row = vr.format_ledger_rows(results)
    line = next(ln for ln in row.splitlines() if "残留" in ln)

    assert vr.STATUS_UNMEASURABLE in line
    assert "8123 无服务监听" in line
    # 只看「结果」列（第 3 格）：标题里本就含「死引用」三字，不能拿整行判。
    result_cell = line.strip("|").split("|")[2]
    assert "死引用" not in result_cell, "★ 测不了的结果格不得带任何测量值"
    assert not re.search(r"死引用\s*\d", result_cell), "★ 更不得出现数字"


# ---------------------------------------------------------------------------
# 3. ★ 判据按数字判，不按退出码判（残留审计恒退出 0）
# ---------------------------------------------------------------------------


def test_residue_is_judged_on_the_parsed_count_not_the_exit_code():
    """★ 残留审计恒 `process.exit(0)` —— rc 完全无区分力。

    「rc==0 即绿」会把 139 读成 PASS。故判据必须是解析出的死引用数。
    """
    status, measurement, _detail = vr.judge("residue", 0, OUT_RESIDUE_139)
    assert status == vr.STATUS_FAIL, "rc=0 但死引用 139 ⇒ 必须 FAIL"
    # 测量值同时带上「运行时 DOM 中的 id」—— 那正是 139 这个假数字的冒烟枪：
    # DOM id = 0 说明页面根本没载入，139 不是「残留很多」而是「一个都没读到」。
    assert measurement.startswith("死引用 139")
    assert "DOM id 0" in measurement

    status, measurement, _detail = vr.judge("residue", 0, OUT_RESIDUE_0)
    assert status == vr.STATUS_PASS
    assert measurement.startswith("死引用 0")


def test_residue_unparseable_output_fails_rather_than_passing():
    """解析不到数字 ⇒ 「无法判读」必须判红，不得 fail-open 成绿。"""
    status, measurement, detail = vr.judge("residue", 0, "no summary line here\n")
    assert status == vr.STATUS_FAIL
    assert measurement is None
    assert "判读" in detail or "解析" in detail


@pytest.mark.parametrize(
    ("item_id", "rc", "output", "expected"),
    [
        ("stack", 0, OUT_STACK_GREEN, vr.STATUS_PASS),
        ("stack", 2, OUT_STACK_DEAD, vr.STATUS_FAIL),
        ("drift-runtime", 0, OUT_DRIFT_OK, vr.STATUS_PASS),
        ("drift-runtime", 1, OUT_DRIFT_BLOCK, vr.STATUS_FAIL),
        ("api-contract", 0, OUT_CONTRACT_OK, vr.STATUS_PASS),
        ("api-contract", 1, OUT_CONTRACT_BROKEN, vr.STATUS_FAIL),
        ("decision-events", 0, OUT_EVENTS_OK, vr.STATUS_PASS),
        ("golden-recall", 0, OUT_RECALL_OK, vr.STATUS_PASS),
        ("golden-recall", 0, OUT_RECALL_DROP, vr.STATUS_FAIL),
    ],
)
def test_judges(item_id, rc, output, expected):
    """逐项判据表（含真实输出片段）。"""
    status, _measurement, _detail = vr.judge(item_id, rc, output)
    assert status == expected


def test_drift_runtime_probe_failure_is_unmeasurable_not_pass():
    """drift_gate 的 rc=3（RUNTIME-PROBE-FAILED）是「测不了」，不是绿也不是红。"""
    status, measurement, detail = vr.judge(
        "drift-runtime", 3, "[RUNTIME-PROBE-ERROR] [RUNTIME-PROBE-FAILED] probe 失败 (rc=2)"
    )
    assert status == vr.STATUS_UNMEASURABLE
    assert measurement is None
    assert "probe" in detail.lower()


def test_golden_recall_denominator_is_pinned():
    """分母变了必须判红 —— 本项目踩过 25 vs 26 静默失效（见台账纪律 5）。"""
    status, measurement, detail = vr.judge("golden-recall", 0, OUT_RECALL_DENOM)
    assert status == vr.STATUS_FAIL
    assert "分母" in detail
    # 测量值报的是**实测到的**分数（25/25 确实全命中），判红的原因是分母变了。
    assert measurement == "25 / 25"


def test_golden_recall_unavailable_embedder_is_unmeasurable():
    """评测器自身打印「无法测量」并 rc=1 ⇒ 运行器不得把它当成回归判红。"""
    status, measurement, _detail = vr.judge(
        "golden-recall", 1, "vector mode requires SILICONFLOW_API_KEY. Top up siliconflow ..."
    )
    assert status == vr.STATUS_UNMEASURABLE
    assert measurement is None


# ---------------------------------------------------------------------------
# 4. ★ 负控：服务停掉 ⇒ 真机项判 FAIL，且整体不得报绿
# ---------------------------------------------------------------------------


def test_stopped_services_are_a_fail_not_a_skip():
    """负控：真机项失败必须判 FAIL，行里体现，且整体判红。"""
    rec = _Recorder({"verify-services": (2, OUT_STACK_DEAD)})
    results = _run(_items(), probe=_all_ok, execute=rec, now=lambda: NOW)

    stack = vr.result_for(results, "stack")
    assert stack.status == vr.STATUS_FAIL
    assert vr.exit_code(results) == 1, "★ 有 FAIL 即整体判红，不得报绿"
    assert _verdict(vr.format_summary(results))[:1] != "✅", "有 FAIL 时结论不得是绿"
    assert vr.STATUS_FAIL in vr.format_ledger_rows(results)


def test_services_down_marks_live_items_unmeasurable_but_offline_items_still_run():
    """服务未起 ⇒ 明确告知「真机项不可测」，离线项照常跑（不得整体中止）。"""
    live_claims = {vr.CLAIM_LLAMA, vr.CLAIM_STATIC_SERVER, vr.CLAIM_CHROME}

    def probe(claim: str) -> tuple[bool, str]:
        if claim in live_claims:
            return False, "服务未起"
        return True, "ok"

    rec = _Recorder(
        {
            "verify-services": (2, OUT_STACK_DEAD),
            "drift_gate": (3, "[RUNTIME-PROBE-FAILED] probe 失败"),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    results = _run(_items(), probe=probe, execute=rec, now=lambda: NOW)

    assert vr.result_for(results, "drift-runtime").status == vr.STATUS_UNMEASURABLE
    assert vr.result_for(results, "residue").status == vr.STATUS_UNMEASURABLE
    # 离线项照常跑完并给出结果
    assert vr.result_for(results, "api-contract").status == vr.STATUS_PASS
    assert vr.result_for(results, "decision-events").status == vr.STATUS_PASS
    assert vr.result_for(results, "golden-recall").status == vr.STATUS_PASS

    summary = vr.format_summary(results)
    assert "真机项不可测" in summary
    assert vr.exit_code(results) == 1  # stack 真跑了且 FAIL


def test_no_fail_but_unmeasurable_is_a_distinct_non_green_exit():
    """无 FAIL 但有「无法测量」⇒ 退出码 2（部分可测），不得返回 0。"""
    rec = _Recorder(
        {
            "verify-services": (0, OUT_STACK_GREEN),
            "drift_gate": (0, OUT_DRIFT_OK),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )

    def probe(claim: str) -> tuple[bool, str]:
        if claim == vr.CLAIM_STATIC_SERVER:
            return False, "8123 无服务监听"
        return True, "ok"

    results = _run(_items(), probe=probe, execute=rec, now=lambda: NOW)
    assert vr.exit_code(results) == 2


def test_all_pass_is_zero_and_says_all_green():
    """全项通过 ⇒ 0，且明确报 ALL GREEN。"""
    rec = _Recorder(
        {
            "verify-services": (0, OUT_STACK_GREEN),
            "drift_gate": (0, OUT_DRIFT_OK),
            "audit-frontend-residue": (0, OUT_RESIDUE_0),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    results = _run(_items(), probe=_all_ok, execute=rec, now=lambda: NOW)
    assert [r.status for r in results] == [vr.STATUS_PASS] * len(results)
    assert vr.exit_code(results) == 0
    assert _verdict(vr.format_summary(results)).startswith("✅")


# ---------------------------------------------------------------------------
# 5. 四要素：命令 / 结果 / 测量时间 / 真机或离线
# ---------------------------------------------------------------------------


def test_ledger_rows_carry_all_four_elements():
    """★ 输出的每一行都要含四要素（规格 §4.2 的硬要求）。"""
    rec = _Recorder(
        {
            "verify-services": (0, OUT_STACK_GREEN),
            "drift_gate": (0, OUT_DRIFT_OK),
            "audit-frontend-residue": (0, OUT_RESIDUE_0),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    results = _run(_items(), probe=_all_ok, execute=rec, now=lambda: NOW)
    body = vr.format_ledger_rows(results)
    lines = [
        ln
        for ln in body.splitlines()
        if ln.startswith("|") and "---" not in ln and "测量时间" not in ln
    ]
    assert len(lines) == len(results)

    for line, res in zip(lines, results, strict=True):
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == 5, line  # 项 / 命令 / 结果 / 真机? / 时间
        assert res.command.display in cells[1] or cells[1].strip("`") == res.command.display
        assert cells[2], "结果列不得为空"
        assert cells[3] in {vr.MODE_LIVE, vr.MODE_OFFLINE}, "真机/离线标记必须明确"
        assert cells[4].startswith("2026-09-22T"), "必须写测量时间"


def test_every_row_time_comes_from_the_clock_it_was_given():
    """测量时间是**实测那一刻**，不是文档撰写日 —— 注入时钟以钉住这一点."""
    later = datetime(2026, 9, 23, 1, 2, 3, tzinfo=timezone.utc)
    results = _run(_items(), probe=_all_ok, execute=_Recorder(), now=lambda: later)
    for res in results:
        assert res.observed_at == later
    assert "2026-09-23T01:02:03" in vr.format_ledger_rows(results)


def test_ledger_rows_include_the_header_shape_for_pasting():
    """输出必须自带表头，才能「直接粘进台账」。"""
    results = _run(_items(), probe=_all_ok, execute=_Recorder(), now=lambda: NOW)
    body = vr.format_ledger_rows(results)
    header = body.splitlines()[0]
    assert header.startswith("|")
    for col in ("命令", "结果", "真机", "测量时间"):
        assert col in header, f"表头缺列: {col}"


# ---------------------------------------------------------------------------
# 6. 只读探针：不得写被测对象；不得外泄密钥
# ---------------------------------------------------------------------------


def test_drift_invocation_disables_its_own_history_write():
    """运行器本身只读；drift_gate 的历史写入必须显式关掉（--no-history）。"""
    cmd = _item("drift-runtime").command
    assert "--no-history" in cmd.argv


def test_no_secret_value_is_ever_printed():
    """密钥值绝不出现在输出里（只允许出现变量名）。"""
    secret = "sk-super-secret-value-0123456789"
    results = _run(
        _items(),
        probe=lambda claim: (True, f"probe ok ({secret})"),
        execute=lambda cmd: (0, f"{secret}\n" + OUT_RECALL_OK),
        now=lambda: NOW,
    )
    blob = (
        vr.format_ledger_rows(results)
        + vr.format_summary(results)
        + json.dumps(vr.to_json(results), ensure_ascii=False)
    )
    assert secret not in blob


def test_env_file_loader_reports_names_not_values(tmp_path: Path):
    """载入 env 文件只回报「载入了哪些键」，绝不回报值。"""
    env_file = tmp_path / "sample.env"
    env_file.write_text(
        "# comment\nexport SILICONFLOW_API_KEY=sk-top-secret\nOTHER=1\n",
        encoding="utf-8",
    )
    target: dict[str, str] = {}
    loaded = vr.load_env_file(env_file, target)
    assert loaded == ("SILICONFLOW_API_KEY", "OTHER")
    assert target["SILICONFLOW_API_KEY"] == "sk-top-secret"
    assert "sk-top-secret" not in repr(loaded)


def test_env_file_loader_never_overrides_an_existing_variable(tmp_path: Path):
    """已在环境里的值优先 —— 运行器不得悄悄覆盖调用者的配置。"""
    env_file = tmp_path / "sample.env"
    env_file.write_text("SILICONFLOW_API_KEY=from-file\n", encoding="utf-8")
    target = {"SILICONFLOW_API_KEY": "from-env"}
    vr.load_env_file(env_file, target)
    assert target["SILICONFLOW_API_KEY"] == "from-env"


def test_missing_env_file_is_not_an_error(tmp_path: Path):
    """env 文件不存在 ⇒ 返回空，不抛异常（前置自检会另行报「无法测量」）。"""
    assert vr.load_env_file(tmp_path / "nope.env", {}) == ()


# ---------------------------------------------------------------------------
# 7. 样例文件选择：decision-events 用最新的 webui-*.jsonl，并写明用的是哪一份
# ---------------------------------------------------------------------------


def test_newest_event_file_picks_the_latest_day(tmp_path: Path):
    """选最新一份，并把它写进结果 —— 「口径」必须可见（台账纪律 5）。"""
    for day in ("2026-09-20", "2026-09-22", "2026-09-19"):
        (tmp_path / f"webui-{day}.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "memory-store-2026-09-22.jsonl").write_text("{}\n", encoding="utf-8")
    assert vr.newest_event_file(tmp_path).name == "webui-2026-09-22.jsonl"


def test_newest_event_file_returns_none_when_absent(tmp_path: Path):
    assert vr.newest_event_file(tmp_path) is None


def test_decision_events_command_names_its_sample_file(tmp_path: Path):
    """命令里必须钉住具体文件（不是整个目录）—— 否则老数据的缺失会污染判据。

    ★ 用 tmp root 而非本仓库真实 root：`logs/` 不入库，CI 上根本不存在。
    若这条测试依赖真机状态，它在 CI 上就会退化成一条「只在本机能过」的假绿。
    """
    events = tmp_path / "logs" / "events"
    events.mkdir(parents=True)
    (events / "webui-2026-09-20.jsonl").write_text("{}\n", encoding="utf-8")
    (events / "webui-2026-09-22.jsonl").write_text("{}\n", encoding="utf-8")

    cmd = next(it for it in vr.build_items(tmp_path) if it.item_id == vr.ITEM_EVENTS).command
    joined = " ".join(cmd.argv)
    assert "--events-dir" in joined
    assert "webui-2026-09-22.jsonl" in joined, "必须钉住最新那份具体文件"
    assert "--require-latency" in cmd.argv


def test_events_falls_back_to_directory_when_no_sample_exists(tmp_path: Path):
    """无任何事件文件时（CI / 新克隆）退化为目录 —— 不得崩，由运行期判「无法测量」。"""
    cmd = next(it for it in vr.build_items(tmp_path) if it.item_id == vr.ITEM_EVENTS).command
    assert "--events-dir" in " ".join(cmd.argv)
    status, measurement, _detail = vr.judge(
        vr.ITEM_EVENTS, 1, "!! 未找到任何 live_decision 事件（输入：x）；缺失不等于通过"
    )
    assert status == vr.STATUS_UNMEASURABLE
    assert measurement is None


# ---------------------------------------------------------------------------
# 8. JSON 形状（供后续四票复用）
# ---------------------------------------------------------------------------


def test_json_export_has_four_elements_and_status():
    results = _run(_items(), probe=_all_ok, execute=_Recorder(), now=lambda: NOW)
    payload = vr.to_json(results)
    assert isinstance(payload, list)
    for row in payload:
        assert set(row) >= {"item_id", "command", "status", "mode", "observed_at"}


# ---------------------------------------------------------------------------
# 9. CLI 行为（默认逐项明细；--summary 真的更短）
# ---------------------------------------------------------------------------


def test_summary_flag_is_not_a_noop():
    """★ `--summary` 必须真的比默认输出短。

    这条是补的回归测试：初版 `--summary` 与默认分支打印**完全相同**的内容 ——
    一个什么都不做的旗标比没有旗标更坏（用户会以为它生效了）。
    """
    results = _run(
        _items(),
        probe=_all_ok,
        execute=lambda cmd: (0, OUT_STACK_GREEN),
        now=lambda: NOW,
    )
    verbose = vr.format_summary(results, verbose=True)
    brief = vr.format_summary(results, verbose=False)

    assert len(brief) < len(verbose), "--summary 必须真的更短"
    assert "命令：" not in brief, "简版不得逐项列出命令"
    # 但两者都必须保留台账行与结论 —— 简版不是「更少的证据」
    assert vr.format_ledger_rows(results) in brief
    assert vr.format_ledger_rows(results) in verbose


def test_default_output_keeps_per_item_commands():
    """默认输出要逐项给出命令 —— 「命令」是四要素之一，不得只在台账行里出现."""
    results = _run(_items(), probe=_all_ok, execute=_Recorder(), now=lambda: NOW)
    verbose = vr.format_summary(results, verbose=True)
    for res in results:
        assert res.command.display in verbose


# ---------------------------------------------------------------------------
# 10. ★★ 「一项都没跑」绝不能报绿（本轮 code-review 查出的阻断级缺陷）
# ---------------------------------------------------------------------------


def test_empty_result_set_is_never_green():
    """★★ 阻断级：空结果集不得判绿。

    这是本票**要消灭的那个病的同构体**：没有任何一项真的跑过，却打印
    「✅ ALL GREEN（本轮的每一项都真跑过且通过）」。本文件的模块 docstring 写死了
    「没有任何路径会因为没有运行而返回 0」—— 初版 `exit_code([])` 返回 0，自相矛盾。

    触发路径真实存在：`--only ","` / `--only "  "` 解析出的项集为空。
    """
    assert vr.exit_code([]) != 0, "★ 一项都没跑，绝不能返回 0"
    assert vr.exit_code([]) == 2


def test_empty_result_set_summary_does_not_claim_all_green():
    """空集的汇总文案也不得出现「ALL GREEN」这种通过字样。"""
    text = vr.format_summary([])
    assert not _verdict(text).startswith("✅")
    assert "每一项都真跑过且通过" not in text
    assert "未选中任何项" in text or "没有可跑的项" in text


def test_only_with_no_valid_ids_is_rejected_not_silently_empty():
    """`--only ","` 这类「一个有效 id 都没有」的输入必须显式报错，不得静默跑空。"""
    with pytest.raises(SystemExit):
        vr.selected_items(vr.build_items(), ",")
    with pytest.raises(SystemExit):
        vr.selected_items(vr.build_items(), "  ")


def test_only_empty_string_is_not_treated_as_no_flag():
    """★★ `--only ""` 必须报错，**不得**被当成「没给这个旗标」而跑起整轮真机仪式。

    终局对抗性复核查出的隐蔽脚枪：`--only "$IDS"` 在 `IDS` 为空时会展开成 `""`，
    而 `if not only` 把 `None`（没给旗标）与 `""`（给了但为空）**当成同一件事** ⇒
    静默跑起完整真机仪式（耗时数分钟，且可能 FAIL）。
    这仍然是 fail-closed（不会报假绿），但「静默做了一件你没要求的事」本身就是缺陷。
    """
    with pytest.raises(SystemExit) as exc:
        vr.selected_items(vr.build_items(), "")
    assert "空白" in str(exc.value)

    # 对照组：`None` = 真的没给旗标 ⇒ 跑完整仪式（这是正确行为，必须保留）
    assert len(vr.selected_items(vr.build_items(), None)) == len(vr.build_items())


def test_only_subset_cannot_report_all_green():
    """★ 子集运行不得自称 ALL GREEN —— 未跑的项与「跑过且通过」是两回事。

    `--only` 是本票文档推荐的「无服务环境」用法，故它必须能表达
    「本轮只覆盖了一部分」，而不是把没选的项当作通过。

    这里按 `main()` 的**真实调用形状**传 `full_ritual_ids`（完整仪式的 id）。
    """
    rec = _Recorder(
        {
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    full = vr.build_items()
    subset = vr.selected_items(full, "api-contract,decision-events,golden-recall")
    run = _run(
        subset,
        probe=_all_ok,
        execute=rec,
        now=lambda: NOW,
        full_ritual_ids=[it.item_id for it in full],
    )

    # 这三项本身都通过，但整体**不是**「全仪式全绿」
    assert all(r.status == vr.STATUS_PASS for r in run)
    assert len(run) < len(full), "子集确实少于全部仪式项"

    # 未覆盖的项必须被点名（不是静默略过）
    assert set(run.uncovered_ids) == {"stack", "drift-runtime", "residue"}
    assert not run.is_complete

    summary = vr.format_summary(run, full_ritual=full)
    assert not _verdict(summary).startswith("✅"), "★ 子集运行不得自称全绿"
    assert "未覆盖" in summary
    assert "服务栈探活" in summary, "未覆盖项要按标题点名"
    assert vr.exit_code(run) != 0, "★ 未跑完整个仪式 ⇒ 退出码不得为 0"


def test_full_ritual_all_pass_is_the_only_green_path():
    """只有「跑完全部仪式项且全通过」才配得上退出码 0。"""
    rec = _Recorder(
        {
            "verify-services": (0, OUT_STACK_GREEN),
            "drift_gate": (0, OUT_DRIFT_OK),
            "audit-frontend-residue": (0, OUT_RESIDUE_0),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    full = vr.build_items()
    run = _run(
        full,
        probe=_all_ok,
        execute=rec,
        now=lambda: NOW,
        full_ritual_ids=[it.item_id for it in full],
    )
    assert run.is_complete
    assert vr.exit_code(run) == 0
    assert _verdict(vr.format_summary(run, full_ritual=full)).startswith("✅")


# ---------------------------------------------------------------------------
# 11. 判据的独立真值源：钉住的分母必须与磁盘上的 golden 集一致
# ---------------------------------------------------------------------------


def test_pinned_denominator_matches_the_golden_set_on_disk():
    """钉死的分母必须与仓库里的 golden 集**实际条数**一致。

    分母若被静默改动而我们只在运行期才知道，台账纪律 5 要防的
    「跨变体比较静默失效」就会重新发生。故此处把它拽红，逼人显式改常量。
    """
    golden = ROOT / "services/memory-store/tools/golden_recall_set.json"
    if not golden.is_file():
        # 该文件已入库，正常 checkout 必然存在；缺失只在被裁剪的工作树里出现。
        # 此时 skip 而不是 fail —— 但要**显式** skip，不得静默通过。
        pytest.skip(f"golden 集不在位（{golden}）；该文件已入库，正常 checkout 应存在")
    entries = json.loads(golden.read_text(encoding="utf-8"))
    assert len(entries) == vr.GOLDEN_EXPECTED_DENOMINATOR, (
        f"golden 集有 {len(entries)} 条，但运行器钉住的分母是 "
        f"{vr.GOLDEN_EXPECTED_DENOMINATOR} —— 请显式同步 GOLDEN_EXPECTED_DENOMINATOR"
    )


# ---------------------------------------------------------------------------
# 12. 前置探针只探一次（非幂等探针不得被调用两次）
# ---------------------------------------------------------------------------


def test_each_precondition_is_probed_exactly_once():
    """★ 每个前置只探一次。

    初版 `run_ritual` 对同一 claim 调了两次 `probe()`（一次判真、一次取 detail），
    而探针里有 HTTP 请求与 sha256 文件读 —— 白跑一遍，且对非幂等探针是错的。
    """
    calls: list[str] = []

    def counting_probe(claim: str) -> tuple[bool, str]:
        calls.append(claim)
        return True, "ok"

    _run(_items(), probe=counting_probe, execute=_Recorder(), now=lambda: NOW)

    expected = [pc.claim_id for it in _items() for pc in it.preconditions]
    assert sorted(calls) == sorted(expected), "每个前置恰好探一次"
    # 逐项核对：同一 claim 在同一次运行里不得重复出现
    assert len(calls) == len(set(calls)) or len(expected) != len(set(expected)), (
        f"有前置被探了多次：{calls}"
    )


def test_failing_precondition_is_also_probed_once():
    """失败的探针同样只调一次（初版正是在「失败取 detail」时多调一次）。"""
    calls: list[str] = []

    def failing_probe(claim: str) -> tuple[bool, str]:
        calls.append(claim)
        return False, "down"

    _run(_items(), probe=failing_probe, execute=_Recorder(), now=lambda: NOW)
    assert len(calls) == len(set(calls)), f"有前置被重复探测：{calls}"


# ---------------------------------------------------------------------------
# 13. 口径可见：事件项的「读的是哪一天」必须进得了台账行
# ---------------------------------------------------------------------------


def test_events_scope_names_the_sample_file_it_read(tmp_path: Path):
    """★ 口径必须可见：事件文件按日切分，「最新一份」会随日期静默变化。

    不写明读的是哪一天，后人拿两轮结果对比时可能比的是不同日的数据而毫无察觉 ——
    这正是台账纪律 5（eval 类必须写口径/分母）要防的那类静默失真。
    """
    events = tmp_path / "logs" / "events"
    events.mkdir(parents=True)
    (events / "webui-2026-09-19.jsonl").write_text("{}\n", encoding="utf-8")
    (events / "webui-2026-09-22.jsonl").write_text("{}\n", encoding="utf-8")

    item = next(it for it in vr.build_items(tmp_path) if it.item_id == vr.ITEM_EVENTS)
    assert "webui-2026-09-22.jsonl" in item.scope_note, "口径要点名具体文件"


def test_scope_note_reaches_the_ledger_row(tmp_path: Path):
    """口径必须真的出现在台账行里（不是只存在数据类字段里没人看）."""
    events = tmp_path / "logs" / "events"
    events.mkdir(parents=True)
    (events / "webui-2026-09-22.jsonl").write_text("{}\n", encoding="utf-8")

    item = next(it for it in vr.build_items(tmp_path) if it.item_id == vr.ITEM_EVENTS)
    run = _run((item,), probe=_all_ok, execute=lambda cmd: (0, OUT_EVENTS_OK), now=lambda: NOW)
    row = vr.format_ledger_rows(run)
    assert "webui-2026-09-22.jsonl" in row, "台账行必须自带口径"
    assert vr.to_json(run)[0]["scope_note"], "JSON 也要带口径"


def test_interpreter_is_reported_because_python_may_be_a_stub():
    """实际解释器必须被报出来 —— 本机 `python` 是静默失败的存根。

    命令列写成 `python ...` 是为了可复制（仓库文档惯例），但那在本机跑不通；
    故实际解释器单独成行，避免「照抄命令却得到别的结果」。
    """
    line = vr.interpreter_line()
    assert sys.executable in line
    assert "python" in line.lower()


# ---------------------------------------------------------------------------
# 13b. 可执行白名单（DeepSec 门禁查出：裸 subprocess 调用被判命令注入）
# ---------------------------------------------------------------------------


def test_execute_refuses_an_executable_outside_the_registry():
    """★★ 白名单必须真的拦下非注册程序 —— 而不是只写在注释里。

    `execute()` 会起子进程。argv 事实上由 :func:`build_items` 的注册表固定给出，
    **不来自用户输入**；但「事实如此」与「代码保证如此」是两回事。
    本仓纪律是**改写消除触发条件**，不是加 ignore 规则掩盖（见 `.deepsecignore`）。
    """
    rc, out = vr.execute(vr.Command(argv=("/bin/sh", "-c", "echo pwned"), display="sh -c ..."))
    assert rc == 126, "非白名单程序必须被拒绝（126 = 拒绝执行）"
    assert "pwned" not in out, "★ 绝不能被真的执行"
    assert "白名单" in out


def test_execute_refuses_an_empty_argv():
    rc, out = vr.execute(vr.Command(argv=(), display="(empty)"))
    assert rc == 126
    assert "空" in out


def test_allowlist_covers_every_ritual_command():
    """★ 白名单必须覆盖本仪式全部子命令 —— 否则运行器会自己把自己拦下。

    这条同时防「加了新项、忘了把它的解释器/程序加进白名单」。
    """
    for it in _items():
        program = it.command.argv[0]
        assert Path(program.replace("\\", "/")).name.lower() in vr._ALLOWED_EXECUTABLES, (
            f"{it.item_id} 的程序 {program!r} 不在白名单内"
        )


def test_allowlist_accepts_absolute_interpreter_paths():
    """白名单按 basename 比对，故绝对路径的解释器（本机常态）必须通过。"""
    parts = vr._assert_executable_allowed((sys.executable, "-c", "print(1)"))
    assert parts[0] == sys.executable


# ---------------------------------------------------------------------------
# 14. 判据表与仪式项表必须严格对应（漏配 = 无声的漏洞）
# ---------------------------------------------------------------------------


def test_every_ritual_item_has_a_judge():
    """★ 每个仪式项都必须有判据；新增项却忘了配判据要立刻红。

    这防的是「加了项、但判据走默认分支」——默认判绿就是又一个假信号源。
    """
    for it in _items():
        status, _measurement, _detail = vr.judge(it.item_id, 0, "some output")
        assert status in {vr.STATUS_PASS, vr.STATUS_FAIL, vr.STATUS_UNMEASURABLE}


def test_unknown_item_id_raises_rather_than_defaulting_to_pass():
    """★ 未知项必须抛错，**不得**回落到任何判绿路径。

    初版对未知 item_id 返回 FAIL（尚可），但真正的风险是将来有人把默认分支
    写成 PASS。此处把「没有判据 ⇒ 不可能是通过」钉死。
    """
    with pytest.raises(KeyError):
        vr.judge("no-such-item", 0, "ALL GREEN\n死引用 0")


def test_judge_table_and_item_registry_agree():
    """判据表的键集合必须与仪式项集合**完全相等**（多一个少一个都是漂移）."""
    assert set(vr._JUDGES) == {it.item_id for it in _items()}


def test_unparseable_output_never_judges_pass():
    """★ 空/垃圾输出对**任何**项都不得判 PASS（判据缺失不得当作通过）."""
    for it in _items():
        status, measurement, _detail = vr.judge(it.item_id, 0, "")
        assert status != vr.STATUS_PASS, f"{it.item_id} 在空输出上判了 PASS"
        assert measurement is None, f"{it.item_id} 在空输出上给出了测量值"


def test_residue_judge_refuses_a_self_contradictory_zero():
    """★★ 「死引用 0」+「运行时 DOM 中的 id 0」是**自相矛盾**的读数，不得判 PASS。

    判据必须**自我防御**，不能只靠前置守卫把这条路堵住：
    DOM 里一个 id 都没有 ⇒ 页面根本没载入 ⇒ 「0 处死引用」不是「没有残留」，
    而是「一个 id 都没读到」。这一对读数正是 139 假数字的冒烟枪
    （真机 139 那次，DOM id 恰好就是 0）。
    """
    status, measurement, detail = vr.judge(
        "residue", 0, "index.html 中的 id: 294 ／ 运行时 DOM 中的 id: 0\n死引用 0 ／ 显式隐藏 0\n"
    )
    assert status == vr.STATUS_FAIL, "★ DOM id=0 时不得判通过"
    assert "DOM id 0" in measurement
    assert "自相矛盾" in detail


def test_residue_judge_passes_only_with_a_real_page_load():
    """对照：DOM id 非 0 且死引用 0 才是真通过（证明上一条没有把正常情形也判红）。"""
    status, measurement, _detail = vr.judge(
        "residue",
        0,
        "index.html 中的 id: 294 ／ 运行时 DOM 中的 id: 294\n死引用 0 ／ 显式隐藏 73\n",
    )
    assert status == vr.STATUS_PASS
    assert "DOM id 294" in measurement


# ---------------------------------------------------------------------------
# 15. ★★ 对抗性复核查出的三处 fail-open（必须封死）
# ---------------------------------------------------------------------------


def test_full_ritual_ids_is_required_so_a_subset_cannot_default_to_complete():
    """★★ `full_ritual_ids` 不得有默认值 —— 默认值本身就是 fail-open。

    对抗性复核查出的真实漏洞：初版给 `full_ritual_ids=None` 兜底成「传入的项即完整
    仪式」⇒ **任何省略该参数的调用方都能把子集报成全绿**。
    只有 `main()` 传了它，于是缺陷只在库调用路径上存在 —— 最难被发现的那一类。

    修法是**取消默认值**：漏传在调用点就是 TypeError，而不是悄悄变成绿。
    """
    import inspect

    sig = inspect.signature(vr.run_ritual)
    param = sig.parameters["full_ritual_ids"]
    assert param.default is inspect.Parameter.empty, "★ 该参数必须有默认值就是漏洞"


def test_library_call_with_subset_scope_is_not_green():
    """库调用路径：显式声明「完整仪式是 6 项」而只跑 3 项 ⇒ 不得判绿."""
    subset = vr.selected_items(vr.build_items(), "api-contract,decision-events,golden-recall")
    rec = _Recorder(
        {
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    run = _run(subset, probe=_all_ok, execute=rec, now=lambda: NOW)
    assert not run.is_complete
    assert set(run.uncovered_ids) == {"stack", "drift-runtime", "residue"}
    assert vr.exit_code(run) == 2
    assert not _verdict(vr.format_summary(run)).startswith("✅")


def test_bare_result_list_never_reports_all_green():
    """★★ 传裸结果列表（覆盖范围未知）⇒ **不得**打印 ALL GREEN。

    对抗性复核查出的第二条：`format_summary(run.results)` 丢掉覆盖信息后
    仍会打印 ALL GREEN（`exit_code` 当时是安全的，文案不是）。
    「知道结果但不知道范围」不能推出「全绿」。
    """
    rec = _Recorder(
        {
            "verify-services": (0, OUT_STACK_GREEN),
            "drift_gate": (0, OUT_DRIFT_OK),
            "audit-frontend-residue": (0, OUT_RESIDUE_0),
            "audit-api-contract": (0, OUT_CONTRACT_OK),
            "decision_events": (0, OUT_EVENTS_OK),
            "eval_golden_recall": (0, OUT_RECALL_OK),
        }
    )
    run = _run(probe=_all_ok, execute=rec, now=lambda: NOW)
    assert vr.exit_code(run) == 0, "全仪式真跑全了 ⇒ 0（对照组）"

    # 同一个结果集，但**丢掉覆盖范围**
    bare = list(run.results)
    text = vr.format_summary(bare)
    # 注意：不能直接断言 `"ALL GREEN" not in text` —— 服务栈那一项的**测量值**
    # 就叫 "ALL GREEN"（verify-services.py 自己的输出）。故只判**结论行**。
    assert not _verdict(text).startswith("✅"), "★ 覆盖范围未知时不得有通过结论"
    assert "覆盖范围未知" in text
    assert vr.exit_code(bare) != 0, "★ 未知覆盖范围不得判 0"

    # 对照组：完整 Run 才允许出现通过结论行
    assert _verdict(vr.format_summary(run)).startswith("✅")


def test_unknown_status_never_slips_into_green():
    """★★ 无法识别的 status 不得滑进 0 分支。

    对抗性复核查出的第三条：初版 `exit_code` 只挑 FAIL 与「无法测量」，
    于是**任何别的 status**（将来新增的状态、或自定义判据返回的错值）
    都会掉进最后一行 `return 0` —— 又一个 fail-open。
    """
    full_items = _items()
    ids = [it.item_id for it in full_items]
    results = [
        vr.Result(
            item_id=it.item_id,
            title=it.title,
            mode=it.mode,
            command=it.command,
            status="SOMETHING_NEW",  # 非 PASS/FAIL/无法测量
            measurement="whatever",
            detail="未知状态",
            observed_at=NOW,
        )
        for it in full_items
    ]
    run = vr.Run(results=results, expected_item_ids=tuple(ids))
    assert run.is_complete, "覆盖是完整的（问题只在状态值）"
    assert vr.exit_code(run) != 0, "★ 未知状态绝不能判 0"

    text = vr.format_summary(run)
    assert not _verdict(text).startswith("✅")
    assert "状态无法识别" in text or "状态未知" in text
    # 也不得因为认不出图标就崩掉
    assert "SOMETHING_NEW" in text
