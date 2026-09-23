# ruff: noqa: RUF001, RUF002, RUF003
r"""Drift Gate —— ``content_digest``（**同源副本被改**）的 fail-closed 语义（工单 #169）.

★ 为什么这个文件存在
--------------------
`test_drift_gate_missing_result.py` 覆盖了前两层：``present_when_missing``
（**文件在不在**）与 ``parse_as``（**内容是不是一份完整卡片**）。本文件覆盖第三层：
**文件在、内容合法 JSON，但内容被改了**。

它是父 spec §6.5 **W2** 的直接取证。W2 的实测形态（主理人复现，本文件复跑）：

    把某个 variant 的 ``criteria_registry`` 整份换成
    ``[{"criterion_id": "D5-cost-index", "threshold": 999.0}]``
    ⇒ 产物**仍是合法 JSON** ⇒ 旧设计 **门禁 rc=0（绿）**，而 pytest 侧的
    canonical 摘要层判红。

为什么必须让**门禁**也守住它（而不只靠 pytest）：CI 的 ``drift-gate`` job
**刻意独立跑**（本仓不加 ``needs``，见父 spec §1 被否方案）——「pytest 没跑或挂了」
时，原来那唯一一道守卫不在场。

★ 本文件钉住的性质（每一类都成对：**该红的红** ∧ **不该红的不红**）
--------------------------------------------------------------------
1. 缺省（不写该字段）= **不做**摘要校验（既有 11 条零变更的回归保护）；
2. 声明的摘要与产物相符 ⇒ 通过；
3. 伪造同源副本 ⇒ 判红、rc=1，且 detail 点名文件 / 期望值 / 实际值；
4. 非法值（``"abc"`` / ``true`` / ``1`` / 大写 hex / 63 或 65 位 / 非 hex 字符 /
   非字符串类型）⇒ **rc=2** 且**点名 check id 与非法值**，**绝不**静默降级成"不校验"；
   ★ 但显式 ``null`` 是**合法**的（= 缺省，即"把缺省值写出来"）—— 与 ``parse_as``
   同形，与 ``present_when_missing`` 刻意不同，见 ``test_default_is_none_...``；
5. ``paths`` 为空或不止一个 ⇒ **rc=2**（一个摘要只能对应恰好一份内容）；
6. ★ **反假阳性**：只重排/重缩进/换行尾 ⇒ **不得**判红（摘要对排版不敏感）；
7. 三层**互不覆盖**：文件缺失仍走 ``present_when_missing`` 的措辞、内容不可解析
   仍走 ``parse_as`` 的措辞；
8. 语义边界与既有字段一致：``severity=warn`` / ``mode=open`` 只影响**是否阻断**，
   不影响 ``passed`` 与措辞里的事实。

★ 一律在 ``tmp_path`` 镜像里跑**真执行器**（子进程 + ``--repo-root``），
**不碰**入库产物、**不碰** ``config/drift-contract.json``。夹具常量取自
``eval_gate_fixtures.py``（**不重复字面量**）。

Run: python -m pytest scripts/tests/test_drift_gate_content_digest.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drift_gate  # noqa: E402
import eval_card_digest  # noqa: E402

# ★ 接线清单 / 两张产物路径 / 「跑真门禁」助手 的**单一来源**都在
#   `eval_gate_fixtures.py`（与 `test_eval_gate_contract.py` /
#   `test_drift_gate_missing_result.py` 共用）。本文件不重写这些字面量或逻辑。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gate_fixtures import (  # noqa: E402
    AXIS_BY_CHECK_ID,
    BASELINE_CHECK_IDS,
    CLOSED_JSON,
    DIRECTED_ARTIFACT,
    EVAL_CHECK_IDS,
    TIMING_ARTIFACT,
    assert_no_report,
    declared_digest_of,
    report_of,
    run_gate,
    write_contract,
)

#: 定向轴的两个 variant（契约 pattern 里已绑，本文件按名引用）。
V1 = "P_live4_prod_prompt"
V2 = "P2_live4_prod_prompt_profile"

#: 合法摘要的**权威形状**（与 `drift_gate.CONTENT_DIGEST_HEX_LENGTH` 比对）。
EXPECTED_HEX_LENGTH = 64


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def committed_text(rel: str) -> str:
    """读**入库**产物文本（只读，绝不改写）."""
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def forge_registry(text: str, variant: str = V1) -> str:
    """复现 W2 的最小反例：整份替换某 variant 的 ``criteria_registry``.

    ★ 逐字照抄工单的反例形态。产物**仍是合法 JSON**（``parse_as`` 抓不到），
    ``criteria`` 那一份**没动**（契约已绑的字面全在、正则全部命中）。
    """
    doc = json.loads(text)
    doc["cards"][variant]["criteria_registry"] = [
        {"criterion_id": "D5-cost-index", "threshold": 999.0}
    ]
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def forge_registry_and_inject_placeholder(
    text: str, rel: str, variant: str = V1, kind: str = "missing"
) -> str:
    """★ 复现 #169 落地后实测出的 **fail-open 绕过**：伪造 + 往产物**内容**里塞占位符字面量.

    原实现（本票开发期）用 ``f"<missing:{rel}>" in output`` 判断"该文件缺失 ⇒ 本层
    跳过"。而 ``output`` 是 ``run_check_files`` 的**合并内容** —— 占位符与真实文件内容
    是**同一种文本** ⇒ 产物自己就能伪造这个判据，让摘要层整体跳过。

    ★ 实测（修复前）：伪造 ``criteria_registry`` **并**注入该字面量 ⇒ 门禁 **rc=0**，
    与"压根没有这道守卫"完全一样。修复后 ⇒ rc=1。

    Parameters
    ----------
    text : str
        入库产物文本。
    rel : str
        该 check 引用的相对路径（占位符里要出现它才能骗过原判据）。
    variant : str
        定向轴的 variant 名。
    kind : str
        ``"missing"`` 或 ``"read-error"``（两种占位符形态都要守）。
    """
    doc = json.loads(text)
    doc["cards"][variant]["criteria_registry"] = [
        {"criterion_id": "D5-cost-index", "threshold": 999.0}
    ]
    marker = (f"<missing:{rel}>" if kind == "missing"
              else f"<read-error:{rel}:boom>")
    # 塞进一个**非 decision-bearing** 的字符串叶子：既不改判据读数，也保证
    # 注入确实落在文件内容里（而不是碰巧没写进去）。
    doc["cards"][variant]["axis_question"] = marker
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def artifact_check(**overrides) -> dict:
    """一条"读某一轴入库产物"的 check（默认**不写** content_digest，隔离被测变量）.

    ★ 默认带 ``present_when_missing="fail"``：真实接线（真契约）就是这样写的，
    本文件要测的是"在真实形态之上再加一层"。
    """
    check = {
        "id": "eval-digest-probe",
        "decision_ref": (
            "doc/specs/2026-09-24-decision-eval-content-digest.md D-169"
        ),
        "description": "入库记分卡的内容必须与冻结的 canonical 摘要一致",
        "phase": "static",
        "paths": [DIRECTED_ARTIFACT],
        "pattern": r'"verdict":\s*"fail"',
        "severity": "block",
        "present_when_missing": "fail",
        "parse_as": "json",
    }
    check.update(overrides)
    return check


def eval_check_like(**overrides) -> dict:
    """一条与 ``artifact_check`` 同形、但路径 / 正则可自定的 check（用于合成产物用例）."""
    base = artifact_check(paths=["card.json"], pattern=r"x")
    base.update(overrides)
    return base


def mirror(tmp_path: Path, directed: str | None = None, timing: str | None = None) -> Path:
    """最小镜像仓库：只放被引用的两件产物（可指定替换文本）."""
    root = tmp_path / "mirror"
    if root.exists():
        shutil.rmtree(root)
    (root / "doc" / "research" / "data").mkdir(parents=True)
    for rel, override in ((DIRECTED_ARTIFACT, directed), (TIMING_ARTIFACT, timing)):
        text = override if override is not None else committed_text(rel)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return root


# ★ `write_contract` / `run_gate` / `report_of` / `assert_no_report` / `CLOSED_JSON`
#   全部来自共享模块 `eval_gate_fixtures.py`（**不在此另写一份**）—— 它们此前在本文件
#   与 `test_drift_gate_missing_result.py` 里各有一份**逐字相同**的拷贝，而那正是
#   本仓反复付费的「同一事实两份副本、各自漂移」形态（复核 #169 指出，已收敛）。


# --------------------------------------------------------------------------
# 0. 契约常量与形状（防「文档说 64 位，代码写别的」两处漂移）
# --------------------------------------------------------------------------


def test_default_is_none_so_no_digest_check_by_default():
    """★ 缺省**必须**是 ``None``（不做摘要校验）—— 既有 11 条的回归保护.

    ★ 理由具体：既有 check 读的是仓库内配置 / 源码（``run-windows.env`` / ``.py`` /
    ``.yml`` / ``.html`` / ``.js``）。若缺省改成"要求摘要"，任何一次注释改动都会
    把 CI 判红 —— 门禁被判死，然后被人拆掉。
    """
    assert drift_gate.DEFAULT_CONTENT_DIGEST is None
    assert drift_gate.CONTENT_DIGEST_HEX_LENGTH == EXPECTED_HEX_LENGTH
    assert "content_digest" not in artifact_check(), "夹具默认不该带该字段"
    assert drift_gate.content_digest_of(artifact_check()) is None
    assert drift_gate.content_digest_of(artifact_check(content_digest=None)) is None, (
        "显式 null 在本字段是**合法**的（等于缺省）—— 与 parse_as 一致、"
        "与 present_when_missing 刻意不同"
    )


@pytest.mark.parametrize("axis", ["directed", "timing"])
def test_committed_digest_is_the_authoritative_shape(axis: str) -> None:
    """★ 契约里声明的摘要必须是**合法形状**（否则 rc=2，而不是静默不校验）."""
    rel = DIRECTED_ARTIFACT if axis == "directed" else TIMING_ARTIFACT
    for cid, a in AXIS_BY_CHECK_ID.items():
        if a != axis:
            continue
        digest = declared_digest_of(cid)
        assert eval_card_digest.is_legal_digest(digest), (
            f"{cid} 声明的摘要形状非法: {digest!r}"
        )
    # 且它必须**就是**产物今天的摘要（契约与产物互相钉死）
    actual = eval_card_digest.canonical_digest(committed_text(rel))
    assert declared_digest_of(f"eval-{axis}-axis-frozen-reading") == actual, (
        f"{rel}: 契约声明的摘要与产物实际内容不符"
    )


# --------------------------------------------------------------------------
# 1. 缺省 == 不做校验（既有行为零变更）
# --------------------------------------------------------------------------


def test_omitted_field_does_not_check_content(tmp_path: Path):
    """★ 不写该字段 ⇒ **不做**摘要校验：伪造内容也照旧按正则判（这里是 rc=0）.

    ★ 反向保护：这条证明新层**只在被显式声明时**发力。若缺省就开始校验，
    既有 11 条会在任何一次源码改动上集体判红。
    ★ 这里的 rc=0 是**刻意**的：正则层绑的是 ``criteria`` 那一份（未改动），
    故它在"没有摘要层"的世界里确实判绿 —— 那正是 W2 的形态。
    """
    repo = mirror(tmp_path, directed=forge_registry(committed_text(DIRECTED_ARTIFACT)))
    contract = write_contract(repo, artifact_check())
    proc = run_gate(contract, repo, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, (
        f"缺省语义变了：不写 content_digest 时不该管内容摘要: {result['detail']!r}"
    )
    assert proc.returncode == 0
    assert result["content_digest"] is None


# --------------------------------------------------------------------------
# 2 & 3. ★ 核心 AC：伪造同源副本 ⇒ 判红（并与"相符即通过"配对）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variant", [V1, V2])
def test_forged_registry_reds_the_gate(tmp_path: Path, variant: str):
    """★★ **核心 AC**：伪造某 variant 的 ``criteria_registry`` ⇒ passed=False 且 rc=1.

    ★ 改前（工单由主理人复现，本文件由执行者复跑）**rc=0** —— 因为
    「内容改了但仍是合法 JSON」这一整类，门禁上**一道守卫都没有**。
    """
    forged = forge_registry(committed_text(DIRECTED_ARTIFACT), variant)
    repo = mirror(tmp_path, directed=forged)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    report = report_of(proc)
    result = report["results"][0]

    assert result["passed"] is False, f"伪造 registry 被判绿: {result['detail']!r}"
    assert proc.returncode == 1, f"block+closed 下应 rc=1，实得 {proc.returncode}"
    assert report["block_failures"] == 1
    assert report["any_block_fail"] is True

    # detail 必须点名**文件**、**期望值**、**实际值** —— 三者缺一，读报告的人
    # 都得自己去算一遍才知道差在哪。
    detail = result["detail"]
    assert DIRECTED_ARTIFACT in detail, f"detail 没点名文件: {detail!r}"
    expected = declared_digest_of("eval-directed-axis-frozen-reading")
    actual = eval_card_digest.canonical_digest(forged)
    assert expected in detail, f"detail 没写期望值: {detail!r}"
    assert actual in detail, f"detail 没写实际值: {detail!r}"
    assert expected != actual, "伪造竟然没改变摘要 —— 本用例无效"
    # ★ 必须说清这是"内容是合法 JSON 但被改了"，不是解析失败：
    #   否则运维会把两类完全不同的处置（重新生成产物 vs 查漂移/查篡改）搞混。
    assert "合法 JSON" in detail, f"detail 没说清『仍是合法 JSON』: {detail!r}"
    assert "同源副本" in detail, f"detail 没点出同源副本这一层: {detail!r}"
    assert "[SKIP]" not in detail, "摘要不符却打 SKIP 措辞"


def test_healthy_artifact_with_declared_digest_stays_green(tmp_path: Path):
    """★★ **反假阳性**：内容与声明的摘要相符 ⇒ passed=True、rc=0.

    ★ 这是本文件最重要的反向断言。一个"内容不符就判红"的守卫若在**健康产物**上
    也判红，会把每一次正常情况判死，人会因此拆掉它 —— 于是真正的洞（同源副本被改）
    回来。
    """
    repo = mirror(tmp_path)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, f"健康产物被误判红: {result['detail']!r}"
    assert proc.returncode == 0
    assert "[OK]" in result["detail"]


# --------------------------------------------------------------------------
# 4b. ★★ 产物**内容**不得能伪造"文件缺失"这一判据（实测出的 fail-open）
# --------------------------------------------------------------------------
#
# ★ 为什么这一节必须存在（本票开发期实测，不是设想）
# --------------------------------------------------
# 摘要层与 parse_as 层原先用 `f"<missing:{rel}>" in output` 判断"该文件缺失 ⇒
# 本层跳过"。而 `output` 是 `run_check_files` 的**合并内容** —— 占位符与真实文件
# 内容**是同一种文本**。⇒ 产物只要在自己的内容里塞一个
# `<missing:<自己路径>>` 字面量，这两层就会**整体跳过**，门禁回到 rc=0。
#
# 这与本仓 09-21「测试有效性」纪律是同一类病：**守卫在场，但它的判据能被被守卫的
# 对象伪造**。当时实测的读数：伪造 registry + 注入该字面量 ⇒ rc=0（= 没有守卫）。
# 修复：缺失判定改为问文件系统（`drift_gate._path_state`），不再嗅探合并串。
# 下面两条把两种占位符形态都钉死，且**成对**给出（该红的红 + 真缺失仍走原措辞）。


@pytest.mark.parametrize("kind", ["missing", "read-error"])
def test_content_cannot_forge_the_missing_predicate(tmp_path: Path, kind: str):
    """★★ 产物内容里塞占位符字面量 **不得**让摘要层跳过（修复前实测 rc=0）。
    """
    repo = mirror(tmp_path, directed=forge_registry_and_inject_placeholder(
        committed_text(DIRECTED_ARTIFACT), DIRECTED_ARTIFACT, kind=kind))
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, (
        f"产物内容里的 {kind} 占位符让摘要层跳过了 —— 判据被被守卫的对象伪造 "
        f"(fail-open)。detail={result['detail']!r}"
    )
    assert proc.returncode == 1, (
        f"注入占位符后门禁应仍判红，实得 rc={proc.returncode}（绕过成功）"
    )
    assert "摘要不符" in result["detail"], f"判红理由不对: {result['detail']!r}"


def test_forged_placeholder_injection_and_real_missing_are_distinguished(tmp_path: Path):
    """★ 成对对照：**真缺失**仍走 `present_when_missing` 的既有措辞.

    ★ 这条防的是"修绕过时把既有语义一起改坏"：修复只应让本层不再**嗅探**合并串，
    不该动 `present_when_missing` 的判定与文案（那是 t1 已交付并复核过的语义）。
    """
    # (1) 真缺失：把文件挪走
    repo = mirror(tmp_path / "absent")
    (repo / DIRECTED_ARTIFACT).unlink(missing_ok=True)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    absent_detail = report_of(proc)["results"][0]["detail"]
    assert "present_when_missing" in absent_detail, (
        f"真缺失的措辞被改了（应仍由 present_when_missing 处置）: {absent_detail!r}"
    )
    assert "摘要不符" not in absent_detail, "真缺失被摘要层抢走了"
    assert proc.returncode == 1

    # (2) 文件在、但内容里**出现了**那个占位符字符串 ⇒ 必须由摘要层判红
    repo2 = mirror(tmp_path / "present", directed=forge_registry_and_inject_placeholder(
        committed_text(DIRECTED_ARTIFACT), DIRECTED_ARTIFACT))
    contract2 = write_contract(repo2, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc2 = run_gate(contract2, repo2, *CLOSED_JSON)
    present_detail = report_of(proc2)["results"][0]["detail"]
    assert "present_when_missing" not in present_detail, (
        f"文件明明在，却被当成缺失处置: {present_detail!r}"
    )
    assert "摘要不符" in present_detail
    assert proc2.returncode == 1


def test_digest_and_default_differ_only_by_that_field(tmp_path: Path):
    """★ 对照的**严格**版本：两份夹具逐字段只差 ``content_digest``.

    ★ 没有这条对照，"判红"可能只是碰巧（夹具写错 / 路径不对）。两个用例的
    **输入产物逐字节相同**，只有那一个字段不同。
    """
    strict = artifact_check(content_digest=declared_digest_of(
        "eval-directed-axis-frozen-reading"))
    lax = artifact_check()
    differing = {k for k in set(strict) | set(lax) if strict.get(k) != lax.get(k)}
    assert differing == {"content_digest"}, f"两份夹具不只差一个字段: {differing}"

    def run(check: dict, tag: str) -> tuple[bool, int]:
        case = tmp_path / tag
        case.mkdir()
        repo = mirror(case, directed=forge_registry(committed_text(DIRECTED_ARTIFACT)))
        proc = run_gate(write_contract(repo, check), repo, *CLOSED_JSON)
        return report_of(proc)["results"][0]["passed"], proc.returncode

    assert run(strict, "strict") == (False, 1)
    assert run(lax, "lax") == (True, 0)


def test_pattern_layer_alone_would_not_catch_it(tmp_path: Path):
    """★ **归因取证**：同一伪造输入在"没有摘要字段"时**正则层判绿**.

    ★ 这条把"新字段真的在起作用"钉死：若正则层本来就会抓到这个伪造，上面那条
    rc=1 就不能归因到摘要层。

    ★ **用的是真契约里那条真 check**（``eval-directed-axis-frozen-reading``，
    逐字照抄），不是本文件合成的简化正则 —— 否则"正则层抓不到"这个结论只在
    一条**我自己编的**正则上成立，而它根本不是生产里跑的东西。这是本节唯一
    必要的严格性：归因结论只能由**被测的那个对象**给出。

    ★ 「改前」是**模拟**（共享工作树禁 checkout）：去掉字段即"#169 之前"的判定
    路径（缺省 ``None`` ⇒ 不校验），与父 spec §6.5 记录 ``parse_as`` 改前读数同法。
    """
    forged = forge_registry(committed_text(DIRECTED_ARTIFACT))
    real = json.loads(
        (REPO_ROOT / "config" / "drift-contract.json").read_text(encoding="utf-8")
    )
    check = next(c for c in real["checks"]
                 if c["id"] == "eval-directed-axis-frozen-reading")
    assert check.get("content_digest"), "真契约里这条 check 没有摘要 —— 本测试无意义"
    check = {k: v for k, v in check.items() if k != "content_digest"}  # ← #169 之前

    root = mirror(tmp_path, directed=forged)
    output = drift_gate.run_check_files(check, root)
    passed, detail = drift_gate.evaluate(check, output, "closed", root)
    assert passed is True, (
        f"真契约的正则层竟然抓到了伪造 —— 那么上面的 rc=1 不能归因到摘要层: {detail!r}"
    )


# --------------------------------------------------------------------------
# 4. 非法值 ⇒ rc=2（点名 check id + 非法值），绝不静默降级
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        "abc",                       # 太短
        "81C79BCDECE27CD1A69534C81297562170BB900EE5F5C77807051D026104609A",  # 大写
        "81c79bcdece27cd1a69534c81297562170bb900ee5f5c77807051d026104609",   # 63 位
        "81c79bcdece27cd1a69534c81297562170bb900ee5f5c77807051d026104609a0",  # 65 位
        "zzc79bcdece27cd1a69534c81297562170bb900ee5f5c77807051d026104609a",  # 非 hex
        True,
        1,
        "",
        " " * 64,
        [],
        {},
    ],
)
def test_invalid_value_is_a_meta_error(tmp_path: Path, bad_value: object):
    """★ 非法值 ⇒ rc=2，输出**点名 check id 与非法值**，绝不静默降级成"不校验".

    ★ 静默降级是这里最坏的失败形态：一个笔误（大写 / 少一位）若被当成"没声明"，
    门禁会**看起来在守内容摘要却完全没守** —— 而这层正是 W2 唯一的门禁侧守卫，
    它一静默失效，本票的核心 AC 就整体作废。

    ★ 连 ``True`` / ``1`` 都拒绝：JSON 里 ``true`` 与一个摘要字符串不是一回事，
    靠真值性猜意图正是漏判的来源（与既有两个字段同一条纪律）。
    """
    repo = mirror(tmp_path)
    contract = write_contract(repo, artifact_check(content_digest=bad_value))
    proc = run_gate(contract, repo, *CLOSED_JSON)

    assert proc.returncode == 2, (
        f"非法值 {bad_value!r} 应 rc=2（meta-error），实得 {proc.returncode}\n"
        f"stdout={proc.stdout[:300]!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "META-ERROR" in combined, combined[:300]
    assert "eval-digest-probe" in combined, f"报错没点名 check id: {combined[:300]}"
    assert repr(bad_value) in combined, f"报错没点名非法值: {combined[:300]}"
    assert "content_digest" in combined, f"报错没点名字段名: {combined[:300]}"
    assert_no_report(proc)


def test_invalid_value_does_not_fall_back_to_no_check(tmp_path: Path):
    """★ 非法值**不得**退化成一次绿灯运行（rc==2 而非 rc==0/1）.

    ★ 用一个**伪造的产物**当输入：若实现静默降级成"不校验"，正则层会命中
    （``criteria`` 那份没动）并给出 rc=0 的假绿。故这里断言 rc=2 且没有任何报告。
    """
    repo = mirror(tmp_path, directed=forge_registry(committed_text(DIRECTED_ARTIFACT)))
    contract = write_contract(repo, artifact_check(content_digest="NOT_A_DIGEST"))
    proc = run_gate(contract, repo, *CLOSED_JSON)

    assert proc.returncode == 2, (
        f"非法契约竟然给出了业务结论（rc={proc.returncode}）—— 静默降级了"
    )
    assert_no_report(proc)


def test_invalid_value_on_one_check_reds_the_whole_contract(tmp_path: Path):
    """★ 一条 check 写错 ⇒ 整份契约 rc=2（含合法 check 也不给结论）.

    ★ 理由：一份报告里若一半 check 有效、一半被静默跳过，"总共 N 项"这个数字
    本身就是假的。宁可整份 meta-error，也不给一个半真的账。
    """
    repo = mirror(tmp_path)
    good = artifact_check(id="eval-good",
                          content_digest=declared_digest_of(
                              "eval-directed-axis-frozen-reading"))
    bad = artifact_check(id="eval-bad", content_digest="nope")
    proc = run_gate(write_contract(repo, good, bad), repo, *CLOSED_JSON)
    assert proc.returncode == 2
    assert "eval-bad" in proc.stdout + proc.stderr
    assert_no_report(proc)


def test_evaluate_unit_level_rejects_an_invalid_value():
    """★ 第二道防线：绕过 ``load_contract`` 直接喂非法值 ⇒ ``ValueError``.

    ★ 与既有两个字段同一条纪律：CLI 路径已在 rc=2 挡住，但库调用方不能被静默放行。
    """
    with pytest.raises(ValueError, match="content_digest"):
        drift_gate.content_digest_of(artifact_check(content_digest="BAD"))


# --------------------------------------------------------------------------
# 5. 「恰好一个引用文件」是硬要求 ⇒ rc=2
# --------------------------------------------------------------------------


def test_digest_with_zero_paths_is_a_meta_error(tmp_path: Path):
    """★ ``paths: []`` + 摘要 ⇒ rc=2（一个没有对象的摘要恒真）.

    ★ 防的是"真空真"：一条没配置任何路径的摘要校验会变成**永远及格**的门禁条目 ——
    与 ``paths: []`` + ``present_when_missing="fail"`` 要杀的那类真空真是同一形态。
    """
    repo = mirror(tmp_path)
    contract = write_contract(repo, artifact_check(
        paths=[], content_digest="0" * EXPECTED_HEX_LENGTH))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    assert proc.returncode == 2, f"空 paths + 摘要应 rc=2，实得 {proc.returncode}"
    combined = proc.stdout + proc.stderr
    assert "META-ERROR" in combined
    assert "eval-digest-probe" in combined, f"没点名 check id: {combined[:300]}"
    assert_no_report(proc)


def test_digest_with_two_paths_is_a_meta_error(tmp_path: Path):
    """★ 多 ``paths`` + 摘要 ⇒ rc=2（"摘要算的是哪一份"没有答案）.

    ★ 为什么必须报错而不是"用第一个"：那会让**其余文件悄悄脱离校验** ——
    正是「守卫存在但它不在这条路径上」那个形态（本仓实测踩过：`evaluate` 曾因
    可选 `repo_root` 而整层静默消失）。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "a.json").write_text('{"x": 1}', encoding="utf-8")
    (repository / "b.json").write_text('{"y": 2}', encoding="utf-8")
    check = artifact_check(paths=["a.json", "b.json"],
                           pattern=r"x",
                           content_digest="0" * EXPECTED_HEX_LENGTH)
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    assert proc.returncode == 2, f"多 paths + 摘要应 rc=2，实得 {proc.returncode}"
    assert "META-ERROR" in proc.stdout + proc.stderr
    assert_no_report(proc)


def test_digest_on_a_single_path_is_accepted(tmp_path: Path):
    """★ 对照：**恰好一个** path 时合法（rc 由内容决定，不是 meta-error）."""
    repository = tmp_path / "repo"
    repository.mkdir()
    text = '{"x": 1}'
    (repository / "a.json").write_text(text, encoding="utf-8")
    check = artifact_check(
        paths=["a.json"], pattern=r'"x":\s*1',
        content_digest=eval_card_digest.canonical_digest(text))
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    assert proc.returncode == 0, proc.stdout[:300]


# --------------------------------------------------------------------------
# 6. ★ 反误伤：布局重写不得判红（摘要的构造性性质）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "render"),
    [
        ("sort_keys + indent=4",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=4,
                              ensure_ascii=False) + "\n"),
        ("sort_keys + indent=2",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=2,
                              ensure_ascii=False) + "\n"),
        ("CRLF 行尾",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=2,
                              ensure_ascii=False).replace("\n", "\r\n") + "\r\n"),
        ("键序反转（原缩进）",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=2,
                              ensure_ascii=False) + "\n"),
    ],
)
def test_layout_only_rewrite_does_not_red(tmp_path: Path, label: str, render):
    """★★ **反误伤**：值逐字不变、只重排 / 重缩进 / 换行尾 ⇒ 摘要**必须**不变.

    ★ 这是「合法重写不得被误判红」的反向保护，镜像既有
    ``test_value_equivalent_spelling_does_not_false_red`` 的纪律。
    ★ 理由具体（父 spec §3.3.2 的 t7 V4）：旧的契约门禁曾把
    ``sort_keys + indent=4`` 重写**误判红**（值逐字不变）—— 那种守卫会阻碍
    格式化 / 换工具链，人最终会把它拆掉。摘要层**在构造上**不会犯这个错
    （``sort_keys`` + ``separators`` + ``json.loads``），本测试就是它的取证。

    ⚠️ 注意**不含**"冒号后不留空格的紧凑渲染"：那一条会因**正则层**（先于本票存在）
    判红，与摘要层无关 —— 已由 `test_eval_gate_contract.py` 的
    ``test_compact_colon_only_rendering_is_a_pre_existing_regex_limit`` 单独登记。
    """
    original = committed_text(DIRECTED_ARTIFACT)
    rewritten = render(original)
    assert rewritten != original, f"{label}: 重写未生效"
    assert json.loads(rewritten) == json.loads(original), f"{label}: 重写改变了 JSON 值"
    assert eval_card_digest.canonical_digest(rewritten) == \
        eval_card_digest.canonical_digest(original), (
        f"{label}: 摘要变了 —— 摘要对排版敏感，会误伤合法重写"
    )

    repo = mirror(tmp_path, directed=rewritten)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    result = report_of(proc)["results"][0]
    assert result["passed"] is True, f"{label}: 合法重排被门禁误判红: {result['detail']!r}"
    assert proc.returncode == 0


def test_real_value_change_still_reds(tmp_path: Path):
    """★ 配对：**真**改一个叶子（不是排版）必须判红，否则摘要恒真."""
    original = committed_text(DIRECTED_ARTIFACT)
    doc = json.loads(original)
    card = doc["cards"][V1]
    metric = card["overall"]["cost_index"]
    metric["median"] = float(metric["median"]) + 1.0
    mutated = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    assert eval_card_digest.canonical_digest(mutated) != \
        eval_card_digest.canonical_digest(original)

    repo = mirror(tmp_path, directed=mutated)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    assert proc.returncode == 1, (
        f"改动了 overall.cost_index.median 却判绿（rc={proc.returncode}）"
    )


# --------------------------------------------------------------------------
# 7. 三层互不覆盖：缺失 / 不可解析仍由各自那层处置，措辞不变
# --------------------------------------------------------------------------


def test_missing_file_is_still_handled_by_present_when_missing(tmp_path: Path):
    """★ 三层分工：文件**缺失**时仍走 ``present_when_missing`` 的分支，措辞不变.

    ★ 本改动是"在它之上加一层"，不是替换它。缺失场景的 detail 仍须是
    「引用的结果文件全部缺失」那句，而**不是**被摘要层抢走。
    ★ 同时也断言 detail 里**没有**摘要层的措辞 —— 两句话必须能区分
    （缺产物该去生成、内容变了该去查漂移，处置完全不同）。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    check = artifact_check(
        paths=["never/created.json"],
        pattern=r"whatever",
        content_digest="0" * EXPECTED_HEX_LENGTH,
    )
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is False
    assert proc.returncode == 1
    assert "引用的结果文件全部缺失" in result["detail"], (
        f"缺失场景被摘要层抢走了（应仍由 present_when_missing 处置）: {result['detail']!r}"
    )
    assert "内容摘要不符" not in result["detail"], (
        f"缺失场景被写成了摘要不符 —— 两种情形处置不同，必须可区分: {result['detail']!r}"
    )


def test_unparsable_content_is_still_handled_by_parse_as(tmp_path: Path):
    """★ 三层分工：内容**不可解析**时仍走 ``parse_as`` 的分支，措辞不变.

    ★ 顺序刻意是 ``parse_as`` **先于** ``content_digest``：内容根本解析不了时，
    报"解析失败"比报"摘要不符"更准确（前者才是根因）。本测试钉住这个顺序 ——
    否则读报告的人会以为产物"只是被改了"，而实际上它是**半截**的。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "card.json").write_text('{"verdict": "pass", "truncated:',
                                          encoding="utf-8")
    check = eval_check_like(paths=["card.json"], pattern=r'"verdict":\s*"pass"',
                            content_digest="0" * EXPECTED_HEX_LENGTH)
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is False
    assert proc.returncode == 1
    assert "不可解析" in result["detail"], (
        f"不可解析场景没走 parse_as 层: {result['detail']!r}"
    )
    assert "内容摘要不符" not in result["detail"], (
        f"不可解析场景被摘要层抢走了（根因是解析失败，不是内容被改）: {result['detail']!r}"
    )


def test_digest_on_unparsable_content_still_reds_without_parse_as(tmp_path: Path):
    """★★ **声明了摘要却算不出来 ⇒ 判红**（不是静默跳过）.

    ★ 这条单独覆盖一种容易写错的实现：只在"能算出实际摘要"时比对，
    ``canonical_digest`` 抛异常就 `continue`。那会让**最该守住的那条路径**
    （内容坏到算不出摘要）静默放手 —— 与 ``parse_as`` 非法值降级成 `null` 同一种病。
    ★ 场景：该 check 声明了摘要但**没写** `parse_as`（真实契约里两者都有，
    但字段各自独立，必须各自成立）。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "card.json").write_text('{"verdict": "pass", "truncated:',
                                          encoding="utf-8")
    check = eval_check_like(pattern=r'"verdict":\s*"pass"',
                            content_digest="0" * EXPECTED_HEX_LENGTH)
    check.pop("parse_as", None)  # ← 刻意不要 parse_as：本用例只测摘要层自己
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is False, (
        f"声明了摘要但内容算不出摘要时判绿了（静默跳过）: {result['detail']!r}"
    )
    assert proc.returncode == 1
    assert "无法**被核验" in result["detail"] or "无法" in result["detail"], (
        f"detail 没说清『摘要无法被核验』: {result['detail']!r}"
    )


# --------------------------------------------------------------------------
# 8. severity / mode 语义不变（新字段只决定 passed，不决定是否阻断）
# --------------------------------------------------------------------------


def test_warn_severity_does_not_block(tmp_path: Path):
    """★ ``severity=warn`` + 摘要不符 ⇒ rc=0，但仍报 ``passed=False``.

    ★ 新字段只决定「内容不符算不算没过」，**不**决定「没过算不算阻断」——
    后者一直由 severity + mode 决定。若这里 rc=1，就等于给这个字段偷偷加了
    第二个开关，既有 severity 语义被绕过。
    """
    repo = mirror(tmp_path, directed=forge_registry(committed_text(DIRECTED_ARTIFACT)))
    contract = write_contract(repo, artifact_check(
        severity="warn",
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, *CLOSED_JSON)
    report = report_of(proc)

    assert report["results"][0]["passed"] is False
    assert proc.returncode == 0, f"severity=warn 不得阻断，实得 rc={proc.returncode}"
    assert report["block_failures"] == 0
    assert report["warn_failures"] == 1
    assert "[WARN]" in report["results"][0]["detail"]


def test_open_mode_does_not_block_but_still_reports_false(tmp_path: Path):
    """★ ``mode=open`` + 摘要不符 ⇒ rc=0，但 ``passed=False`` 必须留下.

    ★ 「不阻断」与「没通过」是两件事。open 模式 rc 恒 0 是既有退出码契约
    （不许改），但报告里必须留下 ``passed=False`` —— 否则 open 模式会变成
    又一个"绿即通过"的错觉来源。
    """
    repo = mirror(tmp_path, directed=forge_registry(committed_text(DIRECTED_ARTIFACT)))
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    proc = run_gate(contract, repo, "--phase", "static", "--mode", "open", "--json")
    report = report_of(proc)

    assert report["results"][0]["passed"] is False
    assert proc.returncode == 0
    assert report["block_failures"] == 1, "block_failures 是『不符几条』，与 mode 无关"
    assert report["any_block_fail"] is False, "any_block_fail 含 mode 条件，不得改"


# --------------------------------------------------------------------------
# 9. 报告契约：既有字段零改动，新字段**附加**
# --------------------------------------------------------------------------


def test_json_report_keeps_every_preexisting_field(tmp_path: Path):
    """★ ``--json`` 报告字段名与语义零改动，只允许**新增**.

    ★ ``drift_gate_smoke_test.py`` 与 CI 都读这些字段；改名字会让它们静默失准。
    """
    repo = mirror(tmp_path)
    contract = write_contract(repo, artifact_check(
        content_digest=declared_digest_of("eval-directed-axis-frozen-reading")))
    report = report_of(run_gate(contract, repo, *CLOSED_JSON))

    for key in (
        "ran_at", "phase", "mode", "source_of_truth", "contract_version",
        "total_checks", "block_failures", "warn_failures", "results", "any_block_fail",
    ):
        assert key in report, f"既有报告字段 {key!r} 丢了"
    for key in (
        "id", "phase", "severity", "decision_ref", "description",
        "expected_regex", "paths", "passed", "detail",
        "present_when_missing", "parse_as",
    ):
        assert key in report["results"][0], f"既有 result 字段 {key!r} 丢了"


def test_content_digest_is_added_to_every_result(tmp_path: Path):
    """新增字段出现在**每个** result 里（读数的人不必再回契约文件对照）.

    ★ 缺省时必须是 ``None``（而不是键缺失）：下游要能区分"看过并决定不校验"
    与"这个字段还没接上"（与 ``present_when_missing`` 归一化成 ``"skip"`` 同理）。
    """
    repo = mirror(tmp_path)
    contract = write_contract(
        repo,
        artifact_check(id="eval-default"),
        artifact_check(id="eval-digest",
                       content_digest=declared_digest_of(
                           "eval-directed-axis-frozen-reading")),
    )
    report = report_of(run_gate(contract, repo, *CLOSED_JSON))
    assert [r["content_digest"] for r in report["results"]] == [
        None, declared_digest_of("eval-directed-axis-frozen-reading")
    ]


def test_omitted_field_is_byte_identical_to_explicit_null(tmp_path: Path):
    """★ 不写该字段 ⇒ 与显式 ``null`` **逐字段一致**（既有 11 条的保护）.

    ★ 既有 11 条 check 一条都没写这个键，故它们全部走这条路径。这里断言 JSON
    报告的 ``results[0]`` 在两种写法下**完全相同** —— 包括新字段（缺省时它必须
    被归一化成 ``None`` 而不是缺失）。
    """
    omitted = tmp_path / "omitted"
    explicit = tmp_path / "explicit"
    omitted.mkdir()
    explicit.mkdir()
    a = report_of(run_gate(
        write_contract(mirror(omitted), artifact_check()), omitted / "mirror",
        *CLOSED_JSON))["results"][0]
    b = report_of(run_gate(
        write_contract(mirror(explicit), artifact_check(content_digest=None)),
        explicit / "mirror", *CLOSED_JSON))["results"][0]
    assert a == b, f"缺省与显式 null 的 result 不一致：\n  缺省={a}\n  显式={b}"
    assert a["content_digest"] is None


# --------------------------------------------------------------------------
# 10. ★ 真契约：接线的**精确**清单（多一条少一条都判红）
# --------------------------------------------------------------------------


def test_real_contract_declares_digest_on_exactly_the_eval_checks():
    """★ 真契约里 **4 条 eval-\\*** 声明 ``content_digest``，其余一条都没有.

    ★ 断言**精确相等**而不是"至少有一条" —— 后者会让"顺手给既有 check 也加上"
    这种改动静默通过，而那会把 CI 全红（既有 check 引用的源码/配置每改一次
    摘要就变，门禁会被判死然后拆掉）。

    ★ 「值必须是**产物今天的**摘要」由 `test_eval_gate_contract.py` 的
    ``test_declared_digest_is_grounded_in_the_real_artifact`` 负责；
    本测试只管**接线清单**（谁声明了、谁没声明）。
    """
    checks = json.loads(
        (REPO_ROOT / "config" / "drift-contract.json").read_text(encoding="utf-8")
    )["checks"]
    with_field = [c["id"] for c in checks if "content_digest" in c]
    assert with_field == list(EVAL_CHECK_IDS), (
        f"声明 content_digest 的 check 与预期不符：\n"
        f"  实际={with_field}\n  预期={list(EVAL_CHECK_IDS)}\n"
        "多一条少一条都是行为变化：新增要在此登记并说明它读的是不是入库产物；"
        "减少则说明某个评测门禁又失去了内容摘要守卫（同源副本被改会判绿）。"
    )
    for check in checks:
        if check["id"] in EVAL_CHECK_IDS:
            assert eval_card_digest.is_legal_digest(check["content_digest"]), (
                f"{check['id']} 的 content_digest 形状非法: {check['content_digest']!r}"
            )
            assert check["content_digest"] != "0" * EXPECTED_HEX_LENGTH, (
                f"{check['id']} 的摘要是全零占位符 —— 那是「看起来填了」的假值"
            )
        else:
            assert "content_digest" not in check, (
                f"既有 check {check['id']} 被加了 content_digest —— "
                "它引用的不是记分卡产物，会让每次源码改动都判红"
            )
            assert drift_gate.content_digest_of(check) is None


def test_baseline_checks_are_unaffected_by_the_digest_layer(tmp_path: Path):
    """★ AC：**未声明**摘要的 check 行为零变更（既有 11 条的保护）.

    ★ 用一个**非 JSON** 的内容喂给一条不带该字段的 check，断言它照旧按 pattern
    判定（命中即通过）—— 即摘要层没有"顺手"作用到它们身上。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "env.txt").write_text("MAIN_CONTEXT=16384\n", encoding="utf-8")
    check = eval_check_like(
        id="baseline-like", paths=["env.txt"], pattern=r"^MAIN_CONTEXT=16384",
        present_when_missing="skip",
    )
    check.pop("parse_as", None)
    proc = run_gate(write_contract(repository, check), repository, *CLOSED_JSON)
    result = report_of(proc)["results"][0]

    assert result["passed"] is True, (
        f"非 JSON 的既有形态 check 被摘要层误伤: {result['detail']!r}"
    )
    assert proc.returncode == 0
    assert result["content_digest"] is None


def test_every_eval_check_digest_agrees_with_its_axis_artifact():
    """★ 同一轴的两条 check 必须声明**同一个**摘要，且等于该轴产物的实际摘要.

    ★ 同一张卡的摘要若在两条 check 上不一致，说明有人只改了一处 —— 那正是
    「同一事实两份副本」的复活形态（本票要消灭的那一类）。
    """
    for cid, axis in AXIS_BY_CHECK_ID.items():
        rel = DIRECTED_ARTIFACT if axis == "directed" else TIMING_ARTIFACT
        actual = eval_card_digest.canonical_digest(committed_text(rel))
        assert declared_digest_of(cid) == actual, (
            f"{cid} 声明的摘要与 {rel} 的实际内容不符"
        )
    assert BASELINE_CHECK_IDS, "既有清单必须仍然被引用（本文件不重复它的字面量）"
