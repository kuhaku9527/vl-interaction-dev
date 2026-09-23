# ruff: noqa: RUF001, RUF002, RUF003
"""Drift Gate —— 「结果文件缺失」的 fail-closed 语义（工单 #159 AC#3 / AC#5）.

★ 为什么这个文件存在
--------------------
评测回归门禁（#159）要求：把两张记分卡（#157 定向轴 / #158 时序轴）的**结构化
结果**接成一道自动拦人线。它的整个价值取决于一件事 —— **结果缺失必须判红**。

而执行器原来的行为恰好相反（主理人实测，不是推测）::

    paths=["doc/research/data/__NEVER_EXISTS__.json"]（该文件故意不存在）, severity=block, mode=closed
    => passed: true, block_failures: 0, RC=0      ← 绿

即 ``_is_all_missing()`` 把所有引用文件都不存在的情形直接读成
``[SKIP] … fail-open（不阻断）``。对「结果文件」而言那不是通过，而是**没测过**；
两者在报告里必须能区分，否则门禁会静默放行一个**根本没跑过**的评测。

本文件钉住新增的逐 check 字段 ``present_when_missing``（``"skip"`` 缺省 /
``"fail"``），并且**同时**钉住它的边界 —— 一个新判红开关最容易犯的两种错是
「该红不红」（漏）与「不该红却红」（假阳性，会让正确的输入被判死，进而诱人
把开关拆掉）：

1. ``"fail"`` + 全缺失 + block + closed ⇒ ``passed is False`` 且 rc==1；
2. **对照**：同一输入改 ``"skip"`` ⇒ ``passed is True`` 且 rc==0（证明起作用的是
   那个字段，而不是碰巧）；
3. 缺省（不写该字段）与 ``"skip"`` 逐字段一致（回归保护）；
4. 非法值 ⇒ rc==2，且输出点名该 check id 与非法值；
5. 部分缺失 ⇒ 既有语义不变（``<missing:path>`` 占位符参与正则）；
6. ``"fail"`` + warn + closed ⇒ rc==0（与既有 severity 语义一致）；
7. ``mode=open`` + ``"fail"`` + 缺失 ⇒ rc==0 但仍 ``passed=False``；
8. ★ **反假阳性**：文件存在且正则匹配 ⇒ ``passed=True``、rc==0。

所有用例用 ``tmp_path`` + ``--repo-root`` 构造临时仓库，**不碰真实 ``logs/``**，
也**不碰** ``config/drift-contract.json``。

Run: python -m pytest scripts/tests/test_drift_gate_missing_result.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drift_gate  # noqa: E402

GATE = SCRIPTS / "drift_gate.py"

#: 契约里引用一个**绝不会存在**的结果文件：它就是「该评测没跑过」的形状。
#: ``known-absent``：本常量**故意**指向一个不存在的路径 —— 那是本文件被测的输入，
#: 不是失效指针。不标这一行，``scripts/doc_health.py`` 的链接检查会把它当真并报 block。
NEVER = "doc/research/data/__NEVER_EXISTS__.json"  # known-absent: 故意不存在，见上

#: 契约里新增字段的**权威合法值集合**（与 drift_gate 常量比对，防止两处漂移）。
EXPECTED_VALID = ("skip", "fail")


# --------------------------------------------------------------------------
# 真契约的接线清单（#159 / t2 `gate-wiring` 落盘，见 config/drift-contract.json）
# --------------------------------------------------------------------------
#
# ★ 为什么这 4 条**允许**写 present_when_missing="fail"
#   （而既有那 11 条**必须**保持缺省 "skip"）
#
# 这 4 条读的是 `doc/research/data/decision_eval_*_card.json` —— 两张记分卡的
# **入库结果产物**。产物不在 ⇒ 该评测**从没跑过**，也就是"没测"。没测不得读成
# 通过，故必须 fail-closed。而且这些产物**入库**（`git ls-files` 认，未被
# .gitignore 覆盖），CI 干净检出里也在 ⇒ 开 fail 不会误红。
#
# 既有那 11 条读的是**仓库内的配置 / 代码**，其中两条读
# `services/scripts/run-windows.env`（`memory-store-port` / `main-context-env`）
# —— 该文件被 `.gitignore` 忽略、CI **不 checkout**。若给它们开 fail，干净检出里
# 会集体判红，门禁被判死然后被人拆掉。故缺省必须保持 fail-open（skip）。
#
# ★ 断言形态：**精确相等**，不是"至少有一条"。用"至少"会让护栏失效 ——
#   那正是本票要修的那种"看起来在守"。
#
# ★ 两份清单与两张产物路径的**单一来源**在 `eval_gate_fixtures.py`（与
#   `test_eval_gate_contract.py` 共用）。它们曾在本文件与那个文件里各写一份，
#   两份拷贝会各自漂移 ⇒ 两套测试对"接线清单是什么"给出不同答案且**都绿**。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gate_fixtures import (  # noqa: E402
    BASELINE_CHECK_IDS,
    EVAL_CHECK_IDS,
    TIMING_ARTIFACT,
)

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def contract_checks() -> list[dict]:
    """读**真契约**的 checks（本文件唯一读 config/drift-contract.json 的地方）."""
    path = REPO_ROOT / "config" / "drift-contract.json"
    return json.loads(path.read_text(encoding="utf-8"))["checks"]


def assert_present_when_missing_wiring(checks: list[dict]) -> None:
    """★ 护栏本体：带该字段的 check **恰好等于**接线清单，其余一律缺省 "skip".

    把断言抽成函数是为了让它**可被负控直接调用** —— 负控若靠改真契约文件来做，
    那个测试就会在有并发写入 / 崩溃残留时留下一个被改过的契约，风险远大于收益。
    这里用"同一函数 + 同一输入形态"来证明它真会红（见
    :func:`test_guard_reds_when_an_unwired_check_is_given_the_field`）。

    Parameters
    ----------
    checks : list[dict]
        契约的 ``checks`` 数组（真契约，或负控里"多了一个字段"的同形副本）。

    Raises
    ------
    AssertionError
        接线清单与真契约不一致，或某条的值不是 ``"fail"``，或既有 check 被加了该字段。
    """
    wired = [c["id"] for c in checks if "present_when_missing" in c]
    assert wired == list(EVAL_CHECK_IDS), (
        f"带 present_when_missing 的 check 与接线清单不符：\n"
        f"  实际={wired}\n  清单={list(EVAL_CHECK_IDS)}\n"
        "多一条少一条都是行为变化：新增要在此登记并说明它读的是不是入库产物；"
        "减少则说明某个评测门禁又变回了 fail-open（缺结果判绿）。"
    )

    # 接线清单里的值必须**是** "fail"，不能是随便什么合法值。
    for check in checks:
        if check["id"] in EVAL_CHECK_IDS:
            assert check["present_when_missing"] == "fail", (
                f"{check['id']} 的 present_when_missing={check['present_when_missing']!r}，"
                "应为 'fail' —— 结果产物缺失即『没测』，不得判绿"
            )

    # 其余（既有）check 一律缺省语义 = "skip"，且**不许**写死成显式 "skip"：
    # 显式写死会把"忘了这个字段"与"刻意选 skip"混为一谈，也会让 diff 噪声变大。
    baseline = [c for c in checks if c["id"] not in EVAL_CHECK_IDS]
    assert [c["id"] for c in baseline] == list(BASELINE_CHECK_IDS), (
        f"既有 check 集合变了：{[c['id'] for c in baseline]}"
    )
    for check in baseline:
        assert "present_when_missing" not in check, (
            f"既有 check {check['id']} 被加了 present_when_missing —— "
            "它们是仓库内配置/代码（其中 run-windows.env 被 .gitignore 忽略、"
            "CI 不 checkout），开 fail 会让 CI 整体变红。"
        )
        assert drift_gate.present_when_missing_of(check) == "skip", (
            f"既有 check {check['id']} 的缺省语义不是 'skip'"
        )


def make_check(**overrides) -> dict:
    """构造一条引用『不存在结果文件』的 check，可按需覆盖字段."""
    check = {
        "id": "eval-axis-missing",
        "decision_ref": "doc/specs/2026-09-23-decision-eval-regression-gate.md D-159",
        "description": "定向轴记分卡必须存在",
        "phase": "static",
        "paths": [NEVER],
        "pattern": r"accuracy",
        "severity": "block",
    }
    check.update(overrides)
    return check


def write_contract(tmp_path: Path, *checks: dict) -> Path:
    """把 check 们写成一份临时契约，返回其路径."""
    path = tmp_path / "contract.json"
    path.write_text(
        json.dumps(
            {"version": 99, "source_of_truth": "test-missing-result", "checks": list(checks)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run_gate(contract: Path, repo_root: Path, *extra: str) -> subprocess.CompletedProcess:
    """以子进程跑真实 CLI（真退出码 + 真 stdout），历史文件写到临时目录."""
    return subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--contract", str(contract),
            "--repo-root", str(repo_root),
            "--history-dir", str(repo_root / "history"),
            *extra,
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def report_of(proc: subprocess.CompletedProcess) -> dict:
    """解析 ``--json`` 报告；解析失败时把 stderr/stdout 一起带进断言信息."""
    assert proc.stdout.strip(), (
        f"没有 stdout 可解析（rc={proc.returncode}）stderr={proc.stderr[:400]!r}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - 只在契约被写坏时触发
        raise AssertionError(
            f"stdout 不是 JSON（rc={proc.returncode}）: {exc}\nstdout={proc.stdout[:400]!r}"
        ) from exc


def assert_no_report(proc: subprocess.CompletedProcess) -> None:
    """断言这次运行**没有**给出业务报告（meta-error 路径的正确形状）.

    ★ 不能简单断言 stdout 为空：既有 meta-error 路径把 ``[META-ERROR]`` 打到
    stdout（``drift_gate_smoke_test.py`` 第 4 例依赖这一点）。故判据是
    「每一行都是 META-ERROR，且没有一行是 JSON」—— 既守住"不给结论"，
    又不误伤既有输出契约。
    """
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        assert "META-ERROR" in line, f"meta-error 下输出了非报错内容（等于给出结论）: {line!r}"
        assert not line.lstrip().startswith("{"), f"meta-error 下输出了 JSON 报告: {line!r}"
    assert "META-ERROR" in proc.stdout + proc.stderr, "meta-error 路径没有任何 META-ERROR 输出"


# --------------------------------------------------------------------------
# 0. 契约常量本身（防止「文档说 skip、代码写 fail」两处漂移）
# --------------------------------------------------------------------------


def test_default_is_skip_not_fail():
    """★ 缺省**必须**是 ``"skip"``.

    ★ 理由很具体：``services/scripts/run-windows.env`` 被 ``.gitignore`` 忽略、
    CI 不 checkout 它。若缺省改成 ``"fail"``，CI 上所有引用该文件的 check 会
    整体变红 —— 门禁被判死，然后被人拆掉。故缺省是安全侧的 fail-open，
    逐 check 显式 opt-in 才 fail-closed。
    """
    assert drift_gate.DEFAULT_PRESENT_WHEN_MISSING == "skip"
    assert tuple(drift_gate.VALID_PRESENT_WHEN_MISSING) == EXPECTED_VALID
    assert "present_when_missing" not in make_check(), "夹具默认不该带该字段"


def test_present_when_missing_of_defaults_to_skip():
    """未写该字段时，取值函数给出 ``"skip"``（键不存在 == 显式 "skip"）."""
    assert drift_gate.present_when_missing_of(make_check()) == "skip"
    assert drift_gate.present_when_missing_of(make_check(present_when_missing="skip")) == "skip"
    assert drift_gate.present_when_missing_of(make_check(present_when_missing="fail")) == "fail"


# --------------------------------------------------------------------------
# 1 & 2. fail-closed 行为 + 对照实验
# --------------------------------------------------------------------------


def test_fail_on_missing_file_reds_the_gate(tmp_path: Path):
    """★ AC#3：``"fail"`` + 全缺失 + block + closed ⇒ ``passed=False`` 且 rc==1.

    ★ 这是本工单的核心断言。之前同一条 check（不带该字段）是 ``passed=True``、
    rc=0 —— 也就是「评测没跑过」被判成了及格。
    """
    contract = write_contract(
        tmp_path, make_check(present_when_missing="fail")
    )
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is False, f"缺结果却报通过: {result['detail']!r}"
    assert proc.returncode == 1, (
        f"block+closed 下缺结果应 rc=1，实得 {proc.returncode}\n{proc.stdout[:400]}"
    )
    assert report["block_failures"] == 1
    assert report["any_block_fail"] is True

    # detail 必须**点名**缺失的文件，并说清「缺结果 = 没测 ≠ 通过」。
    assert NEVER in result["detail"], f"detail 没点名缺失文件: {result['detail']!r}"
    assert "没测" in result["detail"] and "通过" in result["detail"], (
        f"detail 没说清『缺结果 = 没测 ≠ 通过』: {result['detail']!r}"
    )
    assert "[SKIP]" not in result["detail"], (
        "present_when_missing=fail 却仍打 SKIP 措辞 —— 报告会把判红读成跳过"
    )
    # 报告字段名与语义未改：新增字段是**附加**的。
    assert result["present_when_missing"] == "fail"


def test_skip_on_missing_file_stays_fail_open(tmp_path: Path):
    """★ AC 对照：**同一输入**只把字段改成 ``"skip"`` ⇒ ``passed=True``、rc==0.

    ★ 没有这条对照，「判红」可能只是碰巧（例如契约写坏、路径拼错），
    而不是那个新字段在起作用。两条测试的夹具**完全相同**，只有该字段不同。
    """
    contract = write_contract(
        tmp_path, make_check(present_when_missing="skip")
    )
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is True, f"skip 却判红: {result['detail']!r}"
    assert proc.returncode == 0, f"skip 应 rc=0，实得 {proc.returncode}"
    assert report["block_failures"] == 0
    assert report["any_block_fail"] is False
    assert "[SKIP]" in result["detail"], f"skip 路径应打 [SKIP]: {result['detail']!r}"


def test_fail_and_skip_differ_only_by_that_field(tmp_path: Path):
    """★ 对照的**严格**版本：两份契约逐字节只差一个字段值.

    ★ 上面两条用两个 ``tmp_path``，若夹具本身写错（例如某个用例多写了一个键），
    差异就未必来自被测字段。这里显式构造两份契约并断言其差异只有一处。
    """
    fail_check = make_check(present_when_missing="fail")
    skip_check = make_check(present_when_missing="skip")

    differing = {k for k in set(fail_check) | set(skip_check) if fail_check.get(k) != skip_check.get(k)}
    assert differing == {"present_when_missing"}, f"两份夹具不只差一个字段: {differing}"

    fail_dir = tmp_path / "fail_case"
    skip_dir = tmp_path / "skip_case"
    fail_dir.mkdir()
    skip_dir.mkdir()
    fail_proc = run_gate(
        write_contract(fail_dir, fail_check), fail_dir, "--phase", "static", "--mode", "closed", "--json"
    )
    skip_proc = run_gate(
        write_contract(skip_dir, skip_check), skip_dir, "--phase", "static", "--mode", "closed", "--json"
    )

    assert (report_of(fail_proc)["results"][0]["passed"], fail_proc.returncode) == (False, 1)
    assert (report_of(skip_proc)["results"][0]["passed"], skip_proc.returncode) == (True, 0)


# --------------------------------------------------------------------------
# 3. 缺省 == "skip"（回归保护）
# --------------------------------------------------------------------------


def test_omitted_field_behaves_exactly_like_skip(tmp_path: Path):
    """★ AC：不写该字段 ⇒ 与显式 ``"skip"`` **逐字段一致**（既有 11 条不受影响）.

    ★ 既有 11 条 check 一条都没写这个键，故它们全部走这条路径。这里断言
    JSON 报告的 ``results[0]`` 在两种写法下**完全相同** —— 包括新加的
    ``present_when_missing`` 字段（缺省时它必须被归一化成 ``"skip"``，
    而不是 ``None``/缺失，否则下游读报告的人无法区分"看过并决定 skip"
    与"这个字段还没接上"）。
    """
    omitted_dir = tmp_path / "omitted"
    explicit_dir = tmp_path / "explicit"
    omitted_dir.mkdir()
    explicit_dir.mkdir()

    omitted_proc = run_gate(
        write_contract(omitted_dir, make_check()),
        omitted_dir,
        "--phase", "static", "--mode", "closed", "--json",
    )
    explicit_proc = run_gate(
        write_contract(explicit_dir, make_check(present_when_missing="skip")),
        explicit_dir,
        "--phase", "static", "--mode", "closed", "--json",
    )

    assert omitted_proc.returncode == explicit_proc.returncode == 0
    omitted_result = report_of(omitted_proc)["results"][0]
    explicit_result = report_of(explicit_proc)["results"][0]
    assert omitted_result == explicit_result, (
        "缺省与显式 skip 的 result 不一致：\n"
        f"  缺省={omitted_result}\n  显式={explicit_result}"
    )
    assert omitted_result["present_when_missing"] == "skip"


def test_existing_contract_checks_are_all_skipped_by_default():
    """★ 直接对**真契约**取证：接线清单**逐条精确**，其余一律缺省 ``"skip"``.

    ★ 改前（t1 落盘时）断言的是「**一条都没有**写该字段」—— 那在 #159 接线
    （t2）之前是正确的：当时没有任何 check 该 fail-closed，出现一条就说明行为
    被悄悄改了。改后（本票）真契约里**确实有 4 条**评测 check 写了
    ``"fail"``，原来的"一条都没有"于是变成假红。

    ★ 但**不能**因此退化成「至少有一条带 fail」—— 那种松散形式会让护栏完全失效：
    任何人给任意一条既有 check（例如依赖 gitignore 的 ``main-context-env``）加上
    该字段，断言照样通过，而 CI 会在干净检出里整体变红。故改为**精确相等**：
    带该字段的 id 列表必须与 :data:`EVAL_CHECK_IDS` 逐条一致，多一条少一条都判红。

    改前能抓住 / 改后能抓住：
      - 改前：任何 check 被加上该字段（包括**该加的**那 4 条 ⇒ 假红）。
      - 改后：① 未接线的既有 check 被误加该字段（真阳性，见负控
        :func:`test_guard_reds_when_an_unwired_check_is_given_the_field`）；
        ② 该接线的评测 check **没接**（漏接 ⇒ 缺结果又判绿）；
        ③ 评测 check 的值不是 ``"fail"``（例如被改成 ``"skip"``）；
        ④ 既有 check 的 id 集合被增删改名。
    """
    assert_present_when_missing_wiring(contract_checks())


def test_guard_reds_when_an_unwired_check_is_given_the_field():
    """★★ 负控：把护栏**真的推红一次**，证明它在守（不是恒绿的装饰）.

    ★ 本仓的教训：「CI 绿」只证明跑到的断言成立，不证明断言本身有效；守卫必须
    有一条它真抓到过东西的证据。故把负控**固化成测试**，而不是只在本票结论里
    跑一次 —— 后者会随票据关闭而消失。

    ★ 做法：取真契约的 checks，把**未接线**的 ``main-context-env`` 加上
    ``"present_when_missing": "fail"``（这正是要防的那件事），喂给同一个断言函数
    ⇒ 必须 ``AssertionError``，且错误信息点名那条 check。

    ★ 为什么在**内存里**改而不是临时改 ``config/drift-contract.json``：后者是
    t2 的所有物，且一旦测试中途被打断（超时 / Ctrl-C / 崩溃）就会留下一个被改过
    的契约文件 —— 一个测试不该让仓库处于需要人工恢复的状态。
    """
    checks = contract_checks()
    assert_present_when_missing_wiring(checks)  # 先确认基准是绿的，否则下面测的不是负控

    victim = "main-context-env"
    tampered = [dict(c) for c in checks]
    target = next(c for c in tampered if c["id"] == victim)
    assert "present_when_missing" not in target, f"负控选错了对象：{victim} 本来就带该字段"
    target["present_when_missing"] = "fail"

    with pytest.raises(AssertionError) as excinfo:
        assert_present_when_missing_wiring(tampered)
    message = str(excinfo.value)
    assert victim in message, f"判红信息没点名被误加的 check: {message}"


def test_guard_reds_when_an_eval_check_loses_fail_closed():
    """★ 配对负控②：评测 check 的值被改成 ``"skip"`` ⇒ 判红（缺结果又判绿）.

    ★ 上一条防"多"，这一条防"改值" —— 一个把 ``"fail"`` 悄悄改回 ``"skip"`` 的
    改动不会让任何既有测试变红，却会正好退回 #159 要关的那个洞。
    """
    checks = contract_checks()
    tampered = [dict(c) for c in checks]
    target = next(c for c in tampered if c["id"] == EVAL_CHECK_IDS[0])
    target["present_when_missing"] = "skip"

    with pytest.raises(AssertionError) as excinfo:
        assert_present_when_missing_wiring(tampered)
    assert EVAL_CHECK_IDS[0] in str(excinfo.value)


def test_guard_reds_when_an_eval_check_is_dropped_from_the_wiring():
    """★ 配对负控③：评测 check **漏接**（字段被整条删掉）⇒ 判红（清单少一条）.

    ★ 防的是"静默移除"：删一个键不会让任何东西报错，门禁却退回 fail-open。
    """
    checks = contract_checks()
    tampered = [{k: v for k, v in c.items() if k != "present_when_missing"} for c in checks]

    with pytest.raises(AssertionError) as excinfo:
        assert_present_when_missing_wiring(tampered)
    assert "接线清单" in str(excinfo.value)


# --------------------------------------------------------------------------
# 4. 非法值 ⇒ 显式报错 rc==2（不得静默降级成 "skip"）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    ["FALSE", "FAIL", "true", True, False, 1, 0, "", "fail ", " fail", "Skip", None, [], {}],
)
def test_invalid_value_is_a_meta_error(tmp_path: Path, bad_value: object):
    """★ AC：非法值 ⇒ rc==2，输出**点名该 check id 与非法值**，绝不静默降级.

    ★ 静默降级是这里最坏的失败形态：一个 ``"FALSE"``（大写笔误）若被当成
    ``"skip"``，门禁会**看起来在守**却完全没守 —— 与「文件缺失被判绿」是同一个
    缺陷，只是搬到了配置层。故这里连 ``True`` / ``1`` 都拒绝：JSON 里
    ``true`` 与 ``"fail"`` 不是一回事，靠真值性猜意图正是漏判的来源。
    """
    contract = write_contract(tmp_path, make_check(present_when_missing=bad_value))

    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    assert proc.returncode == 2, (
        f"非法值 {bad_value!r} 应 rc=2（meta-error），实得 {proc.returncode}\n"
        f"stdout={proc.stdout[:300]!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "META-ERROR" in combined, combined[:300]
    assert "eval-axis-missing" in combined, f"报错没点名 check id: {combined[:300]}"
    assert repr(bad_value) in combined, f"报错没点名非法值: {combined[:300]}"
    assert "present_when_missing" in combined, f"报错没点名字段名: {combined[:300]}"


def test_invalid_value_does_not_fall_back_to_skip(tmp_path: Path):
    """★ 非法值**不得**退化成一次绿灯运行（rc==2 而非 rc==0/1）.

    ★ 与上一条分开写：上一条钉报错文本，这一条钉「它没有悄悄跑完并给结论」。
    契约写错时，判绿与判红**都不算数** —— 那正是 rc=2 与 rc=0/1 的区别。

    ★ stdout 上**只能**有 META-ERROR 那一行（既有 meta-error 路径就是打到
    stdout 的，smoke test 第 4 例依赖它），**不得**有 JSON 报告 —— 有报告就等于
    给出了业务结论。
    """
    contract = write_contract(tmp_path, make_check(present_when_missing="FALSE"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    assert proc.returncode not in (0, 1), (
        f"非法契约竟然给出了业务结论（rc={proc.returncode}）—— 静默降级了"
    )
    assert_no_report(proc)


def test_invalid_value_on_one_check_reds_the_whole_contract(tmp_path: Path):
    """★ 一条 check 写错 ⇒ 整份契约 rc=2（含合法 check 也不给结论）.

    ★ 理由：同一份报告里如果一半 check 有效、一半被静默跳过，"总共 N 项"这个
    数字本身就是假的。宁可整份 meta-error，也不给一个半真的账。
    """
    good = make_check(id="eval-good")
    bad = make_check(id="eval-bad", present_when_missing="yes")
    contract = write_contract(tmp_path, good, bad)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    assert proc.returncode == 2
    assert "eval-bad" in proc.stdout + proc.stderr
    assert_no_report(proc)


def test_absent_field_is_not_an_error(tmp_path: Path):
    """对照：**不写**该字段是合法的（否则既有 11 条会集体 rc=2）."""
    contract = write_contract(tmp_path, make_check(id="eval-no-field"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    assert proc.returncode == 0, proc.stdout[:300]


# --------------------------------------------------------------------------
# 5. 部分缺失 ⇒ 既有语义不变
# --------------------------------------------------------------------------


def test_partially_missing_files_keep_the_legacy_placeholder_semantics(tmp_path: Path):
    """★ AC：多 path 时**部分**缺失维持既有语义（占位符进内容参与正则）.

    ★ 关键区分：新开关只管「**全都**不存在」。若部分缺失也走 fail 分支，
    范围就悄悄扩大了 —— 那种扩大最容易在复核里被放过，因为它看起来"更安全"。
    这里把一个存在的文件与一个不存在的文件配对，断言：
    ``<missing:...>`` 占位符**照旧参与正则**，因此 ``pattern="<missing:"`` 能匹配
    并**通过**（rc=0）—— 即部分缺失**没有**被新逻辑改判。
    """
    (tmp_path / "present.txt").write_text("hello world\n", encoding="utf-8")
    check = make_check(
        paths=["present.txt", "absent.txt"],
        pattern=r"<missing:absent\.txt>",
        present_when_missing="fail",  # ← 即便显式要求 fail，部分缺失也不走那条路
    )
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is True, (
        f"部分缺失被新开关改判了（应维持既有的占位符语义）: {result['detail']!r}"
    )
    assert proc.returncode == 0
    assert "[SKIP]" not in result["detail"], "部分缺失不该打成 SKIP（那是全缺失的分支）"


def test_partially_missing_still_reds_on_a_real_mismatch(tmp_path: Path):
    """★ 部分缺失 + 正则**不**匹配 ⇒ 照旧判红（既有语义的另一半）.

    ★ 与上一条配对：缺的那个文件不能变成"免罪符"。占位符进了内容、正则没匹上，
    就该按普通不符处理（block+closed ⇒ rc=1）。
    """
    (tmp_path / "present.txt").write_text("hello world\n", encoding="utf-8")
    check = make_check(
        paths=["present.txt", "absent.txt"],
        pattern=r"__NEVER_MATCHES_xyz__",
        present_when_missing="fail",
    )
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    assert report["results"][0]["passed"] is False
    assert proc.returncode == 1


# --------------------------------------------------------------------------
# 6 & 7. severity / mode 语义不变
# --------------------------------------------------------------------------


def test_fail_with_warn_severity_does_not_block(tmp_path: Path):
    """★ AC：``"fail"`` + ``severity=warn`` + closed ⇒ rc==0，但仍报 ``passed=False``.

    ★ 新字段只决定「文件缺失算不算不符」，**不**决定「不符算不算阻断」——
    后者一直由 severity + mode 决定。若这里让 rc=1，就等于给这个字段偷偷加了
    第二个开关，既有 severity 语义被绕过。
    """
    contract = write_contract(tmp_path, make_check(severity="warn", present_when_missing="fail"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is False, f"warn 也该报 passed=False: {result['detail']!r}"
    assert proc.returncode == 0, f"severity=warn 不得阻断，实得 rc={proc.returncode}"
    assert report["block_failures"] == 0
    assert report["warn_failures"] == 1
    assert report["any_block_fail"] is False
    assert "[WARN]" in result["detail"]


def test_fail_in_open_mode_does_not_block_but_still_reports_false(tmp_path: Path):
    """★ AC：``mode=open`` + ``"fail"`` + 缺失 ⇒ rc==0，但 ``passed=False``.

    ★ 「不阻断」与「没通过」是两件事。open 模式下 rc 恒为 0 是既有退出码契约
    （不许改），可报告里必须留下 ``passed=False`` —— 否则 open 模式会变成
    又一个"绿即通过"的错觉来源。
    """
    contract = write_contract(tmp_path, make_check(present_when_missing="fail"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "open", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is False, f"open 模式仍须报 passed=False: {result['detail']!r}"
    assert proc.returncode == 0, f"open 模式 rc 必须为 0，实得 {proc.returncode}"
    assert report["block_failures"] == 1, "block_failures 是『不符几条』，与 mode 无关"
    assert report["any_block_fail"] is False, (
        "any_block_fail 的既有语义含 mode 条件（open 下恒 False），不得改"
    )


def test_open_mode_fail_does_not_write_a_nonzero_exit_even_with_history(tmp_path: Path):
    """★ 同上但**带 history 写入**（默认路径）：确认 rc 仍为 0.

    ★ 分开写是因为 history 分支在 rc 计算**之后**才跑；若有人把 rc 计算挪到
    history 写法里，open 模式的退出码契约就会破。
    """
    contract = write_contract(tmp_path, make_check(present_when_missing="fail"))
    proc = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--contract", str(contract),
            "--repo-root", str(tmp_path),
            "--phase", "static",
            "--mode", "open",
            "--json",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"open 模式带 history 时 rc={proc.returncode}"
    history = sorted((tmp_path / "logs" / "drift-gate-history").glob("*.json"))
    assert history, "history 文件没写出来（本用例依赖它存在才能证明 rc 未被影响）"
    assert json.loads(history[0].read_text(encoding="utf-8"))["results"][0]["passed"] is False


# --------------------------------------------------------------------------
# 8. ★ 反假阳性：正常情况不得被这个守卫误伤
# --------------------------------------------------------------------------


def test_present_file_with_matching_pattern_still_passes(tmp_path: Path):
    """★★ AC：文件**存在**且正则匹配 ⇒ ``passed=True``、rc==0，即使开了 ``"fail"``.

    ★ 这是最重要的**反假阳性**断言。一个"结果缺失就判红"的守卫，若在结果
    **存在**时也判红，就会把每一次正常运行都判死；而人会因此把这个开关拆掉，
    于是真正的缺陷（缺结果判绿）就回来了。故这里必须证明：开关只在"全缺失"时发力。
    """
    (tmp_path / "scorecard.json").write_text(
        json.dumps({"axis": "directed", "accuracy": 0.93}), encoding="utf-8"
    )
    check = make_check(
        paths=["scorecard.json"],
        pattern=r'"accuracy":\s*0\.93',
        present_when_missing="fail",
    )
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    result = report["results"][0]
    assert result["passed"] is True, f"结果存在且匹配却判红: {result['detail']!r}"
    assert proc.returncode == 0, f"应 rc=0，实得 {proc.returncode}"
    assert "[OK]" in result["detail"]
    assert report["block_failures"] == 0


def test_present_file_with_a_real_drift_still_fails(tmp_path: Path):
    """★ 配对：文件存在但**真的不符** ⇒ 照旧判红（不是"存在即通过"）.

    ★ 与上一条合起来才说明守卫是"两段式"：先判存在性（缺失走新分支），
    存在则回到原来的正则判定。少了这一条，"存在即绿"的假阴性会被漏掉。
    """
    (tmp_path / "scorecard.json").write_text(
        json.dumps({"axis": "directed", "accuracy": 0.41}), encoding="utf-8"
    )
    check = make_check(
        paths=["scorecard.json"],
        pattern=r'"accuracy":\s*0\.93',
        present_when_missing="fail",
    )
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")

    assert report_of(proc)["results"][0]["passed"] is False
    assert proc.returncode == 1


def test_empty_but_existing_file_is_not_missing(tmp_path: Path):
    """★ 边界：**存在但为空**的文件不算缺失（判据是文件读过，不是内容非空）.

    ★ 这条防的是一个很自然的误实现："内容为空 ⇒ 判缺失 ⇒ 判红"。空文件是
    **产出过的**结果（评测跑了但记分卡为空 —— 那是不符，该走正则判定与
    既有 [BLOCK] 文案），不是"没产出"。两者对应不同的处置。
    """
    (tmp_path / "empty.json").write_text("", encoding="utf-8")
    check = make_check(
        paths=["empty.json"],
        pattern=r'"accuracy"',
        present_when_missing="fail",
    )
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False
    assert proc.returncode == 1
    assert "全部缺失" not in result["detail"], (
        f"空文件被误判成『缺失』: {result['detail']!r}"
    )
    assert "[SKIP]" not in result["detail"]


def test_empty_paths_with_fail_is_not_vacuously_green(tmp_path: Path):
    """★ 边界：``paths: []`` + ``"fail"`` ⇒ 判红，而不是"没有文件所以没有缺失".

    ★ 这条防的是"真空真"（vacuous truth）：一条没配置任何路径的 check 若被判绿，
    门禁会多出一条**永远及格**的条目 —— 而那正是"缺结果读成通过"的另一张脸
    （这里缺的是配置本身）。detail 会写明契约没列出 paths。
    """
    contract = write_contract(tmp_path, make_check(paths=[], present_when_missing="fail"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, f"空 paths 被真空判绿: {result['detail']!r}"
    assert proc.returncode == 1
    assert "paths" in result["detail"]


def test_unreadable_path_counts_as_missing(tmp_path: Path):
    """★ 读不了的文件（此处用目录）与不存在同等对待 ⇒ ``"fail"`` 下判红并点名.

    ★ 判据是"有没有拿到内容"，不是"路径在不在"。一个读不出来的结果文件对门禁
    而言和不存在没有区别 —— 都不能拿来当"检查过了"。
    """
    (tmp_path / "results").mkdir()  # 目录：read_text() 会抛 OSError
    check = make_check(paths=["results"], present_when_missing="fail")
    contract = write_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    detail = report_of(proc)["results"][0]["detail"]

    assert proc.returncode == 1
    assert "unreadable: results" in detail, f"detail 没点名读不了的路径: {detail!r}"


# --------------------------------------------------------------------------
# 9. 报告契约：既有字段名 / 语义零改动
# --------------------------------------------------------------------------


def test_json_report_keeps_every_preexisting_field(tmp_path: Path):
    """★ AC：``--json`` 报告字段名与语义零改动，只允许**新增**.

    ★ ``drift_gate_smoke_test.py`` 与 CI 都读这些字段；改名字或改语义（例如
    ``block_failures`` 不再只是"block 且不符"）会让它们静默失准。
    """
    contract = write_contract(
        tmp_path,
        make_check(id="eval-block", present_when_missing="fail"),
        make_check(id="eval-warn", severity="warn", present_when_missing="fail"),
    )
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    for key in (
        "ran_at", "phase", "mode", "source_of_truth", "contract_version",
        "total_checks", "block_failures", "warn_failures", "results", "any_block_fail",
    ):
        assert key in report, f"既有报告字段 {key!r} 丢了"
    for key in (
        "id", "phase", "severity", "decision_ref", "description",
        "expected_regex", "paths", "passed", "detail",
    ):
        assert key in report["results"][0], f"既有 result 字段 {key!r} 丢了"

    assert report["total_checks"] == 2
    assert report["block_failures"] == 1, "block_failures 语义变了（应为 block 且不符）"
    assert report["warn_failures"] == 1
    assert report["any_block_fail"] is True
    assert [r["passed"] for r in report["results"]] == [False, False]


def test_present_when_missing_is_added_to_every_result(tmp_path: Path):
    """新增字段出现在**每个** result 里（读数的人不必再回契约文件对照）."""
    contract = write_contract(
        tmp_path,
        make_check(id="eval-default"),
        make_check(id="eval-fail", present_when_missing="fail"),
    )
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    assert [r["present_when_missing"] for r in report["results"]] == ["skip", "fail"]


# --------------------------------------------------------------------------
# 10. 单元层：evaluate() 的分支（不依赖子进程，便于定位失败）
# --------------------------------------------------------------------------


def test_evaluate_unit_level_fail_branch():
    """单元层直接钉 ``evaluate()`` 的两条分支与措辞."""
    check = make_check(present_when_missing="fail")
    output = f"<missing:{NEVER}>"

    passed, detail = drift_gate.evaluate(check, output, "closed", REPO_ROOT)
    assert passed is False
    assert "[BLOCK]" in detail
    assert NEVER in detail

    passed_open, detail_open = drift_gate.evaluate(check, output, "open", REPO_ROOT)
    assert passed_open is False
    assert "[WARN]" in detail_open, "open 模式下措辞不该是 [BLOCK]（不阻断）"

    skip_check = make_check(present_when_missing="skip")
    passed_skip, detail_skip = drift_gate.evaluate(skip_check, output, "closed", REPO_ROOT)
    assert passed_skip is True
    assert "[SKIP]" in detail_skip


def test_evaluate_unit_level_rejects_an_invalid_value_it_was_handed():
    """★ 绕过 ``load_contract`` 直接喂非法值 ⇒ 抛错，不静默降级.

    ★ 这是第二道防线：CLI 路径已在 rc=2 挡住，但库调用方（例如后续把评测结果
    直接喂进 ``evaluate`` 的代码）不能被静默放行。
    """
    check = make_check(present_when_missing="FALSE")
    with pytest.raises(ValueError, match="present_when_missing"):
        drift_gate.evaluate(check, f"<missing:{NEVER}>", "closed", REPO_ROOT)


# ==========================================================================
# 11. `parse_as`：结构化产物"文件在但内容不是合法 JSON" ⇒ 判红（t10 / W1）
# ==========================================================================
#
# ★ 为什么需要这一层（第三轮对抗复核 t9 查出的 W1，主理人已独立二分定位）
#
# 时序卡 31467 B，截断到 16800 B：
#     截到 16761 B ⇒ 门禁 rc=1（红）
#     截到 16800 B ⇒ 门禁 rc=0（绿）   ★ 而该前缀根本不是合法 JSON
#     20000 / 25206 / 31400 B ⇒ 同样 rc=0
#
# 根因是**两个设计的交集**（新锚点带来的，不是历史遗留）：
#   1. `_is_all_missing()` 只判「路径全都缺失/不可读」⇒ 文件一存在就不进
#      fail-closed 分支；
#   2. 4 条 eval pattern 的**全部观测点都落在文件前段**（最靠后的在前 ~13 KB），
#      ⇒ 该文件后面一大半从来没有任何断言读过。
# 半截文件因此能"命中"全部 pattern 并判绿。
#
# ★ 与 `present_when_missing` 的分工（两层并列，互不覆盖）：
#     present_when_missing 管「文件**在不在**」；
#     parse_as             管「文件在、但内容**是不是一份完整卡片**」。
#   两者都只决定 passed，是否**阻断**依旧只由 severity + mode 决定。
#
# ★ 为什么这些测试用合成夹具而不是只打真实卡：合成夹具能精确控制"合法 vs 非法"
#   这一个变量；真实卡的截断用例（见 :func:`test_truncated_committed_timing_card_reds`）
#   作为端到端复核补在下面，且用 `tmp_path` 副本，**不碰入库产物**。
#
# 入库的两张记分卡路径（`DIRECTED_ARTIFACT` / `TIMING_ARTIFACT`）来自本文件顶部
# 从 `eval_gate_fixtures` 的 import —— 端到端截断用例读它们，但只读、不改。


def make_json_contract(repo: Path, *checks: dict) -> Path:
    """把 check 写进 ``repo`` 下的临时契约（``--repo-root`` 也指向 ``repo``）."""
    path = repo / "contract.json"
    path.write_text(
        json.dumps(
            {"version": 99, "source_of_truth": "test-parse-as", "checks": list(checks)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def eval_check(**overrides) -> dict:
    """一条"读结构化产物"的 check（默认**不写** parse_as，以隔离被测变量）."""
    check = {
        "id": "eval-structured-card",
        "decision_ref": "doc/specs/2026-09-23-decision-eval-regression-gate.md D-159",
        "description": "记分卡必须是一份完整可解析的结果产物",
        "phase": "static",
        "paths": ["card.json"],
        "pattern": r'"verdict":\s*"pass"',
        "severity": "block",
        "present_when_missing": "fail",
    }
    check.update(overrides)
    return check


def test_parse_as_defaults_to_none():
    """★ 缺省必须是 ``None``（不做解析）—— 与 ``present_when_missing`` 缺省 skip 同理.

    ★ 既有 11 条 check 读的是仓库内配置/代码（`run-windows.env` / `.py` / `.yml`），
    全都**不是** JSON。缺省若要求解析，CI 会整体变红 ⇒ 门禁被判死然后被人拆掉。
    """
    assert drift_gate.DEFAULT_PARSE_AS is None
    assert tuple(drift_gate.VALID_PARSE_AS) == ("json",)
    assert "parse_as" not in eval_check(), "夹具默认不该带该字段"

    assert drift_gate.parse_as_of(eval_check()) is None
    assert drift_gate.parse_as_of(eval_check(parse_as=None)) is None, (
        "显式 null 在本字段是**合法**的（等于缺省）—— 与 present_when_missing 刻意不同"
    )
    assert drift_gate.parse_as_of(eval_check(parse_as="json")) == "json"


def test_invalid_json_content_reds_the_gate(tmp_path: Path):
    """★ AC ①：``parse_as="json"`` + 文件**存在**但非法 JSON ⇒ passed=False 且 rc=1.

    ★ 这是 W1 的最小复现形态：文件在（所以 `_is_all_missing` 为 False、
    `present_when_missing` 不发力），内容却是半截 —— 旧设计在这个组合下判绿。
    """
    (tmp_path / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
    contract = make_json_contract(tmp_path, eval_check(parse_as="json"))

    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)
    result = report["results"][0]

    assert result["passed"] is False, f"半截产物被判绿: {result['detail']!r}"
    assert proc.returncode == 1, f"block+closed 下应 rc=1，实得 {proc.returncode}"
    assert result["parse_as"] == "json"
    assert "card.json" in result["detail"], f"detail 没点名坏文件: {result['detail']!r}"
    assert "视同没测" in result["detail"], (
        f"detail 没说清『文件在但内容不是完整卡片 ⇒ 视同没测』: {result['detail']!r}"
    )
    assert "[SKIP]" not in result["detail"], "parse 失败却打 SKIP 措辞"


def test_control_without_the_field_stays_green(tmp_path: Path):
    """★★ AC ②**对照**：**同一输入**不写 parse_as（缺省 null）⇒ rc=0.

    ★ 没有这条对照，「判红」可能只是碰巧（夹具写错 / pattern 不匹配），
    而不是那个新字段在起作用。两个用例的输入**逐字节相同**，只差这一个键。
    """
    (tmp_path / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
    contract = make_json_contract(tmp_path, eval_check())  # ← 不写 parse_as

    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, (
        f"缺省 null 时不该管内容是否合法 JSON（否则既有 11 条会全红）: {result['detail']!r}"
    )
    assert proc.returncode == 0
    assert result["parse_as"] is None


def test_parse_as_and_default_differ_only_by_that_field(tmp_path: Path):
    """★ 对照的**严格**版本：两份夹具逐字段只差 ``parse_as``."""
    strict = eval_check(parse_as="json")
    lax = eval_check()
    differing = {k for k in set(strict) | set(lax) if strict.get(k) != lax.get(k)}
    assert differing == {"parse_as"}, f"两份夹具不只差一个字段: {differing}"

    def run(check: dict) -> tuple[bool, int]:
        repo = tmp_path / f"case_{uuid.uuid4().hex[:8]}"
        repo.mkdir()
        (repo / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
        proc = run_gate(
            make_json_contract(repo, check), repo,
            "--phase", "static", "--mode", "closed", "--json",
        )
        return report_of(proc)["results"][0]["passed"], proc.returncode

    assert run(strict) == (False, 1)
    assert run(lax) == (True, 0)


def test_truncated_committed_timing_card_reds(tmp_path: Path):
    """★★ AC ③ 端到端：**真实入库时序卡**截断到 16800 B ⇒ 判红.

    ★ 这是主理人二分定位出的**最小反例**，改动前是 rc=0（绿）。用 ``tmp_path``
    副本复现，**不改入库产物**（改完还要核 md5）。

    ★ 断言里同时确认那个前缀**确实**不是合法 JSON —— 否则这条测试可能在测
    别的东西（例如它其实仍然可解析，只是 pattern 不匹配）。
    """
    repo = tmp_path / "repo"
    (repo / "doc" / "research" / "data").mkdir(parents=True)
    src = REPO_ROOT / TIMING_ARTIFACT
    truncated = src.read_bytes()[:16800]
    (repo / TIMING_ARTIFACT).write_bytes(truncated)

    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated.decode("utf-8", errors="replace"))

    check = eval_check(
        id="eval-timing-truncated",
        paths=[TIMING_ARTIFACT],
        pattern=r'"kind":\s*"timing-axis-scorecard"',
        parse_as="json",
    )
    contract = make_json_contract(repo, check)
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, f"半截时序卡被判绿: {result['detail']!r}"
    assert proc.returncode == 1
    assert TIMING_ARTIFACT in result["detail"]


def test_healthy_structured_artifact_stays_green(tmp_path: Path):
    """★ AC ⑤ **反假阳性**：合法 JSON 且内容正常 ⇒ passed=True、rc=0.

    ★ 这是最重要的反向断言。一个"内容不可解析就判红"的守卫若在正常产物上也判红，
    会把每次正常运行判死，人会因此拆掉它 —— 于是真正的洞（半截产物判绿）回来。
    """
    repo = tmp_path / "repo"
    (repo / "doc" / "research" / "data").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / TIMING_ARTIFACT, repo / TIMING_ARTIFACT)

    check = eval_check(
        id="eval-timing-healthy",
        paths=[TIMING_ARTIFACT],
        pattern=r'"kind":\s*"timing-axis-scorecard"',
        parse_as="json",
    )
    contract = make_json_contract(repo, check)
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, f"健康的完整产物被误判: {result['detail']!r}"
    assert proc.returncode == 0
    assert "[OK]" in result["detail"]


def test_multi_path_any_unparsable_file_reds(tmp_path: Path):
    """★ 多 ``paths``：**任一个**声明为 json 的文件不可解析 ⇒ 判红（另一个合法也不行）.

    ★ 这条钉住"逐文件解析"这个设计选择：宽松成"全部文件都坏才红"会让一条坏文件
    被另一条好文件掩盖 —— 那正是本条要防的。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "good.json").write_text('{"verdict": "pass"}', encoding="utf-8")
    (repo / "bad.json").write_text('{"verdict": "pass"', encoding="utf-8")  # 缺右括号

    check = eval_check(paths=["good.json", "bad.json"], parse_as="json")
    contract = make_json_contract(repo, check)
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, f"好文件掩盖了坏文件: {result['detail']!r}"
    assert proc.returncode == 1
    assert "bad.json" in result["detail"], "没点名坏文件"
    assert "good.json" not in result["detail"].split("paths:")[-1], (
        f"把合法文件也报成坏的: {result['detail']!r}"
    )


def test_empty_file_with_parse_as_json_reds(tmp_path: Path):
    """★ AC：**空文件**在 ``parse_as="json"`` 下判红.

    ★ 空文件是"生成刚开始就被杀"的极端形态；它**不是**合法 JSON。注意这与
    `present_when_missing` 的边界不同：空文件**存在**，故那条路径不管它 ——
    这正是两层必须并列的原因。
    """
    (tmp_path / "card.json").write_text("", encoding="utf-8")
    contract = make_json_contract(tmp_path, eval_check(parse_as="json"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, f"空文件在 parse_as=json 下应判红: {result['detail']!r}"
    assert proc.returncode == 1


@pytest.mark.parametrize("shape", ["nested_array", "nested_object", "open_string", "noise"])
def test_pathological_content_reds_instead_of_crashing(tmp_path: Path, shape: str):
    """★ **反崩溃**：病态内容必须**判红**，不得让门禁抛未捕获异常.

    ★ 这条是实测踩出来的：极深嵌套（`[[[[…`）会让 CPython 的 JSON 解码器爆栈，
    抛的是 ``RecursionError`` —— **不是** ``JSONDecodeError``。只接后者的话异常会
    穿透到 ``main()``，门禁整个 crash，CI 拿到的是 traceback 而**不是一份报告**。

    ★ 为什么"崩溃"比"判红"更坏：判红至少留下 JSON/文本报告，下游能读、能定位；
    崩溃会让下游**什么都读不到**，而 ``drift-gate`` job 独立跑时那是唯一的产出。

    ★ 载荷在函数内构造（不放进 ``parametrize``）：几 MB 的字面量会变成巨型
    test id，实测会让 pytest 在 teardown 撞上
    ``ValueError: the environment variable is longer than 32767 characters``
    —— 那是测试基建的问题，不该混进这条断言的语义里。
    """
    payloads = {
        "nested_array": b"[" * 200_000,                      # 深嵌套
        "nested_object": b"{" * 100_000,                     # 深嵌套
        "open_string": b'"' + b"a" * 1_000_000,              # 未闭合字符串
        "noise": bytes((i * 7919) % 256 for i in range(200_000)),  # 二进制噪声
    }
    (tmp_path / "card.json").write_bytes(payloads[shape])
    contract = make_json_contract(tmp_path, eval_check(parse_as="json"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")

    assert "Traceback" not in proc.stderr, f"{shape}: 门禁崩溃了\n{proc.stderr[:400]}"
    assert "RecursionError" not in proc.stderr, (
        f"{shape}: RecursionError 穿透了\n{proc.stderr[:400]}"
    )
    assert proc.returncode == 1, f"{shape}: 应 rc=1（判红），实得 {proc.returncode}"
    assert report_of(proc)["results"][0]["passed"] is False


def test_parse_as_open_mode_does_not_block_but_reports_false(tmp_path: Path):
    """★ 与既有退出码契约一致：``mode=open`` ⇒ rc=0，但仍报 ``passed=False``.

    ★ 「不阻断」与「没通过」是两件事；open 模式 rc 恒 0 是既有契约（不许改），
    但报告里必须留下 passed=False。
    """
    (tmp_path / "card.json").write_text("{not json", encoding="utf-8")
    contract = make_json_contract(tmp_path, eval_check(parse_as="json"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "open", "--json")
    report = report_of(proc)

    assert report["results"][0]["passed"] is False
    assert proc.returncode == 0


def test_parse_as_warn_severity_does_not_block(tmp_path: Path):
    """★ 与既有 severity 语义一致：``severity=warn`` + closed ⇒ rc=0，但 passed=False."""
    (tmp_path / "card.json").write_text("{not json", encoding="utf-8")
    contract = make_json_contract(
        tmp_path, eval_check(parse_as="json", severity="warn")
    )
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)

    assert report["results"][0]["passed"] is False
    assert proc.returncode == 0
    assert report["block_failures"] == 0
    assert report["warn_failures"] == 1


def test_parse_as_does_not_override_present_when_missing(tmp_path: Path):
    """★ 两层分工：文件**缺失**时仍走 ``present_when_missing`` 的既有分支，措辞不变.

    ★ 本改动是"在它之上加一层"，不是替换它。缺失场景的 detail 仍须是
    「引用的结果文件全部缺失」那句，而不是被 parse 层抢走。
    """
    check = eval_check(paths=["never.json"], parse_as="json", present_when_missing="fail")
    contract = make_json_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is False
    assert proc.returncode == 1
    assert "引用的结果文件全部缺失" in result["detail"], (
        f"缺失场景被 parse 层抢走了（应仍由 present_when_missing 处置）: {result['detail']!r}"
    )


@pytest.mark.parametrize(
    "bad_value",
    ["JSON", "Json", "json ", " json", "json\n", True, False, 1, 0, "", "yaml", "null", [], {}],
)
def test_invalid_parse_as_is_a_meta_error(tmp_path: Path, bad_value: object):
    """★ AC ④：非法 ``parse_as`` ⇒ rc==2，**点名 check id 与非法值**，绝不静默降级.

    ★ 静默降级是这里最坏的形态：``"JSON"``（大写笔误）若被当成 ``None``，
    门禁会**看起来声明了要校验、实际完全没校验** —— 比不声明更坏，因为它
    会让复核者以为这条 check 已经覆盖了解析层。

    ★ 与 ``present_when_missing`` 的差别：那里的 ``null`` 是非法值，这里的
    ``null`` 合法（等于缺省）。所以 ``"null"``（**字符串**）在这里仍必须判红 ——
    它不是 JSON 的 null，而是一个拼错的字符串。
    """
    contract = make_json_contract(tmp_path, eval_check(parse_as=bad_value))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")

    assert proc.returncode == 2, (
        f"非法值 {bad_value!r} 应 rc=2（meta-error），实得 {proc.returncode}\n"
        f"stdout={proc.stdout[:300]!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "META-ERROR" in combined, combined[:300]
    assert "eval-structured-card" in combined, f"报错没点名 check id: {combined[:300]}"
    assert repr(bad_value) in combined, f"报错没点名非法值: {combined[:300]}"
    assert "parse_as" in combined, f"报错没点名字段名: {combined[:300]}"
    assert_no_report(proc)


def test_invalid_parse_as_does_not_fall_back_to_no_parsing(tmp_path: Path):
    """★ 非法值**不得**退化成"不解析然后判绿"（rc==2 而非 rc==0/1）.

    ★ 用一个**非法 JSON 的产物**当输入：若实现静默降级成 None，pattern 仍会命中
    并给出 rc=0 的假绿。故这里断言 rc=2 且没有任何报告输出。
    """
    (tmp_path / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
    contract = make_json_contract(tmp_path, eval_check(parse_as="JSON"))
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")

    assert proc.returncode == 2, f"非法契约竟然给出业务结论（rc={proc.returncode}）"
    assert_no_report(proc)


def test_evaluate_unit_level_rejects_an_invalid_parse_as():
    """★ 第二道防线：绕过 ``load_contract`` 直接喂非法值 ⇒ ``ValueError``."""
    check = eval_check(parse_as="JSON")
    with pytest.raises(ValueError, match="parse_as"):
        drift_gate.evaluate(check, "--- card.json ---\n{}", "closed", REPO_ROOT)


def test_baseline_checks_are_unaffected_by_parse_as(tmp_path: Path):
    """★ AC ⑥：**未声明** ``parse_as`` 的 check 行为零变更（既有 11 条的保护）.

    ★ 既有 check 读的 `run-windows.env` / `.py` / `.yml` 都不是 JSON。这里拿一份
    **非 JSON 的内容**喂给一条不带该字段的 check，断言它照旧按 pattern 判定
    （命中即通过）—— 即解析层没有"顺手"作用到它们身上。
    """
    (tmp_path / "env.txt").write_text("MAIN_CONTEXT=16384\n", encoding="utf-8")
    check = eval_check(
        id="baseline-like",
        paths=["env.txt"],
        pattern=r"^MAIN_CONTEXT=16384",
        present_when_missing="skip",
    )
    contract = make_json_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, (
        f"非 JSON 的既有形态 check 被解析层误伤: {result['detail']!r}"
    )
    assert proc.returncode == 0
    assert result["parse_as"] is None


def test_real_contract_declares_parse_as_on_exactly_the_eval_checks():
    """★ AC：真契约里 **4 条 eval-\\*** 声明 ``parse_as="json"``，其余一条都没有.

    ★ 断言**精确相等**而不是"至少有一条" —— 后者会让"顺手给既有 check 也加上"
    这种改动静默通过，而那会把 CI 全红（既有 check 引用的文件不是 JSON）。
    """
    checks = contract_checks()  # 真契约
    with_field = [c["id"] for c in checks if "parse_as" in c]
    assert with_field == list(EVAL_CHECK_IDS), (
        f"声明 parse_as 的 check 与预期不符：\n  实际={with_field}\n  预期={list(EVAL_CHECK_IDS)}"
    )
    for check in checks:
        if check["id"] in EVAL_CHECK_IDS:
            assert check["parse_as"] == "json", f"{check['id']} 的值应为 'json'"
        else:
            assert "parse_as" not in check, (
                f"既有 check {check['id']} 被加了 parse_as —— 它引用的不是 JSON，会误红"
            )
            assert drift_gate.parse_as_of(check) is None


def test_real_contract_reported_parse_as_matches_contract(tmp_path: Path):
    """★ ``--json`` 报告里的 ``parse_as`` 必须与契约逐条一致（新字段被正确带出）."""
    proc = run_gate(
        REPO_ROOT / "config" / "drift-contract.json", REPO_ROOT,
        "--phase", "static", "--mode", "closed", "--json", "--no-history",
    )
    report = report_of(proc)
    declared = {
        c["id"]: c.get("parse_as")
        for c in contract_checks()
    }
    for result in report["results"]:
        assert result["parse_as"] == declared[result["id"]], (
            f"{result['id']} 报告里的 parse_as 与契约不符"
        )
    assert {r["parse_as"] for r in report["results"] if r["id"].startswith("eval-")} == {"json"}


# ==========================================================================
# 12. W3：`_is_all_missing` 内容嗅探的**端到端**取证（t9 报缺端到端测试）
# ==========================================================================
#
# ★ 已知语义（不是缺陷，是设计）：`_is_all_missing()` 判的是
#   `"--- " not in output` —— 一个**内容嗅探**，而非路径存在性检查。
#   因此当**路径字面本身**含 `--- ` 时（例如文件名叫 `--- trap ---.json`），
#   `<missing:--- trap ---.json>` 占位符里就带了 `--- `，嗅探会误判成"读到了"。
#
# t9 实探 7 种构形**未能构成绕过**（t8 的"未构成绕过"声明成立），但缺端到端测试锁住。
# 本节的测试就是那条端到端取证：它不改执行器（嗅探逻辑属 t1 交付、已多轮复核），
# 而是把"该形态下门禁**仍然判红**"钉成可执行断言 —— 一旦有人改动嗅探或 pattern
# 语义，这里会立刻发现它变绿。
#
# ⚠️ 如实登记（不声称已修）：`_is_all_missing` 仍是内容嗅探。真实入库产物的路径
#    `doc/research/data/decision_eval_*.json` 不含 `--- `，故对**实际接线**没有影响；
#   但这是一条**依赖路径字面形状**的隐式契约，若要彻底消除应改为按 `paths` 逐条
#   检查文件存在性（那会动 t1 已交付并复核过的语义，故不在本票范围）。


def test_content_sniffing_trap_still_reds_end_to_end(tmp_path: Path):
    """★★ W3 端到端：路径含 `--- ` 的"嗅探陷阱"下，门禁**仍然判红**（不是判绿）.

    ★ 这条替代/补齐 t8 的单元层断言，理由是**端到端**才覆盖 `run_all` →
    `run_check_files` → `evaluate` 的完整链路：单元层直接喂字符串，绕过了
    `run_check_files` 真正生成占位符的那一步 —— 而陷阱恰恰出在那一步的产物上。

    ★ 为什么要 `parse_as="json"` 的**第二层**也一起钉：W1 修好之后，这条陷阱
    多了一道保险（占位符内容不是合法 JSON）。但本测试**不依赖** parse_as 才是
    关键 —— 它同时断言 `present_when_missing="fail"` 单层下也判红，证明护栏不是
    靠新字段才成立的。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    trap_rel = "--- trap ---.json"  # known-absent: 本测试故意不创建它
    check = eval_check(
        id="eval-sniff-trap",
        paths=[trap_rel],
        pattern=r'"verdict":\s*"pass"',
        present_when_missing="fail",
    )
    contract = make_json_contract(repo, check)
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")
    report = report_of(proc)
    result = report["results"][0]

    # (1) 端到端：文件确实不存在，门禁必须判红 —— 陷阱不得让它变绿。
    assert not (repo / trap_rel).exists()
    assert result["passed"] is False, (
        f"嗅探陷阱下门禁判绿了（W3 绕过真的成立）: {result['detail']!r}"
    )
    assert proc.returncode == 1, f"应 rc=1，实得 {proc.returncode}"


def test_content_sniffing_trap_plus_lax_pattern_is_the_documented_risk(tmp_path: Path):
    """★ W3 的**残余风险**如实取证：陷阱 + 宽 pattern 才会真的变绿（记录现状）.

    ★ 这条**不是**要求修复，而是把"什么情况下陷阱会变成真绕过"钉下来：
    只有当 pattern 宽到能匹配占位符文本本身（例如 `missing`）时，占位符才会被
    当成"匹配成功" ⇒ 判绿。``present_when_missing`` 在**这个组合**下不发力，
    因为 `_is_all_missing` 已被陷阱骗过（返回 False）。

    ⇒ 这是本票**如实登记的已知局限**：路径字面含 `--- ` 且 pattern 能匹配
    `<missing:` 时，fail-closed 会被绕过。真实入库路径不含 `--- `，故当前接线
    不受影响；修法是让 `_is_all_missing` 改判路径存在性（动 t1 语义，不在本票范围）。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    trap_rel = "--- trap ---.json"  # known-absent: 本测试故意不创建它
    check = eval_check(
        id="eval-sniff-trap-lax",
        paths=[trap_rel],
        pattern=r"missing",  # ← 宽到能匹配占位符自身
        present_when_missing="fail",
        parse_as="json",
    )
    contract = make_json_contract(repo, check)
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")
    result = report_of(proc)["results"][0]

    # 记录现状：宽 pattern 下确实判绿。若有人修好了嗅探，这条会失败 ——
    # 那时请**更新本测试与上面的登记**，而不是把它删掉。
    assert result["passed"] is True, (
        "嗅探陷阱 + 宽 pattern 现在也能判红了 —— 好于预期，"
        "请把 _is_all_missing 的已知局限登记从「成立」改为「已修」并更新本测试"
    )


# ==========================================================================
# 13. `repo_root` 必需化（t12 / Standards 轴）：拒绝"可选的守卫"
# ==========================================================================
#
# ★ 修的是什么（真实洞，主理人已独立复现）
#
# `evaluate()` 曾写作 `repo_root: Path | None = None`，并在解析层加
# `and repo_root is not None` 守卫。后果是同一份输入、同一个 check：
#
#     evaluate(check, out, "closed", root)   => passed=False  ✅ 判红
#     evaluate(check, out, "closed")         => passed=True   ★ 整层静默消失
#
# 唯一的生产调用点 `run_all` 一直传 `repo_root`，所以 CLI 行为是对的 —— 但
# **库调用方 / 将来新增的调用点**会静默丢掉整层 fail-closed。
#
# ★ 这正是本仓反复付费的形态：「守卫存在、有测试，**但它不在这条路径上**」
#   = 那条路径上没有守卫。而且它已经**实际误导过一次**：主理人用不传 repo_root
#   的方式复现时得到 passed=True，一度误判 W1 修复失效。
#
# 故本节的测试不是"补一个断言"，而是把「这个参数**必须**必需」钉成可执行的契约：
# 一旦有人为了"方便调用方"把它改回可选，下面的测试会立刻失败。


def test_evaluate_rejects_a_missing_repo_root():
    """★★ 核心回归：``evaluate()`` 不许再有可选的 ``repo_root``（缺省 = 静默 fail-open）.

    ★ 用 ``inspect.signature`` 直接检查**签名形状**，而不是只测行为：
    行为测试只能覆盖"当前实现"，而缺省值正是"换个调用方式就静默降级"的入口 ——
    必须从签名上就不可省略。两条断言一起才封闭：
      (1) ``repo_root`` 没有 ``default``（必需的）；
      (2) 它的标注不是 ``Path | None``（那正是旧 fail-open 的入口）。
    """
    import inspect

    params = inspect.signature(drift_gate.evaluate).parameters
    assert "repo_root" in params, "evaluate() 没有 repo_root 参数了 —— 语义已变，请复核"
    assert params["repo_root"].default is inspect.Parameter.empty, (
        "repo_root 又有了缺省值 —— 那会让不传它的调用路径**静默**丢掉整个 "
        "parse_as fail-closed 层（实测 passed 从 False 变 True）。"
        "若确实需要可选，请改成显式传参的另一个函数，不要用缺省。"
    )
    # ★ 标注本身也要不含 None：`Path | None` 正是旧 fail-open 的入口。
    #   本模块用了 `from __future__ import annotations`，故标注是**字符串**；
    #   这里同时兼容字符串与真对象两种形态，避免依赖"当前恰好是哪种"。
    annotation = params["repo_root"].annotation
    annotation_text = annotation if isinstance(annotation, str) else str(annotation)
    assert "None" not in annotation_text, (
        f"repo_root 的标注是 {annotation_text!r}；应为 `Path`（不是 `Path | None`）"
        " —— 允许 None 正是旧 fail-open 的入口。"
    )
    assert annotation_text.strip("'\" ") in {"Path", "pathlib.Path"}, (
        f"repo_root 的标注是 {annotation_text!r}；应为 `Path`"
    )


def test_missing_repo_root_is_a_loud_error_not_a_silent_pass(tmp_path: Path):
    """★★ 反例取证：不传 ``repo_root`` 时**必须报错**，绝不返回 ``passed=True``.

    ★ 这是主理人实测的最小反例的固化形态：内容**含 pattern 要匹配的字面**、
    但**不是合法 JSON**（截断）。改前不传 repo_root 会 ``passed=True`` —— 一个
    半截产物被判成通过，且**没有任何信号**。改后是 ``TypeError``（显式失败）。
    """
    (tmp_path / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
    check = eval_check(parse_as="json")
    output = drift_gate.run_check_files(check, tmp_path)
    assert "--- " in output, "夹具没读到文件，这条测的就不是解析层了"

    with pytest.raises(TypeError, match="repo_root"):
        drift_gate.evaluate(check, output, "closed")  # type: ignore[call-arg]


def test_every_call_path_reds_on_the_same_truncated_input(tmp_path: Path):
    """★ AC 的反例证明：同一份「含 pattern 字面但非法 JSON」输入，**每条**调用路径都判红.

    ★ 覆盖两条路径，避免"只有我想到的那条路被封住"：
      (a) **库调用**：直接 ``evaluate(check, out, mode, root)``；
      (b) **端到端 CLI**：真子进程 + ``--repo-root``，从 ``--json`` 报告读 ``passed``。
    两条都必须 ``False``，且 CLI 的 rc 必须是 1。
    """
    (tmp_path / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
    check = eval_check(parse_as="json")

    # (a) 库调用
    output = drift_gate.run_check_files(check, tmp_path)
    passed, detail = drift_gate.evaluate(check, output, "closed", tmp_path)
    assert passed is False, f"库调用路径漏判: {detail!r}"

    # (b) 端到端 CLI
    contract = make_json_contract(tmp_path, check)
    proc = run_gate(contract, tmp_path, "--phase", "static", "--mode", "closed", "--json")
    assert proc.returncode == 1, f"CLI 路径 rc={proc.returncode}（应 1）"
    assert report_of(proc)["results"][0]["passed"] is False


def test_run_all_is_the_production_path_and_passes_repo_root(tmp_path: Path):
    """★ 生产路径（``run_all``）必须把 ``repo_root`` 传下去.

    ★ 前面几条钉住"签名不可省略 + 不传就报错"，这条钉住"生产路径确实传了"。
    三者合起来是完整的：**签名要求传** ∧ **不传会炸** ∧ **生产路径传了**。
    """
    (tmp_path / "card.json").write_text("{not json", encoding="utf-8")
    contract_path = make_json_contract(tmp_path, eval_check(parse_as="json"))
    contract = drift_gate.load_contract(str(contract_path))

    report = drift_gate.run_all(contract, "static", "closed", tmp_path)
    assert report["results"][0]["passed"] is False, (
        f"run_all 没把解析层跑到: {report['results'][0]['detail']!r}"
    )
    assert report["any_block_fail"] is True


# ==========================================================================
# 14. 收敛后的共享常量与 helper：单一来源必须真的被用上（t12）
# ==========================================================================


def test_shared_fixture_constants_are_the_single_source():
    """★ 两套测试的清单断言**同源**：本文件用的是共享模块里的那个对象.

    ★ 这堵的是"收敛后又各自长出副本"：只要有人在本文件重新定义 `EVAL_CHECK_IDS`，
    它就不再是共享模块的那个对象，本断言会失败。
    """
    import eval_gate_fixtures as shared

    assert drift_gate is not None  # 保持 import 侧效应（sys.path）已生效
    assert EVAL_CHECK_IDS is shared.EVAL_CHECK_IDS, (
        "本文件的 EVAL_CHECK_IDS 不再是共享模块里的那个对象 —— 又长出副本了"
    )
    assert BASELINE_CHECK_IDS is shared.BASELINE_CHECK_IDS
    assert TIMING_ARTIFACT is shared.TIMING_ARTIFACT


def test_shared_constants_match_the_real_contract():
    """★ 共享常量必须与**真契约**逐条一致（常量不是真值源，契约才是）."""
    checks = contract_checks()
    wired = [c["id"] for c in checks if "present_when_missing" in c]
    assert wired == list(EVAL_CHECK_IDS)
    baseline = [c["id"] for c in checks if c["id"] not in EVAL_CHECK_IDS]
    assert baseline == list(BASELINE_CHECK_IDS)
    assert len(checks) == len(EVAL_CHECK_IDS) + len(BASELINE_CHECK_IDS)


def test_no_duplicate_check_ids_between_the_two_shared_lists():
    """★ 两份清单不得重叠（重叠会让"其余 check"的断言失去意义）."""
    assert not set(EVAL_CHECK_IDS) & set(BASELINE_CHECK_IDS)
    assert len(set(EVAL_CHECK_IDS)) == len(EVAL_CHECK_IDS)
    assert len(set(BASELINE_CHECK_IDS)) == len(BASELINE_CHECK_IDS)


def test_severity_head_accepts_both_label_states():
    """★ 收敛后的 ``_severity_head`` helper 行为与原文等价（收敛不改语义）.

    ★ 收敛前该表达式在文件里重复 3 处。它必须与 ``run_all`` 里真正的阻断判别式
    （``severity == "block" and mode == "closed"``）同形 —— 两处漂移会让**报告写的
    标签**与**退出码**互相矛盾（报告说 [WARN] 不阻断，rc 却是 1）。
    """
    block = eval_check(severity="block")
    warn = eval_check(severity="warn")
    assert drift_gate._severity_head(block, "closed") == f"[BLOCK] {block['id']}"
    assert drift_gate._severity_head(block, "open") == f"[WARN] {block['id']}"
    assert drift_gate._severity_head(warn, "closed") == f"[WARN] {warn['id']}"
    assert drift_gate._severity_head(warn, "open") == f"[WARN] {warn['id']}"


def test_severity_head_label_agrees_with_the_exit_code(tmp_path: Path):
    """★ 交叉验证：报告里的 [BLOCK]/[WARN] 标签必须与真实 rc 一致.

    ★ 这是"收敛成一处"的**目的**：标签与退出码只有一个真值源。用 4 种
    severity×mode 组合跑真 CLI，断言"标签是 [BLOCK]" ⇔ "rc==1"。
    """
    for severity, mode, expect_block in [
        ("block", "closed", True),
        ("block", "open", False),
        ("warn", "closed", False),
        ("warn", "open", False),
    ]:
        case = tmp_path / f"{severity}_{mode}"
        case.mkdir()
        (case / "card.json").write_text('{"verdict": "pass", "truncated":', encoding="utf-8")
        contract = make_json_contract(
            case, eval_check(severity=severity, parse_as="json")
        )
        proc = run_gate(contract, case, "--phase", "static", "--mode", mode, "--json")
        result = report_of(proc)["results"][0]

        assert result["passed"] is False
        labelled_block = "[BLOCK]" in result["detail"]
        assert labelled_block is expect_block, (
            f"severity={severity} mode={mode}: 标签 "
            f"{result['detail'].splitlines()[0][:30]!r} 与预期不符"
        )
        assert (proc.returncode == 1) is expect_block, (
            f"severity={severity} mode={mode}: rc={proc.returncode} 与标签不一致"
        )


def test_validate_functions_share_one_error_path(tmp_path: Path):
    """★ 表驱动校验器：两个字段的非法值报错**都必须**点名 check id 与非法值.

    ★ 收敛 `validate_*` 的风险是"为了去重把错误信息压成一句通用文案"。这条测试
    把两个字段各自**关键**的信息钉住：字段名、check id、非法值、以及各自**专属**的
    解释（`present_when_missing` 说"看起来在守却完全没守"；`parse_as` 说"声明了要
    校验却没校验，比不声明更坏"）。文案可以改，但这些要素不能丢。
    """
    cases = [
        ("present_when_missing", "FALSE", "看起来在守却完全没守"),
        ("parse_as", "JSON", "声明了要校验却没校验"),
    ]
    for field, bad, distinctive_phrase in cases:
        repo = tmp_path / field
        repo.mkdir()
        contract = make_json_contract(repo, eval_check(**{field: bad}))
        proc = run_gate(contract, repo, "--phase", "static", "--mode", "closed", "--json")

        assert proc.returncode == 2, f"{field}={bad} 应 rc=2，实得 {proc.returncode}"
        combined = proc.stdout + proc.stderr
        assert "META-ERROR" in combined
        assert "eval-structured-card" in combined, f"{field}: 没点名 check id"
        assert repr(bad) in combined, f"{field}: 没点名非法值"
        assert field in combined, f"{field}: 没点名字段名"
        assert distinctive_phrase in combined, (
            f"{field}: 丢了这个字段**专属**的后果说明 —— 为去重牺牲了错误信息质量"
        )


def test_validate_rejects_non_dict_check_elements():
    """★ 两个校验器共用的"非对象元素"分支仍生效（rc=2，不静默）.

    ★ 收敛把 `isinstance(check, dict)` 判断抽成一个共用函数，这条钉住它没被漏掉。
    走库层调用（CLI 的 `--contract` 会先在 JSON 解析处挡掉一部分形态）。
    """
    for validator in (
        drift_gate.validate_present_when_missing,
        drift_gate.validate_parse_as,
    ):
        with pytest.raises(SystemExit) as excinfo:
            validator(["not-a-dict"])
        assert excinfo.value.code == 2, f"{validator.__name__} 没有以 rc=2 退出"

