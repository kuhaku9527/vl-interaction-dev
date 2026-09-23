# ruff: noqa: RUF002, RUF003
"""#159 评测回归门禁 —— 两套测试**共用**的常量（单一来源，不重复字面量）.

★ 为什么这个文件存在
--------------------
`test_eval_gate_contract.py`（契约接线的行为测试）与
`test_drift_gate_missing_result.py`（`present_when_missing` / `parse_as` 的行为测试）
**用途不同、不该合并**，但它们必须认同**同一组事实**：

  * 哪 4 条 check 是 #159 接进来的（`EVAL_CHECK_IDS`）；
  * #159 之前那 11 条既有 check 是谁（`BASELINE_CHECK_IDS`）；
  * 两张记分卡产物落在哪个入库路径（`DIRECTED_ARTIFACT` / `TIMING_ARTIFACT`）。

这些值曾在两个文件里**各写一份**（一处 `= (...)`、一处 `= [...]`）。两份拷贝的
危险不在于"多打几个字"，而在于**它们会各自漂移**：改一处、忘另一处，于是两个
文件对"接线清单是什么"给出不同答案 —— 而两个都对门禁行为下断言。那种"两边都绿、
但说的不是同一件事"的形态，正是本仓反复付费的那一类。

本模块**只放常量**（外加一个纯函数），不含测试、不被 pytest 收集
（文件名不匹配 `test_*.py`），故两套测试各自独立运行、互不牵连。

★ 目录位置：与两套测试同目录，pytest 的 `rootdir` 插入规则会让它可直接 import；
两个测试文件另有显式 `sys.path` 插入，避免依赖 pytest 的隐式行为。
"""

from __future__ import annotations

#: #159 引入的评测 check（定向轴 ×2 + 时序轴 ×2）。顺序与契约一致。
EVAL_CHECK_IDS: tuple[str, ...] = (
    "eval-directed-axis-frozen-reading",
    "eval-directed-axis-structural-guards",
    "eval-timing-axis-frozen-reading",
    "eval-timing-axis-structural-guards",
)

#: #159 之前契约里的 11 条 check —— 一条都不许增删改名（回归保护）。
#: ⚠️ `vlm-n_ctx` 是**连字符**（不是下划线），照抄易错，故一律由测试比对真契约。
BASELINE_CHECK_IDS: tuple[str, ...] = (
    "vlm-kv-quantization",
    "vlm-n_ctx",
    "memory-store-port",
    "webui-gateway-port",
    "main-context-env",
    "webinfer-request-timeout",
    "ruff-pin",
    "webui-vitest",
    "webui-joywiki",
    "webui-wikinspace",
    "webinfer-wiki-recall",
)

#: 两张记分卡的**入库**结果产物（`git ls-files` 认，未被 .gitignore 覆盖）。
DIRECTED_ARTIFACT = "doc/research/data/decision_eval_directed_card.json"
TIMING_ARTIFACT = "doc/research/data/decision_eval_timing_card.json"

#: 轴的短名 → 产物路径（供 `@pytest.mark.parametrize` 用）。
ARTIFACTS_BY_AXIS: dict[str, str] = {
    "directed": DIRECTED_ARTIFACT,
    "timing": TIMING_ARTIFACT,
}

#: 每条 eval check → 它读哪条轴（`content_digest` 的期望值按 check 登记在契约里，
#: 但断言时需要知道"这条 check 的摘要该等于哪张卡的摘要"）。
AXIS_BY_CHECK_ID: dict[str, str] = {
    "eval-directed-axis-frozen-reading": "directed",
    "eval-directed-axis-structural-guards": "directed",
    "eval-timing-axis-frozen-reading": "timing",
    "eval-timing-axis-structural-guards": "timing",
}

#: 摘要算法的**唯一**实现所在的模块名（执行器与两套测试都必须经它取值）。
DIGEST_MODULE_NAME = "eval_card_digest"


def artifact_of(axis: str) -> str:
    """轴的短名 → 该轴入库产物路径（**纯函数**，无 IO）."""
    return ARTIFACTS_BY_AXIS[axis]


def is_eval_check(check_id: str) -> bool:
    """该 check id 是否是 #159 接进来的评测 check（**纯函数**，无 IO）."""
    return check_id in EVAL_CHECK_IDS


# --------------------------------------------------------------------------
# #169：内容摘要（`content_digest`）的**单一来源**读取
# --------------------------------------------------------------------------
#
# ★ 为什么这些 helper 放在共享模块里（而不是各测试文件各写一份）
# ---------------------------------------------------------------
# #169 的一条硬要求是：**摘要的期望值只许有一个真值源 —— 契约**。
# 本票之前，期望值是 `test_eval_gate_contract.py` 里的 `CANONICAL_DIGESTS` 字面量；
# 现在它搬进了 `config/drift-contract.json` 的 `content_digest` 字段，两套测试
# （契约侧 + 执行器字段行为侧）**都**要读它。若各写一份读取逻辑，就会重演本仓
# 反复付费的形态：两份拷贝各自漂移，而两边都对同一份事实下断言、且都绿。
#
# 故读取逻辑（含"缺了/多个不一致时判红"的判据）**只有一个定义**，在这里。

CONTRACT_REL = "config/drift-contract.json"


def repo_root() -> "Path":  # noqa: F821 - Path 在函数体内 import，避免模块级副作用
    """仓库根（`scripts/tests/` 的上两级）—— 与既有测试用的是同一个推导式."""
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def load_contract_document() -> dict:
    """读**真契约**（`config/drift-contract.json`）并返回其 JSON 文档."""
    import json
    from pathlib import Path

    path = Path(repo_root()) / CONTRACT_REL
    return json.loads(path.read_text(encoding="utf-8"))


def declared_digest_of(check_id: str) -> str:
    """从**契约**读出该 check 声明的 ``content_digest``（唯一真值源）.

    Raises
    ------
    AssertionError
        该 check 不在契约里，或没有声明摘要 —— 两者都是"本测试要核的事实现在
        没有出处"，必须判红而不是 skip（测不到 ≠ 通过）。
    """
    for check in load_contract_document()["checks"]:
        if check["id"] == check_id:
            digest = check.get("content_digest")
            assert digest, (
                f"契约里 {check_id} 没有 content_digest —— 摘要层失去了真值源。"
                f"本测试不再自带一份期望值（那正是 #169 要消灭的『同一事实两份副本』）。"
            )
            return digest
    raise AssertionError(f"契约里找不到 check {check_id!r}")


def declared_digest_by_axis() -> dict[str, str]:
    """轴短名 → 契约声明的摘要（同一条轴上的两条 check 必须声明**同一个**值）.

    ★ 同一张卡的摘要若在两条 check 上不一致，说明有人只改了一处 —— 那是
    「同一事实两份副本」的复活形态，本函数直接判红。
    """
    digests: dict[str, str] = {}
    for axis, artifact in ARTIFACTS_BY_AXIS.items():
        want = {
            cid: declared_digest_of(cid)
            for cid, a in AXIS_BY_CHECK_ID.items()
            if a == axis
        }
        assert len(set(want.values())) == 1, (
            f"{axis} 轴的两条 check 声明了**不同**的摘要：{want} —— "
            f"它们读的是同一份产物，摘要必须一致"
        )
        assert artifact  # 保持产物路径被引用（下面的断言依赖绑定关系）
        digests[axis] = next(iter(want.values()))
    return digests


# --------------------------------------------------------------------------
# 共用的「跑真门禁」助手（★ 同一段逻辑不许有两份拷贝）
# --------------------------------------------------------------------------
#
# ★ 为什么这些也放进共享模块（复核 #169 指出，属实测的重复）：
# `test_drift_gate_missing_result.py` 与 `test_drift_gate_content_digest.py` 各自
# 抄了一份**逐字相同**的 `run_gate` / `report_of` / `assert_no_report`。两份拷贝的
# 危险不是"多打几个字"，而是**它们会各自漂移**：改一处、忘一处，于是两个文件对
# "门禁怎么跑、报告怎么读"给出不同答案 —— 而两边都在对门禁行为下断言。
# 那正是本模块存在的理由（见文件头 docstring），故收敛到这里。

#: 门禁执行器路径（两个测试文件此前各自从 `SCRIPTS` 拼一次）。
GATE_REL = "scripts/drift_gate.py"

#: 最常用的 CLI 组合：静态阶段 + closed 模式 + JSON 报告。
CLOSED_JSON: tuple[str, ...] = ("--phase", "static", "--mode", "closed", "--json")


def gate_path() -> "Path":  # noqa: F821 - Path 在函数体内 import
    """门禁执行器路径."""
    from pathlib import Path

    return Path(repo_root()) / GATE_REL


def write_contract(repo: "Path", *checks: dict, source: str = "test") -> "Path":  # noqa: F821
    """把 check 们写成 ``repo/contract.json``，返回其路径（``--repo-root`` 也指向 repo）."""
    import json
    from pathlib import Path

    path = Path(repo) / "contract.json"
    path.write_text(
        json.dumps(
            {"version": 99, "source_of_truth": f"test-{source}", "checks": list(checks)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run_gate(contract: "Path", repo_root_: "Path", *extra: str):  # noqa: F821
    """以子进程跑**真 CLI**（真退出码 + 真 stdout），历史文件写到临时目录.

    ★ ``--history-dir`` 指向临时目录是必须的：否则每次跑都会往仓库的
    ``logs/drift-gate-history/`` 里写文件（既有测试就是这么防的）。
    """
    import subprocess
    import sys
    from pathlib import Path

    return subprocess.run(
        [
            sys.executable,
            str(gate_path()),
            "--contract", str(contract),
            "--repo-root", str(repo_root_),
            "--history-dir", str(Path(repo_root_) / "history"),
            *extra,
        ],
        cwd=str(repo_root_),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def report_of(proc) -> dict:
    """解析 ``--json`` 报告；解析失败时把 stderr/stdout 一起带进断言信息."""
    import json

    assert proc.stdout.strip(), (
        f"没有 stdout 可解析（rc={proc.returncode}）stderr={proc.stderr[:400]!r}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - 只在契约被写坏时触发
        raise AssertionError(
            f"stdout 不是 JSON（rc={proc.returncode}）: {exc}\nstdout={proc.stdout[:400]!r}"
        ) from exc


def assert_no_report(proc) -> None:
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
