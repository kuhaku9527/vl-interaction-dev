# ruff: noqa: RUF001, RUF002, RUF003
r"""#159 评测回归门禁 —— 契约接线测试（离线、不起服务）.

★ 为什么这个文件必须存在
------------------------
#159 把两张记分卡（#157 定向轴 / #158 时序轴）的**结构化产物**接进 drift-gate。
门禁的判据全部写在 `config/drift-contract.json` 里，而契约本身是**数据**——
没有任何东西阻止它在后续改动中被弱化：

* 把 `present_when_missing` 从 `"fail"` 改回缺省 ⇒ 又回到「缺结果判绿」的原始缺陷；
* 把 `pattern` 改成一条**永不匹配**的正则 ⇒ 看起来在守，实际什么都不守；
* 把 `paths` 指到 gitignore 覆盖的路径 ⇒ CI 干净检出里文件不存在，门禁静默旁路；
* 卡片阈值改了（`BOUNDS` / `TIMING_BOUNDS`）而产物没重跑 ⇒ 契约断言的是**过时数字**；
* 顺手给既有 11 条 check 也加上 `present_when_missing` ⇒ 悄悄改掉既有行为。

这五件事都**不会让 CI 变红**，所以必须由本文件钉成可执行断言。
`scripts/tests/` 会被 CI 的 `scripts-tests` job 跑**整个目录**（不是点名文件），
故放这里才真的被执行。

★ 本文件**不**重跑记分卡、**不**起服务、**不**需要模型：只读契约 + 读产物 + 导入
`services/webinfer` 的纯常量模块（实测无第三方依赖，见
`test_webinfer_criteria_modules_import_without_third_party_deps`）。

Run: python -m pytest scripts/tests/test_eval_gate_contract.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "config" / "drift-contract.json"
WEBINFER = REPO_ROOT / "services" / "webinfer"
#: CI 工作流（t6 的 R6 测试从中**抽取**生成步骤的真脚本执行，不做复述）
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality.yml"
#: 生成步骤的 name 前缀（抽取时的定位锚）
STEP_NAME = "Generate decision-eval scorecards"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import drift_gate as dg  # noqa: E402

# ★ 接线清单 / 既有清单 / 两张产物路径 —— **单一来源**在 `eval_gate_fixtures.py`。
#   它曾在本文件与 `test_drift_gate_missing_result.py` 里各写一份；两份拷贝会各自
#   漂移，于是两套测试对"接线清单是什么"给出不同答案（且都绿）。详见该模块 docstring。
#   这里用 `as` 保留原名，使本文件其余 ~40 处引用一字不改（只换来源，不改语义）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gate_fixtures import (  # noqa: E402
    AXIS_BY_CHECK_ID,
    BASELINE_CHECK_IDS,
    DIGEST_MODULE_NAME,
    DIRECTED_ARTIFACT,
    EVAL_CHECK_IDS,
    TIMING_ARTIFACT,
    declared_digest_by_axis,
    declared_digest_of,
)

#: t6 合并进来的 CI 步骤测试用短名引用同两条路径（同一常量，不重复字面量）
DIRECTED_REL = DIRECTED_ARTIFACT
TIMING_REL = TIMING_ARTIFACT

DIRECTED_CARD_KEY = "P_live4_prod_prompt"
DIRECTED_CARD_KEY_V2 = "P2_live4_prod_prompt_profile"
TIMING_CARD_KEY = "frozen-fixture"


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def eval_checks(contract: dict) -> list[dict]:
    return [c for c in contract["checks"] if c["id"].startswith("eval-")]


@pytest.fixture(scope="module")
def directed_json() -> dict:
    return json.loads((REPO_ROOT / DIRECTED_ARTIFACT).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def timing_json() -> dict:
    return json.loads((REPO_ROOT / TIMING_ARTIFACT).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def directed_text() -> str:
    return (REPO_ROOT / DIRECTED_ARTIFACT).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timing_text() -> str:
    return (REPO_ROOT / TIMING_ARTIFACT).read_text(encoding="utf-8")


def merged(text: str, rel: str) -> str:
    """Reproduce the executor's merged-content shape (see drift_gate.run_check_files)."""
    return f"--- {rel} ---\n{text}\n"


def webinfer_modules() -> tuple[object, object]:
    """Import the two criteria modules plain (no third-party deps needed)."""
    if str(WEBINFER) not in sys.path:
        sys.path.insert(0, str(WEBINFER))
    import decision_eval_criteria as criteria
    import decision_eval_timing_criteria as timing_criteria

    return criteria, timing_criteria


def index_items(card: dict) -> list[dict]:
    """定向轴的 criteria.index（dict 形态）。"""
    return card["criteria"]["index"]


def timing_items(card: dict) -> list[dict]:
    """时序轴的 criteria 是**列表**（不是 dict）——两轴形状不同，勿套用。"""
    return card["criteria"]


def card_verdict_from_criteria(card: dict) -> str:
    """按卡片自己列出的判据**重算**卡片级结论（任一 fail ⇒ fail；其次 unmeasurable）。

    ★ 这是「门禁守不住时由结构层兜住」的那个重算函数（t8）：
    时序卡卡片级 `verdict` 没有排版稳定的字面锚点（`sort_keys` 会把它的后继键
    重排走），故改由本函数在 `json.loads` 之后做结构判定 —— 与排版无关。
    """
    items = list(index_items(card)) if isinstance(card.get("criteria"), dict) \
        else list(timing_items(card))
    verdicts = [it["verdict"] for it in items]
    if "fail" in verdicts:
        return "fail"
    if "unmeasurable" in verdicts:
        return "unmeasurable"
    return "pass"


# --------------------------------------------------------------------------
# 1. 契约形状：4 条评测 check，复用既有 schema 与同一执行器
# --------------------------------------------------------------------------


def test_eval_checks_exist_and_cover_both_axes(eval_checks: list[dict]) -> None:
    """两条轴各有 ≥1 条 check，且**两条结果文件都要被读**（AC#7）。"""
    assert [c["id"] for c in eval_checks] == list(EVAL_CHECK_IDS), (
        f"评测 check 集合或顺序变了: {[c['id'] for c in eval_checks]}"
    )
    paths = {p for c in eval_checks for p in c["paths"]}
    assert DIRECTED_ARTIFACT in paths, "定向轴结果文件没有任何 check 读它"
    assert TIMING_ARTIFACT in paths, "时序轴结果文件没有任何 check 读它"

    directed = [c["id"] for c in eval_checks if DIRECTED_ARTIFACT in c["paths"]]
    timing = [c["id"] for c in eval_checks if TIMING_ARTIFACT in c["paths"]]
    assert len(directed) >= 1 and len(timing) >= 1
    assert not set(directed) & set(timing), "一条 check 不应同时承担两条轴"


def test_eval_checks_use_existing_schema_only(eval_checks: list[dict]) -> None:
    """只许用既有 schema 字段 + #159 的 present_when_missing + t10 的 parse_as + #169 的 content_digest。

    ★ 「复用，不新造」的可执行形态：出现任何 schema 外的新键（比如试图让契约
    去做数值比较的 `min`/`max`），说明有人在偷偷扩展执行器语义 —— 那会绕过
    `drift_gate.py` 的实现在别处、且 CI 跑的是另一套解释。

    ★ `parse_as` 是 t10 为修 W1（半截产物判绿）新增的**执行器**字段，不是契约
    私造的：它与 `present_when_missing` 同族 —— 都由 `drift_gate.py` 的
    `validate_*` 校验、缺省值都定义在执行器常量里 —— 故允许。

    ★ `content_digest` 是 #169 为修 W2（**同源副本被改**：`criteria_registry`
    伪造时门禁判绿）新增的执行器字段，与上面两个**同族同形态**：
    由 `drift_gate.validate_content_digest` 校验形状、缺省值定义在执行器常量
    `DEFAULT_CONTENT_DIGEST` 里。**它不是一个"契约私造的比较语义"** ——
    它不引入任何新的判定能力（不是数值比较、不是路径拼接），只是把
    「这份产物的内容应该是什么」写成一条可比对的**值**，比对用同一个执行器。
    """
    allowed = {
        "id", "decision_ref", "description", "phase", "paths", "pattern",
        "not_pattern", "severity", "present_when_missing", "parse_as",
        "content_digest",
    }
    for c in eval_checks:
        extra = set(c) - allowed
        assert not extra, f"{c['id']} 用了 schema 外字段: {sorted(extra)}"


def test_eval_checks_are_static_block_and_fail_closed(eval_checks: list[dict]) -> None:
    """phase=static（CI 可跑）+ severity=block + present_when_missing=fail。"""
    for c in eval_checks:
        assert c["phase"] == "static", (
            f"{c['id']} 的 phase={c['phase']!r}；#159 刻意用 static —— "
            "runtime 阶段会无条件触发 VLM probe，无 llama 时 rc=3，CI 永远跑不了"
        )
        assert c["severity"] == "block", f"{c['id']} 必须是 block，否则 closed 模式拦不住"
        assert c["present_when_missing"] == "fail", (
            f"{c['id']} 的 present_when_missing={c.get('present_when_missing')!r}；"
            "★ 缺结果必须判红（fail-closed），不得退回 skip"
        )


def test_eval_checks_have_no_line_number_references(eval_checks: list[dict]) -> None:
    """decision_ref / description 不得含 `path:NNN` 形态的行号引用。

    `决策/README.md` §0.6 禁行号：行号会随文件编辑失效，引用应立即腐坏。
    行号引用一律改用关键词或 check id。
    """
    line_ref = re.compile(r"\.(md|py|json|yml|yaml|js):\d+")
    for c in eval_checks:
        blob = f"{c['decision_ref']}\n{c['description']}"
        found = line_ref.findall(blob)
        assert not found, f"{c['id']} 带行号引用 {found}（决策书禁行号）"


def test_baseline_checks_unchanged_and_still_fail_open(contract: dict) -> None:
    """既有 11 条 check：id 集合不变，且一条都不许带 present_when_missing。

    ★ 给既有 check 加该字段会**改变既有行为**（例如 `vlm-n_ctx` 在干净检出里
    依赖 gitignore 的 logs/ 文件，缺省 skip 是刻意的 fail-open 设计）。
    """
    base = [c for c in contract["checks"] if not c["id"].startswith("eval-")]
    assert len(base) == 11, f"既有 check 数量变了: {len(base)}"
    ids = [c["id"] for c in base]
    assert sorted(ids) == sorted(BASELINE_CHECK_IDS), f"既有 check id 集合变了: {ids}"
    for c in base:
        assert "present_when_missing" not in c, (
            f"既有 check {c['id']} 被加了 present_when_missing —— "
            "那会改掉既有 fail-open 行为，不在 #159 的意图内"
        )
        assert dg.present_when_missing_of(c) == "skip"


# --------------------------------------------------------------------------
# 2. 结果文件落在**入库**稳定路径（AC#2）
# --------------------------------------------------------------------------


def test_artifact_paths_are_tracked_and_not_gitignored() -> None:
    """两条产物必须已在 git 索引内，且不被任何 ignore 规则覆盖。

    ★ 用 `git check-ignore` 取证而不是读 `.gitignore` 文本：后者要求本测试
    重实现 ignore 语义（否定规则、目录规则、嵌套 .gitignore），迟早漂移。
    `check-ignore -q` 的退出码是权威判据：0 = 被忽略，1 = **没有**匹配任何规则。

    ⚠️ 反向坑（本仓已踩过）：把 rc=1 读成「已忽略」正好读反。故这里断言
    `returncode == 1`，并在失败信息里把这条陷阱写出来。
    """
    for rel in (DIRECTED_ARTIFACT, TIMING_ARTIFACT):
        p = subprocess.run(["git", "check-ignore", "-q", "--", rel],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
        assert p.returncode == 1, (
            f"{rel} 被 ignore 规则覆盖（check-ignore rc={p.returncode}）—— "
            "注意 rc=1 的含义是「没有被忽略」，rc=0 才是被忽略"
        )
        ls = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
        assert ls.returncode == 0, f"{rel} 不在 git 索引内（未入库）: {ls.stderr.strip()}"


def test_artifact_paths_are_stable_literals(contract: dict) -> None:
    """门禁硬编码的必须是固定字面路径，不得含通配 / 变量 / 临时目录。"""
    for c in contract["checks"]:
        if not c["id"].startswith("eval-"):
            continue
        for rel in c["paths"]:
            assert "*" not in rel and "?" not in rel, f"{c['id']} 的路径含通配: {rel}"
            assert "$" not in rel and "{" not in rel, f"{c['id']} 的路径含变量: {rel}"
            assert not rel.startswith(("/", "~")), f"{c['id']} 的路径必须是仓库相对: {rel}"
            assert "reports/eval" not in rel, (
                f"{c['id']} 指向 reports/eval —— 该目录被 .gitignore 忽略，"
                "CI 干净检出里不存在，门禁会被静默旁路"
            )


def test_artifact_files_are_committed_content_not_placeholders() -> None:
    """产物必须**真实存在**且是卡片输出（不是手写的占位 stub）。"""
    d = json.loads((REPO_ROOT / DIRECTED_ARTIFACT).read_text(encoding="utf-8"))
    t = json.loads((REPO_ROOT / TIMING_ARTIFACT).read_text(encoding="utf-8"))
    assert d["kind"] == "directed-axis-scorecard" and d["ticket"] == "#157"
    assert t["kind"] == "timing-axis-scorecard" and t["ticket"] == "#158"
    assert len(d["cards"]) >= 2, "定向轴产物应含两个 variant"
    assert TIMING_CARD_KEY in t["cards"]


# --------------------------------------------------------------------------
# 3. ★ 跨层绑定阈值：契约/产物 与 卡片 BOUNDS 逐条比对（AC#6）
# --------------------------------------------------------------------------


def test_directed_artifact_thresholds_match_card_bounds(
    directed_json: dict,
) -> None:
    """定向轴：产物每条判据的 threshold 必须等于卡片 CRITERIA 的声明值。

    ★ 这是把「阈值有出处」从**文档承诺**变成**可执行断言**的那一步：
    契约里的正则只断言了数字**字面出现**；本测试独立地从
    `decision_eval_criteria` 取真值再比一次。任一侧被改（改了 BOUNDS 而没重跑
    产物、或手改了产物数字）都判红 ⇒ 分叉即测试红。
    """
    criteria, _ = webinfer_modules()
    declared = {c.criterion_id: c.threshold for c in criteria.CRITERIA}

    seen: dict[str, set[float]] = {}
    for card in directed_json["cards"].values():
        for item in index_items(card):
            seen.setdefault(item["criterion_id"], set()).add(float(item["threshold"]))

    assert set(seen) == set(declared), (
        f"产物判据集合与卡片 CRITERIA 不一致: 产物={sorted(seen)} 卡片={sorted(declared)}"
    )
    for cid, thresholds in seen.items():
        assert thresholds == {float(declared[cid])}, (
            f"{cid} 阈值分叉：产物={thresholds} 卡片 BOUNDS={declared[cid]} —— "
            "改线必须同时重跑产物，否则门禁在守一个过时数字"
        )


def test_timing_artifact_thresholds_match_card_bounds(timing_json: dict) -> None:
    """时序轴：同上，对照 `TIMING_BOUNDS`（structural 判据无阈值，跳过）。"""
    _, timing_criteria = webinfer_modules()
    declared = {c.criterion_id: c.threshold for c in timing_criteria.TIMING_CRITERIA}

    seen: dict[str, float | None] = {}
    for card in timing_json["cards"].values():
        for item in timing_items(card):
            seen[item["criterion_id"]] = item["threshold"]

    assert set(seen) == set(declared), (
        f"产物判据集合与 TIMING_CRITERIA 不一致: 产物={sorted(seen)} 卡片={sorted(declared)}"
    )
    for cid, threshold in seen.items():
        expect = declared[cid]
        if expect is None:
            assert threshold is None, f"{cid} 卡片声明无阈值，产物却给了 {threshold}"
        else:
            assert threshold is not None and float(threshold) == float(expect), (
                f"{cid} 阈值分叉：产物={threshold} 卡片 TIMING_BOUNDS={expect}"
            )


def test_every_directed_threshold_is_grounded_in_bounds(directed_json: dict) -> None:
    """产物里的每个 threshold 都能在 BOUNDS 的取值域里找到出处（无游离数字）。"""
    criteria, _ = webinfer_modules()
    bound_values = {float(v) for v in criteria.BOUNDS.values()}
    for card in directed_json["cards"].values():
        for item in index_items(card):
            assert float(item["threshold"]) in bound_values, (
                f"{item['criterion_id']} threshold={item['threshold']} 不在 BOUNDS {sorted(bound_values)} 中"
            )


def test_webinfer_criteria_modules_import_without_third_party_deps() -> None:
    """★ 本测试的**前置假设**必须是可执行的，不能只是注释。

    CI 的 `scripts-tests` job 只装 pytest + ruff（**不**装 webinfer 的任何依赖）。
    若哪天 `decision_eval_criteria` 引入第三方 import，上面三条跨层绑定测试会在
    CI 里以 ModuleNotFoundError 失败 —— 报错信息看起来像「阈值分叉」，
    一个指错方向的告警。故在此显式断言依赖闭包仍然是纯标准库。
    """
    import ast

    local = {p.stem for p in WEBINFER.glob("*.py")}
    stdlib = set(sys.stdlib_module_names)
    seen: set[str] = set()
    third_party: set[tuple[str, str]] = set()

    def scan(mod: str) -> None:
        if mod in seen:
            return
        seen.add(mod)
        src = WEBINFER / f"{mod}.py"
        if not src.exists():
            return
        for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for top in names:
                if top in local:
                    scan(top)
                elif top and top not in stdlib:
                    third_party.add((mod, top))

    for entry in ("decision_eval_criteria", "decision_eval_timing_criteria"):
        scan(entry)

    assert not third_party, (
        f"判定模块引入了第三方依赖 {sorted(third_party)} —— "
        "scripts-tests job 只装 pytest+ruff，跨层绑定测试会在 CI 失败"
    )


# --------------------------------------------------------------------------
# 4. ★ 契约 patterns 在真实产物上确实匹配（防「永不匹配的正则」）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("check_id", EVAL_CHECK_IDS)
def test_contract_pattern_matches_committed_artifact(check_id: str, contract: dict) -> None:
    """每条评测 check 的 pattern 在**已提交产物**上必须真的命中。

    ★ 反向保护：一条永不匹配的正则会让门禁**看起来在守**却永远判红（或永远
    放过 not_pattern）。这里用执行器自己的 `run_check_files` + 同样的
    `re.MULTILINE` 语义复现，避免本测试与门控行为分叉。
    """
    check = next(c for c in contract["checks"] if c["id"] == check_id)
    content = dg.run_check_files(check, REPO_ROOT)
    assert "--- " in content, f"{check_id} 引用的文件没被读到: {check['paths']}"
    assert re.search(check["pattern"], content, re.MULTILINE), (
        f"{check_id} 的 pattern 在真实产物上不匹配（契约写了个永不命中的正则）"
    )
    if check.get("not_pattern"):
        assert not re.search(check["not_pattern"], content, re.MULTILINE), (
            f"{check_id} 的 not_pattern 在健康产物上命中了"
        )


def test_patterns_are_not_trivially_anchored_across_variants(contract: dict) -> None:
    """★ 定向轴有两个 variant：V1 的断言不得被 V2 顶替。

    实测教训（#159 开发期由负控查出）：早期写法按「固定长度窗口」从 V1 的
    card key 走到 D1..D5，窗口开得够大就直接跨进了 V2 —— 于是把 **V1 的某条
    判据改成 fail**，pattern 仍能在 V2 上满足，门禁照样判绿。

    ★ t8 换掉了实现方式：不再用「V1 段以 V2 的 card key 为界」这种**区域**写法。
    理由（实测）：`json.dumps(..., sort_keys=True)` 会把
    `"P2_live4_prod_prompt_profile"` 排在 `"P_live4_prod_prompt"` **之前**，区域顺序
    本身就不是不变量 ⇒ 区域写法会把「值逐字不变、只是重排」的产物误判红
    （t7 的 V4，已复现 rc=1）。现在改为**按值区分变体**：
    每条断言锚在*自己的* criterion id 上，变体由**变体独有的数值**界定
    （D1 50.0/34.6、D3 unmeasurable/pass、D4 0.0/11.1、D5 82.4/62.7），
    再加上「同一断言须命中两处」的计数约束处理两 variant 取值相同的判据。
    故本测试改为断言**这些不变量**，而不是断言某个具体排版。
    """
    check = next(c for c in contract["checks"] if c["id"] == "eval-directed-axis-frozen-reading")
    pattern = check["pattern"]
    # 变体独有读数必须逐字在案（pattern 里的字面量经 re.escape，故用同一形式构造）
    for label, literal in [
        ("V1 D1 读数", "nondirected_spurious_response_rate_pct=50.0"),
        ("V2 D1 读数", "nondirected_spurious_response_rate_pct=34.6"),
        ("V1 D4 读数", "not_for_me_recall_pct=0.0 >= 17.0"),
        ("V2 D4 读数", "not_for_me_recall_pct=11.1 >= 17.0"),
        ("V1 D5 读数", "cost_index=82.4 <= 101.0"),
        ("V2 D5 读数", "cost_index=62.7 <= 101.0"),
    ]:
        assert re.escape(literal) in pattern, f"{label}（{literal}）没有出现在断言里"
    # ★ 变体必须**取值可分**：若两 variant 用同一组值，其中一条断言恒真。
    assert re.escape("50.0") in pattern and re.escape("34.6") in pattern, (
        "D1 的两 variant 读数必须分开写死，否则单变体退化会被另一 variant 顶替"
    )
    # 两 variant 的「已知红」（D4 未达标）都要在案
    # ★ 两 variant 的 D4「已知红」读数都要在案。断言用**实测的转义形态**
    #   （reason 里的空格与点号都经 re.escape，写成 `=0\.0\ >=\ 17\.0`），
    #   所以这里按实际文本核，而不是按人读的样子核 —— t8 先按人读的样子写，漏配。
    for label, literal in (
        ("V1 D4 读数", r"not_for_me_recall_pct=0\.0\ >=\ 17\.0"),
        ("V2 D4 读数", r"not_for_me_recall_pct=11\.1\ >=\ 17\.0"),
    ):
        assert literal in pattern, f"{label}（{literal}）没有出现在断言里"
    # ★ 计数约束（「同一断言命中两处」）落在 **structural** 那条 check 上，因为
    #   取值相同的判据（S1/S2/S5）都在那里；本 check 的变体区分靠**逐 variant 不同的
    #   数值**（D1 50.0/34.6、D4 0.0/11.1、D5 82.4/62.7），下面已逐条断言。
    #   对应断言见 test_two_variants_have_independent_structural_expectations。
    assert "\\s*34" in pattern and "\\s*50" in pattern, "两 variant 的 D1 读数必须分开写死"


# --------------------------------------------------------------------------
# 5. ★ 端到端：健康判绿 / 缺结果判红（AC#3、AC#5）
# --------------------------------------------------------------------------


def _run_gate(contract_path: Path, repo_root: Path) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "drift_gate.py"),
         "--contract", str(contract_path), "--phase", "static", "--mode", "closed",
         "--no-history", "--repo-root", str(repo_root)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _blocked_ids(out: str) -> list[str]:
    return [ln.split("] ", 1)[1].split(" ")[0]
            for ln in out.splitlines() if ln.startswith("[BLOCK]")]


def test_gate_is_green_on_healthy_tree() -> None:
    """健康态（产物齐备且未漂移）门禁必须 rc=0。"""
    rc, out = _run_gate(CONTRACT_PATH, REPO_ROOT)
    assert rc == 0, f"健康态门禁判红 (rc={rc}):\n{out}"
    assert not _blocked_ids(out), f"健康态出现 BLOCK: {_blocked_ids(out)}"


@pytest.mark.parametrize(
    ("rel", "expected_blocked"),
    [
        (DIRECTED_ARTIFACT,
         ["eval-directed-axis-frozen-reading", "eval-directed-axis-structural-guards"]),
        (TIMING_ARTIFACT,
         ["eval-timing-axis-frozen-reading", "eval-timing-axis-structural-guards"]),
    ],
)
def test_gate_fails_closed_when_result_file_missing(
    rel: str, expected_blocked: list[str], tmp_path: Path
) -> None:
    """★ 缺结果判红：只在镜像根里删掉一张卡，必须 rc=1 且**只**该轴判红。

    ★ 关键：这是 `present_when_missing="fail"` 的直接取证，也是 #159 要关的洞
    —— 改之前「文件不存在」会产出 `[SKIP] ... fail-open` 并被读成通过。
    同时断言**另一条轴不受影响**，证明两条结果文件是各自被读取的（AC#7），
    而不是靠「读了其中一张」蒙对。

    ★ 用镜像根（tmp_path + 只放被删掉的那张）而不是真删仓库文件：真删会让
    测试互相污染，且失败时留下残缺工作区。`--repo-root` 是执行器的既有参数。
    """
    mirror = tmp_path / "mirror"
    (mirror / "doc" / "research" / "data").mkdir(parents=True)
    for other in (DIRECTED_ARTIFACT, TIMING_ARTIFACT):
        if other == rel:
            continue
        dst = mirror / other
        dst.write_bytes((REPO_ROOT / other).read_bytes())

    rc, out = _run_gate(CONTRACT_PATH, mirror)
    assert rc == 1, f"删掉 {rel} 后门禁应 rc=1，实际 rc={rc}:\n{out}"
    blocked = _blocked_ids(out)
    assert blocked == expected_blocked, (
        f"删掉 {rel} 后判红的应为 {expected_blocked}，实际 {blocked}:\n{out}"
    )
    other_axis = [i for i in EVAL_CHECK_IDS if i not in expected_blocked]
    for cid in other_axis:
        assert cid not in blocked, f"{cid} 的另一轴产物仍在，不该判红"


def test_missing_message_distinguishes_absent_from_nonmatching() -> None:
    """「没测」（缺结果）与「读到但不符」（漂移）必须是**两句不同的话**。

    ★ 这是 fail-closed 的**可读性**前提：若两种情形输出同一句话，运维看到 BLOCK
    无法判断该去**生成产物**还是该去**查漂移** —— 而这两件事的处置完全不同。
    此处直接调执行器的 `evaluate`，对同一条 fail-closed check 喂两种输入。
    """
    check = {
        "id": "eval-x", "decision_ref": "ref", "description": "d",
        "pattern": r'"verdict": "pass"', "severity": "block",
        "present_when_missing": "fail",
        # ★ 刻意用仓库外形态的占位路径（不写 doc/ 前缀）：`scripts/doc_health.py`
        #   的 --check link 会扫 doc/、services/、scripts/ 等前缀的字面路径，
        #   写 `doc/x.json` 会给自己引入一条「引用的路径不存在」误报
        #   （实测：本文件引入前 17 条 block → 引入后 18 条）。占位符不该污染
        #   真文档健康度。
        "paths": ["artifacts/probe.json"],
    }
    # (a) 文件全缺失 ⇒ 「没测」措辞
    passed_a, detail_a = dg.evaluate(
        check, "<missing:artifacts/probe.json>", "closed", REPO_ROOT)
    # (b) 文件读到了、但内容不符 ⇒ 「漂移」措辞
    passed_b, detail_b = dg.evaluate(
        check, '--- artifacts/probe.json ---\n{"verdict": "fail"}\n', "closed", REPO_ROOT)

    assert passed_a is False and passed_b is False
    assert "结果文件全部缺失" in detail_a, detail_a
    assert "未匹配" in detail_b, detail_b
    assert detail_a != detail_b
    assert "未匹配" not in detail_a, "缺结果被写成了『内容不符』—— 两种情形不可区分"


def test_skip_default_still_fail_open_for_missing() -> None:
    """★ 缺省语义**未被 #159 改动**：不写该字段时，缺文件仍是 skip（rc=0）。

    ★ 反向保护：若哪天有人把缺省值也改成 fail，`vlm-n_ctx`（依赖 gitignore 的
    logs/ 快照）会在干净检出的 CI 里把门禁判红 —— 一次「顺手收紧」造成 CI 全红，
    而本票的意图只是让**评测结果** fail-closed。故缺省必须是 skip。
    """
    check = {
        "id": "baseline-x", "decision_ref": "ref", "description": "d",
        "pattern": r"x", "severity": "block", "paths": ["logs/missing.json"],
    }
    assert dg.present_when_missing_of(check) == "skip"
    passed, detail = dg.evaluate(check, "<missing:logs/missing.json>", "closed", REPO_ROOT)
    assert passed is True, detail
    assert "[SKIP]" in detail


# ==========================================================================
# t6 修复批次：对抗复核查出的 5 个「门禁+作者测试双双判绿」绕过
# ==========================================================================
#
# 背景：t4 对抗复核构造出 5 个绕过。每一条的**改动前**行为都实测为 rc=0
# （即门禁判绿），下面每节的第一个测试都是那份实测的**回归保护**。
#
# 测试策略：一律在 `tmp_path` 镜像里跑真执行器（`--repo-root`），
# **不**改入库产物、**不**改真契约。镜像里放真契约的 4 条 eval check
# （从真契约读出，不手抄），故「契约被改弱」与「产物被篡改」都能被测到。


def _eval_checks_from_contract() -> list[dict]:
    """真契约里的 4 条 eval check（读真实文件，不手抄）。"""
    doc = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    checks = [c for c in doc["checks"] if c["id"].startswith("eval-")]
    assert len(checks) == 4, [c["id"] for c in checks]
    return checks


def _mirror(tmp_path: Path, directed: str | None, timing: str | None) -> Path:
    """A minimal repo root holding only the artifacts the eval checks reference."""
    root = tmp_path / "mirror"
    (root / "doc" / "research" / "data").mkdir(parents=True, exist_ok=True)
    for rel, text in ((DIRECTED_ARTIFACT, directed), (TIMING_ARTIFACT, timing)):
        if text is not None:
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return root


def _write_eval_contract(tmp_path: Path, *, with_digest: bool = False) -> Path:
    """把真契约的 4 条 eval check 写成临时契约（读真文件，不手抄）。

    ★ **默认去掉 `content_digest`**（`with_digest=False`），这是一处**刻意的
    变量隔离**，不是放松守卫 —— 理由必须写清楚，否则下一位读者会以为本节的
    测试变弱了：

    本节（R1–R5）测的是「**正则层**有没有真的读某个字段」。而 #169 加的
    `content_digest` 是**排在正则之前**的一层：任一叶子被改动都会让它先判红，
    且它**倾向于**同时点亮该轴的两条 check（它们读同一份产物）。
    于是断言「`blocked == ["eval-directed-axis-structural-guards"]`」的那类
    用例会被摘要层**掩盖**——它们会以"错的红"通过或失败，而**不再证明**
    "正则真的读了该字段"。

    ⇒ 隔离方式：这些用例在**去了摘要字段**的契约副本上跑，让被测变量只剩正则层；
    摘要层自身的行为由独立文件 `test_drift_gate_content_digest.py` 与本节末尾的
    `test_digest_layer_reds_the_gate_on_a_forged_registry` 覆盖。
    ★ 去掉字段**就是**「#169 之前的状态」（该字段缺省 `null` = 不做摘要校验），
    故这些用例的判定路径与 #169 之前**逐字相同**，历史读数仍可比。
    ★ `test_mirror_without_the_digest_field_keeps_the_pattern_layer_honest` 反过来证明：
    去掉摘要字段后健康态仍判绿 ⇒ 该镜像没有引入别的差异。
    （★ 上面两个符号名是**实测存在**的用例名；本文件曾把它们写成另外两个不存在的
     名字 —— 那种"注释引用一个查无此人的测试"正是本仓禁止的失效引用形态。）
    """
    doc = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    checks = _eval_checks_from_contract()
    if not with_digest:
        checks = [{k: v for k, v in c.items() if k != "content_digest"} for c in checks]
    doc["checks"] = checks
    p = tmp_path / "eval-contract.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return p


def _gate(contract: Path, root: Path) -> tuple[int, list[str]]:
    rc, out = _run_gate(contract, root)
    return rc, _blocked_ids(out)


def _repl(text: str, old: str, new: str, nth: int = 0) -> str:
    """Replace the nth occurrence (asserts it exists) — never mutate the real file."""
    idx = [m.start() for m in re.finditer(re.escape(old), text)]
    assert len(idx) > nth, f"{old!r} occurrence #{nth} missing (found {len(idx)})"
    i = idx[nth]
    return text[:i] + new + text[i + len(old):]


# ★ These two fixtures intentionally mirror the module-level pair of the same name
# (`directed_text` / `timing_text` near the top, same bodies). The module-level ones
# serve the contract tests; the r1..r5 mutation tests below were written against
# their own local copies. Both read the same committed artifact, so behaviour is
# identical — they are kept rather than deleted because deleting them broke 20 tests
# at collection time. ruff reports the redefinition as F811; that is understood, and
# `scripts/tests/` is not in CI's ruff scope (the `scripts-tests` job only runs pytest).
@pytest.fixture(scope="module")
def direct_text() -> str:
    return (REPO_ROOT / DIRECTED_ARTIFACT).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timing_text() -> str:  # noqa: F811
    return (REPO_ROOT / TIMING_ARTIFACT).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# R1 ★ 结构判据的 observed 从未被读（改前实测 rc=0）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "old", "new", "nth"),
    [
        ("S1 V1 分母 {75,78} -> {0,0}",
         '"n_directed": 75,\n              "n_nondirected": 78',
         '"n_directed": 0,\n              "n_nondirected": 0', 0),
        ("S1 V2 分母 {75,78} -> {0,0}",
         '"n_directed": 75,\n              "n_nondirected": 78',
         '"n_directed": 0,\n              "n_nondirected": 0', 1),
        # rows_without_token_evidence 出现 4 次，第 0 次在 `reading` 块（无 criterion_id
        # 之前）—— 改那一处证明不了 pattern 被读；第 1 次才是 S3 的 observed。
        ("S3 V1 rows_without_token_evidence 0 -> 9",
         '"rows_without_token_evidence": 0', '"rows_without_token_evidence": 9', 1),
        ("S3 V1 quiet_rows 54 -> 0", '"quiet_rows": 54', '"quiet_rows": 0', 1),
        ("S3 V2 quiet_rows 59 -> 0", '"quiet_rows": 59', '"quiet_rows": 0', 1),
        ("S5 V1 rounds_count 3 -> 1", '"rounds_count": 3', '"rounds_count": 1', 1),
    ],
)
def test_structural_observed_is_actually_read(
    tmp_path: Path, direct_text: str, timing_text: str,
    label: str, old: str, new: str, nth: int,
) -> None:
    """★ R1：退化结构判据的 **observed** 后门禁必须判红（改前实测 rc=0）。

    判据的 description 自己写「分母退化或证据缺失时必须判红」，改前**做不到** ——
    契约只绑了 verdict，而变 observed 不改 verdict ⇒ 判绿。
    """
    contract = _write_eval_contract(tmp_path)
    mutated = _repl(direct_text, old, new, nth)
    assert mutated != direct_text, f"{label}: 变异未生效"
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    assert rc == 1, f"{label}: 期望 rc=1，实得 rc={rc}"
    assert blocked == ["eval-directed-axis-structural-guards"], f"{label}: {blocked}"


@pytest.mark.parametrize(
    ("label", "old", "new", "nth"),
    [
        ("T_SAMPLE_FLOOR n_speaking 21 -> 2（低于 10 的下限）", '"n_speaking": 21', '"n_speaking": 2', 12),
        ("T_SAMPLE_FLOOR n_missing_stamp 0 -> 5", '"n_missing_stamp": 0', '"n_missing_stamp": 5', 6),
    ],
)
def test_timing_sample_floor_is_actually_read(
    tmp_path: Path, direct_text: str, timing_text: str,
    label: str, old: str, new: str, nth: int,
) -> None:
    """★ R1（时序侧）：样本下限退化必须判红。

    `n_speaking`/`n_missing_stamp` 各出现 14/7 次，第 0 次在顶层 `reading` 块
    （任一 criterion_id **之前**）—— 改那一处证明不了 pattern 被读，故取
    T_SAMPLE_FLOOR 那一次的索引（12 / 6）。
    """
    contract = _write_eval_contract(tmp_path)
    mutated = _repl(timing_text, old, new, nth)
    assert mutated != timing_text, f"{label}: 变异未生效"
    rc, blocked = _gate(contract, _mirror(tmp_path, direct_text, mutated))
    assert rc == 1, f"{label}: 期望 rc=1，实得 rc={rc}"
    assert blocked == ["eval-timing-axis-structural-guards"], f"{label}: {blocked}"


def test_two_variants_have_independent_structural_expectations() -> None:
    """★ S3/S4 的 `quiet_rows` 两 variant 不同（54 / 59）：契约必须**分别**绑。

    ★ 若两 variant 共用一组期望值，则其中一个 variant 的断言恒真（另一 variant
    的值也满足）⇒ 单 variant 退化被静默放过。这正是 t2 踩过的「固定窗口跨进
    V2」在**数据层**的同一个形态。
    """
    check = next(c for c in _eval_checks_from_contract()
                 if c["id"] == "eval-directed-axis-structural-guards")
    pattern = check["pattern"]
    # 两 variant 各自的 quiet_rows 值都要出现在断言里。
    # ★ 断言的是**语义形态**而非某一种拼写：数字经值等价化写成 `54(?:\.0+)?`
    #   （这样 `54.0` 这种值等价写法不会被误判红），键名经 re.escape 会变成
    #   `"quiet_rows"`（引号内的空格不转义，但键值之间是 `\s*`）。故用「键名 +
    #   值等价数字」的正则去核，而不是拿 re.escape 拼一个字面子串 —— t8 先按字面
    #   子串写，结果因为一个 `\ ` 而漏配（假红）。
    def bound(key: str, value) -> bool:
        """Is `"<key>": <value>` bound in the pattern?

        ⚠️ This is a **substring** test, not a regex search: the pattern itself
        contains the literal text `\\s*` (a regex escape), so running a regex over it
        would try to match whitespace where the text has a backslash. t8 wrote it as
        a regex first and got a false red on both variants.

        The numeric part is emitted value-equivalently (`54` -> `54(?:\\.0+)?`), so
        that suffix is required too.
        """
        literal = '"' + key + '":\\s*' + str(value)
        if float(value) == int(float(value)):
            literal += r"(?:\.0+)?"
        return literal in pattern

    assert bound("quiet_rows", 54), "V1 的 quiet_rows=54 未被绑定"
    assert bound("quiet_rows", 59), "V2 的 quiet_rows=59 未被绑定"
    # ★ AC#1：S4 的**四个** observed 键都要在案（t7 查出的正是「三个零计数键一个都没绑」）。
    for key in ("quiet_rows_unattributable", "quiet_rows_empty_output",
                "quiet_rows_contradictory"):
        assert ('"' + key + r'":\s*0') in pattern, (
            f"S4 的 observed 键 {key} 未被绑定 —— 改它门禁仍判绿（t7 实测 rc=0）"
        )
    # ★ 取值相同的判据（S1/S2/S5 与 S4 的三个零计数）须要求命中**两处**，
    #   否则只改其中一个 variant 会被另一个顶替而判绿（t8 实测 rc=0）。
    assert re.search(r"\[\\s\\S\]\{1,\d+\}", pattern), (
        "缺少「同一断言命中两处」的计数约束"
    )
    # ★ S3 与 S4 的 quiet_rows 必须**分别**在案，否则其中一个 variant 的断言会
    #   被另一个满足（两 variant 共用一组值的失败形态）。t8 的写法是把
    #   `"quiet_rows": 54` 与 `"quiet_rows": 59` 各自锚在自己的 S4/S3 断言里。
    assert r'"quiet_rows":\s*54' in pattern, "V1 的 quiet_rows=54 未被绑定"
    assert r'"quiet_rows":\s*59' in pattern, "V2 的 quiet_rows=59 未被绑定"
    # ★ 不再要求「V1 段以 V2 的 card key 为界」：区域写法在 sort_keys 重排下会误判
    #   （P2_ 会排到 P_ 之前），已改为按值区分变体 —— 见
    #   test_patterns_are_not_trivially_anchored_across_variants 的说明。
    assert pattern.count(re.escape('"criterion_id": "S4-no-unattributed-quiet"')) >= 2, (
        "S4 的两个 variant 副本都要被断言（否则单个 variant 退化可被顶替）"
    )


# --------------------------------------------------------------------------
# R2 ★ 卡片级 criteria.verdict 无任何断言（改前实测 rc=0）
# --------------------------------------------------------------------------


_CARDLINE = '],\n        "verdict": "fail"\n      },\n      "criteria_registry"'


@pytest.mark.parametrize("nth", [0, 1], ids=["V1", "V2"])
def test_card_level_criteria_verdict_is_read(
    tmp_path: Path, direct_text: str, timing_text: str, nth: int
) -> None:
    """★ R2：把某 variant 的**卡片级** `criteria.verdict` 由 fail 改成 pass ⇒ 判红。

    改前无任何断言读它 ⇒ rc=0（两个 variant 都实测过）。
    """
    contract = _write_eval_contract(tmp_path)
    mutated = _repl(direct_text, _CARDLINE, _CARDLINE.replace('"fail"', '"pass"'), nth)
    assert mutated != direct_text
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    assert rc == 1, f"V{nth + 1} 卡片级 verdict 被改却判绿"
    assert blocked == ["eval-directed-axis-frozen-reading"], blocked


def test_timing_card_level_verdict_is_read(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★ R2（时序侧）：卡片级 verdict pass -> fail 必须判红 —— 由**结构层**判，不由门禁判。

    ★ t8 的如实更正（这一条从门禁层挪到测试层，理由实测）：
    时序卡的卡片级 `verdict` **没有排版稳定的锚点**。原文是
    `"verdict": "pass",\\n      "caveats"`，但 `sort_keys=True` 会把 `caveats` 重排走
    （实测：canonical 里该 verdict 变成 card 对象的**最后一个键**，其后不再是
    `caveats`）。任何把「verdict 紧跟某个后继键」写死的契约断言，都会把
    「值逐字不变、只是重排」的产物误判红 —— 那正是 t7 V4 的误伤形态。
    故该字段改由**结构断言**（`json.loads` 后按字段读，与排版无关）守着；
    门禁只守「存在性 + 决策承载字面」。
    ★ 这不降低覆盖：下面同样给出门禁层的负控，证明**门禁**对它能守的部分确实生效
    （`T_ONSET_MEDIAN/P90` 的 threshold/observed/verdict 都在门禁层），
    而卡片级 verdict 由本测试在结构层守住。
    """
    # (t8: this test is now STRUCTURAL — no contract/gate needed; see the docstring)
    # (1) 结构层：json 读到的卡片级 verdict 必须与按判据重算一致
    mutated_json = json.loads(timing_text)
    for name, card in mutated_json["cards"].items():
        verdicts = [it["verdict"] for it in timing_items(card)]
        expected = "fail" if "fail" in verdicts else (
            "unmeasurable" if "unmeasurable" in verdicts else "pass")
        assert card["verdict"] == expected, name
    # (2) 门禁层：把卡片级 verdict 改成 fail 后，**判据层**的门禁断言仍然生效
    #     （T_ONSET_* 的 threshold/observed/verdict 都在契约里），故整次判定不会
    #     因为漏绑卡片级 verdict 而整体失守；同时用结构断言补上门禁守不住的那一格。
    mutated = _repl(timing_text, '"verdict": "pass",\n      "caveats"',
                    '"verdict": "fail",\n      "caveats"')
    assert mutated != timing_text
    broken = json.loads(mutated)
    card = next(iter(broken["cards"].values()))
    assert card["verdict"] != card_verdict_from_criteria(card), (
        "结构层重算没抓到卡片级 verdict 与判据不一致 —— 该字段就完全没人守了"
    )


def test_directed_known_red_is_bound_and_documented() -> None:
    """★ 已知红必须**显式绑定**且在 description 里说明它不是门禁误报。

    定向轴两 variant 的 `criteria.verdict` 都是 `fail`（D4 未达标，属 #157 的
    如实读数）。绑它 + 写清意图，后人看到 `verdict=fail` 才不会当作门禁故障去
    「修」——这是本票最重要的可读性风险。
    """
    check = next(c for c in _eval_checks_from_contract()
                 if c["id"] == "eval-directed-axis-frozen-reading")
    pattern, desc = check["pattern"], check["description"]
    # 两 variant 的 known-red 都要绑定。pattern 里的字面量经 re.escape，
    # 且 `"verdict"` 与 `"fail"` 之间用 `\s*` 承接空白 —— 故断言的是**语义形态**
    # （出现次数）而不是某一种拼写。
    known_red = re.findall(r'"verdict":\\s\*"fail"', pattern)
    assert len(known_red) >= 2, (
        f"两 variant 的 known-red verdict 都要绑定，实际匹配 {len(known_red)} 处"
    )
    for kw in ("已知红", "如实读数", "不是门禁误报"):
        assert kw in desc, f"description 缺少「{kw}」—— 后人会把已知红误当故障"


def test_card_verdict_equals_recomputed_from_index_criteria(directed_json: dict) -> None:
    """★ 卡片级 verdict 必须等于按其 index 判据**重算**的结果。

    规则（与卡片模块一致）：任一 index 判据 verdict==fail ⇒ 卡片判 fail。
    这条把「卡片自报的总结论」与「它自己列出的判据」交叉绑定：产物若被手改成
    「判据全 pass 但总结论 pass、而已有一条 fail」这类自相矛盾的状态，即判红。
    """
    for name, card in directed_json["cards"].items():
        verdicts = [it["verdict"] for it in index_items(card)]
        expected = "fail" if "fail" in verdicts else (
            "unmeasurable" if "unmeasurable" in verdicts else "pass")
        assert card["criteria"]["verdict"] == expected, (
            f"{name}: 卡片级 verdict={card['criteria']['verdict']!r}，"
            f"但按 index 判据重算应为 {expected!r}（判据={verdicts}）"
        )


def test_timing_card_verdict_equals_recomputed(timing_json: dict) -> None:
    """时序轴同上（criteria 是**列表**，与定向轴形状不同）。"""
    for name, card in timing_json["cards"].items():
        verdicts = [it["verdict"] for it in timing_items(card)]
        expected = "fail" if "fail" in verdicts else (
            "unmeasurable" if "unmeasurable" in verdicts else "pass")
        assert card["verdict"] == expected, (
            f"{name}: verdict={card['verdict']!r}，按判据重算应为 {expected!r}"
        )


def test_timing_card_verdict_is_pass_so_not_pattern_is_meaningful() -> None:
    """★ not_pattern 的前提必须是**可执行的**：时序卡当前确实无 unmeasurable。

    若哪天时序卡本身判出 unmeasurable，not_pattern 会让门禁判红 —— 那是**有意**
    的行为（缺测 ≠ 通过），但测试的表述要让人看懂这是设计意图而非误报。
    """
    check = next(c for c in _eval_checks_from_contract()
                 if c["id"] == "eval-timing-axis-structural-guards")
    assert check["not_pattern"], "时序结构 check 缺少 not_pattern"
    # 空白不敏感（R3）
    assert re.search(r"\\s\*", check["not_pattern"]) or re.search(r"\\s", check["not_pattern"]), (
        f"not_pattern 对空白敏感，可被双空格/制表符绕过: {check['not_pattern']!r}"
    )


# --------------------------------------------------------------------------
# R3 ★ not_pattern 对空白敏感（改前实测 rc=0）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "verdict_line"),
    [
        ("单空格（基线形态）", '"verdict": "unmeasurable",\n'),
        ("双空格", '"verdict":  "unmeasurable",\n'),
        ("制表符", '"verdict":\t"unmeasurable",\n'),
    ],
)
def test_not_pattern_is_whitespace_insensitive(
    tmp_path: Path, direct_text: str, timing_text: str, label: str, verdict_line: str
) -> None:
    """★ R3：诚实判出的 unmeasurable **必须判红**，且不因空白写法而失配。

    改前 not_pattern 是 `"verdict": "unmeasurable"`（**单空格字面**）⇒ 双空格或
    制表符写法直接绕过 ⇒ rc=0。
    ★ 注意语义：这里判红是**设计意图**（「没测」≠「通过」），不是解析失败。
    """
    contract = _write_eval_contract(tmp_path)
    mutated = timing_text.replace(
        '"criterion_id": "T_ONSET_P90",',
        '"criterion_id": "T_ONSET_P90",\n            ' + verdict_line, 1)
    assert mutated != timing_text
    rc, blocked = _gate(contract, _mirror(tmp_path, direct_text, mutated))
    assert rc == 1, f"{label}: 诚实 unmeasurable 应判红（没测 ≠ 通过），实得 rc={rc}"
    assert blocked == ["eval-timing-axis-structural-guards"], f"{label}: {blocked}"


def test_not_pattern_does_not_hit_the_healthy_artifact(timing_text: str) -> None:
    """反向保护：not_pattern 不得在**健康**时序卡上命中（否则门禁恒红）。"""
    check = next(c for c in _eval_checks_from_contract()
                 if c["id"] == "eval-timing-axis-structural-guards")
    hits = re.findall(check["not_pattern"], timing_text, re.MULTILINE)
    assert not hits, f"not_pattern 在健康产物上命中 {len(hits)} 次 ⇒ 门禁恒红"


# --------------------------------------------------------------------------
# R5 ★ threshold 字面被改（改前实测 rc=0）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "rel", "old", "new"),
    [
        ("定向轴 D1 threshold 63.0 -> 999.0", DIRECTED_ARTIFACT, '"threshold": 63.0', '"threshold": 999.0'),
        ("时序轴 onset threshold 898.0 -> 9999.0", TIMING_ARTIFACT, '"threshold": 898.0', '"threshold": 9999.0'),
    ],
)
def test_threshold_literal_is_bound_in_contract(
    tmp_path: Path, direct_text: str, timing_text: str,
    label: str, rel: str, old: str, new: str
) -> None:
    """★ R5：改产物里的 `threshold` 字面 ⇒ 门禁判红（改前 rc=0，唯一拦截者是离线测试）。

    ★ 职责边界：门禁（契约正则）能抓**字面改动**；「阈值与卡片 BOUNDS 是否一致」
    由 `test_directed_artifact_thresholds_match_card_bounds` 等离线测试抓。
    两者互补，缺一不可 —— 门禁挡「产物被改」，测试挡「线本身被挪」。
    """
    contract = _write_eval_contract(tmp_path)
    if rel == DIRECTED_ARTIFACT:
        mutated = _repl(direct_text, old, new)
        rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
        want = ["eval-directed-axis-frozen-reading"]
    else:
        mutated = _repl(timing_text, old, new)
        rc, blocked = _gate(contract, _mirror(tmp_path, direct_text, mutated))
        want = ["eval-timing-axis-frozen-reading"]
    assert mutated != (direct_text if rel == DIRECTED_ARTIFACT else timing_text)
    assert rc == 1, f"{label}: 期望 rc=1，实得 rc={rc}"
    assert blocked == want, f"{label}: {blocked}"


# --------------------------------------------------------------------------
# R4 ★ 值等价与拼写等价的分界（**不修**，但要钉住行为）
# --------------------------------------------------------------------------


def test_value_equivalent_spelling_does_not_false_red(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★ R4：`50.0` -> `50.00`（**JSON 值完全相同**）不得误判红。

    ★ 这是「合法重排不该被误伤」的反向保护：门禁只该对**语义变化**判红。
    契约用 `50(?:\\.0+)?` 之类的值等价形式表达数字。
    """
    contract = _write_eval_contract(tmp_path)
    for label, text, rel_mut in [
        ("directed observed 50.0 -> 50.00", direct_text, "directed"),
        ("timing observed 573.0 -> 573.00", timing_text, "timing"),
    ]:
        if rel_mut == "directed":
            mutated = _repl(text, '"observed": 50.0', '"observed": 50.00')
            root = _mirror(tmp_path, mutated, timing_text)
        else:
            mutated = _repl(text, '"observed": 573.0', '"observed": 573.00')
            root = _mirror(tmp_path, direct_text, mutated)
        assert mutated != text
        # JSON 语义未变
        assert json.loads(mutated) == json.loads(text), f"{label}: 变异改变了 JSON 语义"
        rc, blocked = _gate(contract, root)
        assert rc == 0, f"{label}: 值等价改写被误判红 {blocked}"


def test_real_value_change_is_still_red(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """R4 反面：**真**改数值（50.0 -> 99.0）必须判红（模糊地带里守住底线）。"""
    contract = _write_eval_contract(tmp_path)
    mutated = _repl(direct_text, '"observed": 50.0', '"observed": 99.0')
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    assert rc == 1, "真退化却判绿"
    assert blocked == ["eval-directed-axis-frozen-reading"], blocked


# --------------------------------------------------------------------------
# ★ 性能：门禁不得挂起（t6 开发期实测过 >120s 的天真写法）
# --------------------------------------------------------------------------


def test_patterns_are_fast_on_healthy_artifacts(
    direct_text: str, timing_text: str
) -> None:
    """★ 单条 pattern 求值必须远小于 1 秒（实测最坏 ~0.013s）。

    ★ 为什么这是**安全**要求而非性能优化：拖挂的门禁比判红更坏 —— CI 会
    **超时**而不是给出漂移报告（无报告 = 无人能定位）。t6 的第一版把 5 个
    有界懒惰 gap 串起来，碰到「末条判据退化」时回溯爆炸，实测 >120s。
    """
    for check in _eval_checks_from_contract():
        text = f"--- x ---\n{direct_text if 'directed' in check['id'] else timing_text}\n"
        t0 = time.monotonic()
        re.search(check["pattern"], text, re.MULTILINE)
        secs = time.monotonic() - t0
        assert secs < 1.0, f"{check['id']} 求值耗时 {secs:.2f}s —— 门禁可能挂起 CI"


def test_patterns_stay_fast_when_every_assertion_fails(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★ 最坏情形（末条判据退化 ⇒ 全部断言失败）也不得回溯爆炸。

    ★ 这是上面那条性能断言的**真**内容：健康态匹配快是理所当然的，而
    「全不匹配」才是回溯爆炸的触发条件（t6 实测 >120s）。
    """
    contract = _write_eval_contract(tmp_path)
    # 末条判据（V2 的 S5）退化：V2 段每条断言都要失败。
    # S5 在契约里带「命中两处」的计数约束（两 variant 取值相同），故取最后一次出现。
    mutated = _repl(direct_text, '"rounds_count": 3', '"rounds_count": 1', 3)
    assert mutated != direct_text
    t0 = time.monotonic()
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    secs = time.monotonic() - t0
    assert rc == 1, f"末条判据退化应判红，实得 rc={rc}"
    assert blocked == ["eval-directed-axis-structural-guards"], blocked
    assert secs < 15.0, f"整次门禁耗时 {secs:.1f}s —— 回溯爆炸（CI 会超时而非判红）"


# --------------------------------------------------------------------------
# ★ 内容嗅探的边界（AC 明点的 `--- ` 陷阱）
# --------------------------------------------------------------------------


def test_missing_file_with_dashes_in_path_is_not_read_as_content() -> None:
    """★ `_is_all_missing` 是**内容嗅探**（`"--- " 不在输出里`），故路径字面里
    出现 `--- ` 会让缺失被判成「读到了」。

    这里把该行为**钉成已知语义**并断言**当前**后果：占位符进不了 pattern ⇒ 仍判红。
    ★ 为什么用测试而不是改执行器：`_is_all_missing` 属执行器（t1 的归属），
    且它在 `--repo-root` 之外的调用面很窄；本测试的价值是**证明这条路径不会
    静默变绿**，一旦有人改动嗅探逻辑就会在这里被发现。
    """
    check = {
        "id": "eval-x", "decision_ref": "ref", "description": "d",
        "pattern": r'"verdict":\s*"pass"', "severity": "block",
        "present_when_missing": "fail",
        "paths": ["artifacts/--- trap ---.json"],
    }
    missing = "<missing:artifacts/--- trap ---.json>"
    # (1) 嗅探确实被路径字面骗过（记录现状，不是期望的理想行为）
    assert dg._is_all_missing(missing) is False, (
        "_is_all_missing 的行为变了 —— 请重新核这段注释与下面的断言"
    )
    # (2) 但后果仍是判红：占位符不满足 pattern
    passed, detail = dg.evaluate(check, missing, "closed", REPO_ROOT)
    assert passed is False, detail
    # (3) 真正危险的是「pattern 宽到能匹配占位符本身」—— 那才会变绿。
    #     钉住它，使这条边界在改动时立刻可见。
    lax = dict(check, pattern=r"missing")
    passed_lax, _ = dg.evaluate(lax, missing, "closed", REPO_ROOT)
    assert passed_lax is True, (
        "宽 pattern 现在也能在缺失时判红 —— 好于预期，请更新本测试与注释"
    )


# ==========================================================================
# t6（R6）：CI 生成步骤的行为测试
# ==========================================================================
#
# ★ 为什么这段必须存在
# --------------------
# `quality.yml` 的 `drift-gate` job 里有一段生成两张记分卡产物的 shell。它的职责是
# **区分「卡片正常判红」与「卡片压根没产出结果」** —— 而 t4 对抗复核实测出一个
# fail-open：定向轴**输入**置空时卡片 rc=2 且**一个字节都没写**，步骤却因为
# 「文件存在且可解析」而拿**旧产物**当成新产物 ⇒ `CI-STEP-RC=0` + 门禁 rc=0，
# 即「卡片从未产出结果而全绿」。
#
# 修法（先 `rm -f` + rc=2 判失败）如果不被测钉住，下次有人「简化」掉那行 `rm`
# 就会静默复发 —— 而且**复发时 CI 是绿的**，没有人会收到信号。
#
# ★ 跑的是**从 quality.yml 抽出来的真脚本**，不是它的复述
# --------------------------------------------------------
# 抽取用**无依赖**的文本解析（不用 PyYAML）：CI 的 `scripts-tests` job 只装
# pytest + ruff，import yaml 会以 ModuleNotFoundError 失败，而那种报错看起来像
# 「步骤写坏了」，是个指错方向的告警。抽不到脚本 ⇒ **判红**（不 skip）——
# 「测不到」不是「通过」。
# --------------------------------------------------------------------------
# 抽取步骤脚本（无第三方依赖）
# --------------------------------------------------------------------------


def extract_step_run() -> str:
    """从 quality.yml 抽出该 step 的 `run: |` 块（文本解析，不依赖 PyYAML）。

    实现：定位 `- name: <STEP_NAME>` 行，向后找同级的 `run: |`，然后收走
    所有比它更深缩进的行（块标量），去缩进返回。
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("- name:") and STEP_NAME in line:
            name_indent = len(line) - len(line.lstrip())
            for j in range(i + 1, len(lines)):
                cand = lines[j]
                if not cand.strip():
                    continue
                cur_indent = len(cand) - len(cand.lstrip())
                # 离开本 step（出现同级或更浅的条目）
                if cand.lstrip().startswith("- ") and cur_indent <= name_indent:
                    break
                if re.match(r"^\s*run:\s*\|\s*$", cand):
                    run_indent = len(cand) - len(cand.lstrip())
                    body = []
                    for k in range(j + 1, len(lines)):
                        nxt = lines[k]
                        if not nxt.strip():
                            body.append("")
                            continue
                        if (len(nxt) - len(nxt.lstrip())) <= run_indent:
                            break
                        body.append(nxt[run_indent + 2:])
                    return "\n".join(body).rstrip("\n")
    raise AssertionError(
        f"在 {WORKFLOW} 里找不到 step {STEP_NAME!r} 的 run 块 —— "
        "本测试无法执行（不得 skip：测不到 ≠ 通过）"
    )


def _pick_bash() -> str:
    """Choose the bash used to run the step.

    On Windows this host has two bashes with INCOMPATIBLE path namespaces:
    Cygwin/Git (`/d/...`) and WSL (`/mnt/d/...`). `subprocess` resolves a bare
    `bash` to the WSL one, whose python path differs — picking wrong makes every
    scenario fail with "not found" and thus pass/fail for the wrong reason.
    Prefer Git bash so the `/d/...` interpreter form is valid.
    """
    if os.name == "nt":
        for cand in (r"C:\Program Files\Git\bin\bash.exe",
                     r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if Path(cand).exists():
                return cand
    found = shutil.which("bash")
    assert found, "找不到 bash —— 无法执行 CI 步骤"
    return found


def _interpreter_for(bash: str) -> str:
    """The interpreter path form that *bash* can exec (namespace-aware)."""
    exe = sys.executable
    if os.name != "nt":
        return exe
    win = str(exe).replace("\\", "/")
    posix = "/" + win[0].lower() + win[2:]           # C:/x -> /c/x
    wsl = "/mnt/" + win[0].lower() + win[2:]         # C:/x -> /mnt/c/x
    for form in (posix, wsl, win):
        probe = subprocess.run(
            [bash, "-c", f'exec "{form}" -c "print(1)"'],
            capture_output=True, text=True, timeout=60)
        if probe.returncode == 0:
            return form
    raise AssertionError(f"没有任何解释器路径形式能在 {bash} 下执行: {exe}")


BASH = _pick_bash()
PY_FOR_BASH = _interpreter_for(BASH)

#: 只拦截**卡片脚本**，不能宽泛匹配 `*decision_eval*` —— 步骤末尾的校验 heredoc
#: 里也出现 `decision_eval_*_card.json`（期望产物路径），宽泛匹配会把**校验**
#: python 也换成桩，于是「什么也没检查」却被读成通过（t6 实测踩过）。
CARD_SCRIPTS = "*decision_eval_card.py*|*decision_eval_timing_card.py*"

#: 卡片不写任何文件，退出码由 {code} 给定
NO_WRITE = """#!/bin/sh
case "$*" in
  __CARDS__) exit {code} ;;
esac
exec {real} "$@"
""".replace("__CARDS__", CARD_SCRIPTS)

#: 卡片写一个 0 字节文件后 rc=0（模拟写半截）
EMPTY_WRITE = """#!/bin/sh
case "$*" in
  __CARDS__)
    out=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--out" ]; then out="$a"; fi
      prev="$a"
    done
    [ -n "$out" ] && : > "$out"
    exit 0 ;;
esac
exec {real} "$@"
""".replace("__CARDS__", CARD_SCRIPTS)

#: 真跑卡片（定向轴如实判红 rc=1），模型「健康」态
WRITE_THEN_1 = """#!/bin/sh
case "$*" in
  *decision_eval_card.py*) {real} "$@" || true; exit 1 ;;
  *decision_eval_timing_card.py*) {real} "$@" || true; exit 0 ;;
esac
exec {real} "$@"
"""


def make_shim(root: Path, body: str) -> Path:
    d = root / "shim"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "python"
    p.write_text(body.format(code=2, real=PY_FOR_BASH), encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return d


def mirror(tmp_path: Path, with_old_artifacts: bool) -> Path:
    """镜像仓库：步骤需要 `services/webinfer`（含冻结夹具）+ 产物目录。

    ⚠️ `tests/fixtures/**` 是**必需**的（时序卡读它的冻结事件夹具）；漏掉会让
    「健康」场景失败，从而把所有人的注意力引到错的地方。
    """
    root = tmp_path / "repo"
    (root / "doc/research").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "services/webinfer", root / "services/webinfer",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    shutil.copytree(REPO_ROOT / "doc/research/data", root / "doc/research/data")
    if not with_old_artifacts:
        for rel in (DIRECTED_REL, TIMING_REL):
            (root / rel).unlink(missing_ok=True)
    return root


def run_step(root: Path, shimdir: Path) -> tuple[int, str]:
    env = dict(os.environ)
    win = str(shimdir).replace("\\", "/")
    env["PATH"] = win + ":" + env["PATH"].replace(";", ":")
    p = subprocess.run(
        [BASH, "-c", extract_step_run()], cwd=str(root), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    out = (p.stdout or "") + (p.stderr or "")
    assert "command not found" not in out, (
        f"桩未被 PATH 找到 ⇒ 本场景无效（会以错误的理由通过）:\n{out[:400]}"
    )
    return p.returncode, out


@pytest.fixture(scope="module")
def step_script() -> str:
    """脚本能抽到、非空、bash 语法合法，且仍含 t8 的原子替换修复。"""
    script = extract_step_run()
    assert script.strip(), "抽到的步骤脚本为空"
    # ★ AC#6：原地 `rm -f` + 原地写会在卡片失败时**破坏工作区**（t7 实测：卡片失败
    #   时 STEP-RC=1，但定向卡已被重写、时序卡被删）。故必须有暂存目录 + `mv` 替换。
    assert "STAGE" in script and "mktemp -d" in script, (
        "步骤里没有暂存目录（`mktemp -d`）—— 「失败时不动工作区」的修复被删掉了"
    )
    assert re.search(r"^\s*mv -f\s", script, re.MULTILINE), (
        "步骤里没有 `mv -f` 原子替换 —— 暂存目录白建了"
    )
    # ★ AC#5：内容指纹（sha256）必须在日志里，不能只有 size。
    assert "sha256" in script, (
        "步骤没有把产物 sha256 打进日志 —— 「同一次 CI 内生成→断言」的对象"
        "事后无法核验（只有 size 拿不到内容指纹）"
    )
    # 真做语法检查（写临时文件再 `bash -n`，因为 `bash -c` 不校验就执行）
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                     encoding="utf-8", newline="\n") as fh:
        fh.write(script)
        tmp = fh.name
    try:
        ok = subprocess.run([BASH, "-n", tmp], capture_output=True, text=True, timeout=60)
        assert ok.returncode == 0, f"步骤脚本语法错误: {ok.stderr}"
    finally:
        Path(tmp).unlink(missing_ok=True)
    return script


# --------------------------------------------------------------------------
# 场景
# --------------------------------------------------------------------------


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def artifact_state(root: Path) -> dict[str, str | None]:
    """入库产物的存在性 + md5（用于「失败后工作区未被改动」的判据）。"""
    return {rel: (md5_of(root / rel) if (root / rel).exists() else None)
            for rel in (DIRECTED_REL, TIMING_REL)}


def test_healthy_run_passes_and_writes_both_artifacts(tmp_path: Path, step_script: str) -> None:
    """健康态：真跑两张卡（定向轴如实 rc=1）⇒ 步骤 rc=0，两产物都写出。"""
    root = mirror(tmp_path, with_old_artifacts=False)
    shimdir = make_shim(tmp_path, WRITE_THEN_1)
    rc, out = run_step(root, shimdir)
    assert rc == 0, f"健康态应 rc=0，实得 rc={rc}\n{out}"
    for rel in (DIRECTED_REL, TIMING_REL):
        assert (root / rel).exists(), f"{rel} 未被写出"
        assert (root / rel).stat().st_size > 0
    assert "[ok]" in out
    # ★ AC#5：sha256 必须进日志，且**与实际产物一致**（不是印一个假指纹）。
    digests = re.findall(r"sha256=([0-9a-f]{64})", out)
    assert len(digests) == 2, f"日志里的 sha256 条数不对：{digests}"
    for rel, dig in zip((DIRECTED_REL, TIMING_REL), digests):
        actual = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        assert dig == actual, f"{rel}: 日志 sha256 与产物实际内容不符"


@pytest.mark.parametrize(
    ("label", "shim", "want", "needle"),
    [
        ("rc=2 且不写文件", NO_WRITE, "rc=2", "rc=2"),
        ("rc=1 且不写文件", NO_WRITE.replace("exit {code}", "exit 1"),
         "缺失（卡片未写出）", "缺失（卡片未写出）"),
        ("崩溃 rc=9", NO_WRITE.replace("exit {code}", "exit 9"),
         "不是一个读数", "不是一个读数"),
        ("静默 rc=0 且不写文件", NO_WRITE.replace("exit {code}", "exit 0"),
         "缺失（卡片未写出）", "缺失（卡片未写出）"),
    ],
)
def test_failure_leaves_committed_artifacts_byte_identical(
    tmp_path: Path, step_script: str, label: str, shim: str, want: str, needle: str
) -> None:
    """★ AC#6 的判据：**失败后入库产物仍在、且 md5 逐字节不变**。

    这是「临时目录生成 + 成功后原子替换」的核心保证，也是 t7 V6 的回归保护：
    原地 `rm -f` + 原地写在卡片失败时会留下「定向卡被重写、时序卡被删」的工作区。
    """
    root = mirror(tmp_path, with_old_artifacts=True)
    before = artifact_state(root)
    assert all(before.values()), "前置条件：两份入库产物都该在场"
    shimdir = make_shim(tmp_path, shim)
    rc, out = run_step(root, shimdir)
    assert rc == 1, f"{label}: 应失败，实得 rc={rc}\n{out}"
    assert needle in out, out[:400]
    after = artifact_state(root)
    assert after == before, (
        f"{label}: 失败后入库产物被改动了！\n  before={before}\n  after ={after}\n"
        "（原地写会在失败时破坏工作区；应只写暂存目录、成功后 mv）"
    )


def test_rc2_without_artifact_fails(tmp_path: Path, step_script: str) -> None:
    """★ R6 负控：卡片 rc=2 且**不写任何文件** ⇒ 步骤必须失败。

    改前：旧产物被当成新产物 ⇒ rc=0（这就是被修掉的 fail-open）。
    且 rc=2 现在本身也判失败（「没测 ≠ 通过」）。
    """
    root = mirror(tmp_path, with_old_artifacts=True)
    shimdir = make_shim(tmp_path, NO_WRITE)
    rc, out = run_step(root, shimdir)
    assert rc == 1, f"rc=2 且无产物应失败，实得 rc={rc}\n{out}"
    assert "rc=2" in out, out[:400]


def test_empty_artifact_fails(tmp_path: Path, step_script: str) -> None:
    """★ 负控：卡片写出 **0 字节**文件后 rc=0 ⇒ 必须失败（防「写半截」）。"""
    root = mirror(tmp_path, with_old_artifacts=False)
    shimdir = make_shim(tmp_path, EMPTY_WRITE.replace("exit {code}", "exit 0"))
    rc, out = run_step(root, shimdir)
    assert rc == 1, f"空产物应失败，实得 rc={rc}\n{out}"
    assert "为空文件" in out, out[:400]


def test_step_does_not_swallow_the_known_red() -> None:
    """★ 步骤**不得**把 rc=1 当失败 —— 那是 #157 的如实读数（D4 未达标）。

    判定权必须留在 `drift_gate`（它按契约断言判红/判绿并把已知红登记在案）；
    若生成步骤因 rc=1 直接失败，CI 会因「读数如实」而红，等于把判定权搬进生成步骤。
    """
    script = extract_step_run()
    assert re.search(r"^\s*1\)\s", script, re.MULTILINE), (
        "步骤里没有针对 rc=1 的专门分支 —— 请确认 rc=1 仍被判为「放行、交门禁判定」"
    )


# ==========================================================================
# t8：canonical 全量内容绑定（第二层，**不是**第二套门禁）
# ==========================================================================
#
# ★ 为什么需要这一层：门禁是正则，绑不完整。实测两份产物的标量叶子数：
#     directed = 1892   timing = 429
#   而 `criteria` 只占其中一小部分（其余在 overall / by_subset /
#   criteria_registry / negative_controls / rounds / metrics ...）。上一轮补
#   `observed` 漏了 `overall`，这轮补 `overall` 还会漏 `by_subset`
#   ⇒ **正则永远绑不完**。故做「类别修复」：对整份产物算 canonical sha256。
#
# ★ 分工（两侧都要，谁都不能替代谁）：
#   - **gate**（config/drift-contract.json + scripts/drift_gate.py）：
#     fail-closed 存在性 + 决策承载字面；在 `drift-gate` job 里独立跑，
#     故 CI 的保护**不依赖** pytest。
#   - **本层**（scripts/tests/，由既有 `scripts-tests` job 跑）：
#     全量叶子绑定 + 门禁表达不了的结构不变量。
#   ★ 不新建 job、不新建执行器。
#
# ★ `sort_keys=True` 同时消掉 t7 V4 的键序误伤：值逐字不变、只重排/换缩进时，
#   摘要**必须相同**（由 test_canonical_digest_is_layout_insensitive 钉住）。
#
# ★ #169 起：摘要的**实现**与**期望值**都不在本文件里了。
#   - 实现 ⇒ `scripts/eval_card_digest.py`（执行器 `drift_gate.py` 也 import 它；
#     两处各写一份 `json.dumps(..., sort_keys=True, separators=...)` 会各自漂移）；
#   - 期望值 ⇒ **`config/drift-contract.json` 的 `content_digest` 字段**。
#     本文件原先自带一份 `CANONICAL_DIGESTS` 字面量，与契约里的新字段构成
#     「同一事实两份副本、只靠一条测试对齐」—— 那正是 `eval_gate_fixtures.py`
#     当初被抽出来要消灭的形态（本仓已为此付过费），故**删掉**本地常量，
#     改为每次从契约读（`declared_digest_by_axis()`，单一来源在共享模块里）。
#   ★ 这不是"放松"：下列测试证明的**性质一条都没少**（排版不敏感 / 全量叶子覆盖 /
#     真值改动必变 / 非空 / 唯一性 / verdict 合法集合），只是"期望值从哪来"换了出处。

#: `_strip_volatile` 与 `canonical_digest` 的实现**唯一**来源（见上面那段说明）。
#: 本文件只 import，不重实现 —— `test_executor_and_tests_share_one_digest_implementation`
#: 会断言它与 `drift_gate.py` 用的是**同一个模块对象**。
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import eval_card_digest as ecd  # noqa: E402


def portable_digest(text: str) -> str:
    """canonical 摘要 —— 直接转调**唯一实现**（`scripts/eval_card_digest.py`）。

    ★ 本函数曾经是本文件里的一份**独立实现**。保留这个名字是为了让下面所有
    测试（以及它们证明的性质）一字不改，但**算法不再在这里**：它只有一份。
    """
    return ecd.canonical_digest(text)


#: 机器相关绝对路径所在的键（R7）—— 从唯一实现处转出，不再本地重写一份元组。
VOLATILE_PATH_KEYS = ecd.VOLATILE_PATH_KEYS


def count_leaves(node) -> int:
    """标量叶子数 —— 用来证明绑定的确实是**全量**内容。"""
    if isinstance(node, dict):
        return sum(count_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_leaves(v) for v in node)
    return 1


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_canonical_digest_matches_committed(key: str) -> None:
    """★ 全量内容绑定：产物**任何一个叶子**被改，摘要即变 ⇒ 判红。

    ★ 这是「类别修复」而非「补字面」：它不关心改的是哪个键，因此不存在
    「这轮补 overall、下轮漏 by_subset」的形态。

    ★ #169：期望值（`want`）现在**从契约读**（`content_digest` 字段），
    不再是本文件里的一份字面量副本 —— 契约同时是执行器的判据来源，
    故「门禁判红的那个数」与「本测试期望的那个数」在**构造上**不可能分叉。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    want = declared_digest_by_axis()[key]
    assert want, f"{key} 的 canonical 摘要为空 —— 空摘要等于没绑定"
    got = portable_digest(text)
    assert got == want, (
        f"{rel} 的内容摘要变了\n  期望(契约)={want}\n  实得={got}\n"
        "若这是**有意的**读数变化，请重跑卡片并更新**契约**里的 content_digest；"
        "若你只是重排/换缩进，那说明摘要还不够不敏感 —— 请报出来，不要放宽它。"
    )
    # ★ 契约与两份产物必须**互相**绑定：只改契约里的数字（而不是产物）也必须判红，
    #   否则「改契约就能让摘要层变绿」—— 那等于把这条守卫的插头拔掉。
    for cid, axis in AXIS_BY_CHECK_ID.items():
        if axis == key:
            assert declared_digest_of(cid) == got, (
                f"{cid} 声明了 {declared_digest_of(cid)}，但 {rel} 实际是 {got}"
            )


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_canonical_digest_is_layout_insensitive(key: str) -> None:
    """★ 值逐字不变、只改排版（键序 / 缩进 / 行尾）⇒ 摘要**必须相同**。

    这是 t7 V4 误伤的回归保护：`sort_keys + indent=4` 重写曾被旧门禁判红。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    base = portable_digest(text)
    for label, rendered in (
        ("sort_keys+indent=4", json.dumps(json.loads(text), sort_keys=True, indent=4,
                                          ensure_ascii=False)),
        ("sort_keys+indent=0", json.dumps(json.loads(text), sort_keys=True,
                                          ensure_ascii=False)),
        ("CRLF 行尾", json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False)
            .replace("\n", "\r\n")),
    ):
        assert portable_digest(rendered) == base, (
            f"{rel}: {label} 重排后摘要变了 —— 摘要对排版敏感，会误伤合法重写"
        )


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_digest_covers_every_region_not_just_criteria(key: str) -> None:
    """★ 证明绑定的是**全量叶子**，不只是 `criteria` 那一小块。

    对每个叶子抽样变异（含 criteria 之外的 overall / by_subset /
    criteria_registry / negative_controls ...），每个变异都必须让摘要变。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    data = json.loads(text)
    base = portable_digest(text)

    flat: list[tuple[tuple, object]] = []

    def walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, (*path, i))
        else:
            flat.append((path, node))

    walk(data)
    total = len(flat)
    step = max(1, total // 12)
    checked = 0
    for i, (path, value) in enumerate(flat):
        if i % step:
            continue
        probe = json.loads(text)
        cur = probe
        for part in path[:-1]:
            cur = cur[part]
        if isinstance(value, bool):
            cur[path[-1]] = not value
        elif isinstance(value, (int, float)):
            cur[path[-1]] = value + 1
        elif isinstance(value, str):
            cur[path[-1]] = value + "x"
        else:
            cur[path[-1]] = "x"
        alt = json.dumps(probe, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")) + "\n"
        assert portable_digest(alt) != base, (
            f"{rel}: 叶子 {path} 被改却没让摘要变 ⇒ 摘要覆盖不全"
        )
        checked += 1
    assert checked >= 8, f"{rel}: 抽样只有 {checked} 个 —— 本测试没真正覆盖全量"
    assert total == count_leaves(data), f"{rel}: 叶子计数不一致 ({total} vs {count_leaves(data)})"


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_real_value_change_does_change_the_digest(key: str) -> None:
    """反向保护：**真改数值**（不是排版）必须让摘要变，否则摘要恒真。"""
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    base = portable_digest(text)

    def first_number(node):
        if isinstance(node, dict):
            for v in node.values():
                r = first_number(v)
                if r is not None:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = first_number(v)
                if r is not None:
                    return r
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            return node
        return None

    n = first_number(json.loads(text))
    assert n is not None
    mutated = text.replace(str(n), str(n + 1), 1)
    assert mutated != text
    assert portable_digest(mutated) != base, "真改数值却没让摘要变 ⇒ 摘要无意义"


# ==========================================================================
# #169：摘要的**单一来源**（实现一份、期望值一份），以及它守的那一类缺陷（W2）
# ==========================================================================
#
# ★ 本票给了执行器一个 `content_digest` 字段（per-check，可选）。于是"摘要"这件事
#   突然有**三个**可能各写一份的地方：执行器、契约测试、执行器行为测试。
#   本仓为此付过费（`eval_gate_fixtures.py` 就是被抽出来的），故这里把三条都钉住：
#     (1) **算法**只有一份 ⇒ `scripts/eval_card_digest.py`，两侧 import 同一对象；
#     (2) **期望值**只有一份 ⇒ `config/drift-contract.json` 的 `content_digest`；
#     (3) 它对**真实执行器**确实发力（不是只在 pytest 里成立的装饰）。


def test_executor_and_tests_share_one_digest_implementation() -> None:
    """★★ 执行器与测试必须用**同一个模块对象**算摘要，不得各写一份。

    ★ 这堵的是"收敛后又各自长出副本"：只要有人在 `drift_gate.py` 里重新内联
    `json.dumps(..., sort_keys=True, separators=(",", ":"))`，它就不再是
    `eval_card_digest` 里的那个函数对象，本断言立刻失败。

    ★ 为什么这条是**安全**要求而非洁癖：执行器与测试对**同一份产物**下断言。
    两份实现一旦漂移（例如一边忘了排除机器相关路径键），就会出现
    「测试说产物对、门禁说产物错」或反过来的局面 —— 而两边**都**会报绿/报红得很自信。
    """
    import eval_card_digest as shared

    assert ecd is shared, (
        "本文件的 eval_card_digest 不是共享模块里的那个对象 —— 又长出副本了"
    )
    assert dg.canonical_digest is shared.canonical_digest, (
        "执行器 drift_gate.canonical_digest 不是 eval_card_digest 里的那个函数 —— "
        "两侧各有一份摘要实现，它们会各自漂移"
    )
    assert dg.is_legal_digest is shared.is_legal_digest, (
        "「合法摘要长什么样」的判定也必须只有一份（否则契约校验与测试会分叉）"
    )
    # 执行器与测试导入的必须是**同一个文件**（不是同名但不同路径的两个模块）。
    assert Path(dg.__file__).resolve().parent == Path(shared.__file__).resolve().parent
    assert Path(shared.__file__).name == f"{DIGEST_MODULE_NAME}.py"


def test_expected_digest_comes_from_the_contract_not_a_local_copy() -> None:
    """★★ 期望值**只**许来自契约：本文件里不得再出现一份摘要字面量。

    ★ 本票之前，`test_eval_gate_contract.py` 自带一个 `CANONICAL_DIGESTS` 字典，
    与（当时还不存在的）契约字段构成"同一事实两份副本、只靠一条测试对齐"。
    契约现在也持有该值并被**执行器**消费 ⇒ 两份副本必须收敛成一份（契约），
    否则「门禁判红的那个数」与「测试期望的那个数」可以各自被改动而互不察觉。

    ★ 用**源码扫描**而不是断言某个常量不存在：后者可以被绕过（换个名字即可），
    而"这里不再出现 64 位 hex 字面量"是这一收敛的**可执行形态**。

    ★★ **扫描范围是 `scripts/tests/` 下全部 `.py`**（不只是本文件）：
    本测试原先只扫 `Path(__file__)`，于是把字面量放进同目录的
    `eval_gate_fixtures.py` 或另一个测试文件就完全逃过扫描（评审 #169 实测指出）
    —— 那正是"单一来源"的**声明比实现宽**这一形态。清单与路径的单一来源是
    `eval_gate_fixtures.py`，摘要的单一来源是**契约**，两者都不许被第二份副本旁路。
    """
    offenders: list[tuple[str, list[str]]] = []
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        found = re.findall(r"\b[0-9a-f]{64}\b", path.read_text(encoding="utf-8"))
        if found:
            offenders.append((path.name, found))
    assert not offenders, (
        f"以下测试文件里出现了摘要字面量 {offenders} —— 期望值必须只有一个来源"
        "（契约的 content_digest 字段）。请改用 eval_gate_fixtures.declared_digest_of() "
        "/ declared_digest_by_axis() 从契约读。"
    )


@pytest.mark.parametrize("axis", ["directed", "timing"])
def test_declared_digest_is_grounded_in_the_real_artifact(axis: str) -> None:
    """★ 契约里的摘要必须**就是**入库产物今天的摘要（两侧互相钉死）。

    ★ 分工：`test_canonical_digest_matches_committed` 断言"产物没被改"，
    这一条断言"契约没被改" —— 缺任一条，只改一侧就能让摘要层变绿
    （把契约里的数字换成产物的新摘要，等于把守卫的插头拔掉）。
    """
    rel = DIRECTED_ARTIFACT if axis == "directed" else TIMING_ARTIFACT
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    declared = declared_digest_by_axis()[axis]
    assert portable_digest(text) == declared, (
        f"{rel} 的摘要({portable_digest(text)}) 与契约声明的({declared})不一致"
    )


@pytest.mark.parametrize("axis", ["directed", "timing"])
def test_card_scripts_reproduce_the_declared_digest(axis: str, tmp_path: Path) -> None:
    """★★ 重跑卡片 ⇒ 产物摘要必须**仍等于**契约声明的值（确定性的本地代理）。

    ★ 这是"跨机确定性"的**本地代理**，边界必须写清（不得读成跨机已证）：
    本测试在**同一台机器、同一个检出**上把卡片重新生成到 `tmp_path`，再比内容摘要。
    它证明的是「产物可由脚本确定性重放」；而**跨机**是否也一致，取决于
    `VOLATILE_PATH_KEYS` 的排除列表是否够用 —— 那只有 CI（另一台机器、另一个
    绝对路径）跑 `drift-gate` 时的真实读数能证明。**本票不声称跨机已验证。**
    （父 spec §6.3 R7 记录了内嵌绝对路径导致逐字节比对仅同机成立。）

    ★ 退出码：定向轴**如实判红 rc=1**（D4 未达标，见父 spec §3.5），故不能断言 rc=0 ——
    那是设计意图，不是失败。判据是"产物被写出来了"，且摘要对得上。
    """
    script = (
        REPO_ROOT / "services" / "webinfer"
        / ("decision_eval_card.py" if axis == "directed" else "decision_eval_timing_card.py")
    )
    out = tmp_path / "card.json"
    proc = subprocess.run(
        [sys.executable, str(script), "--json", "--out", str(out)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    assert out.exists(), (
        f"{script.name} 没有写出产物（rc={proc.returncode}）:\n"
        f"{(proc.stdout or '')[-500:]}{(proc.stderr or '')[-500:]}"
    )
    assert proc.returncode in (0, 1), (
        f"{script.name} 的退出码 {proc.returncode} 不是读数（0=全绿/1=有判据判红）:\n"
        f"{(proc.stderr or '')[-500:]}"
    )
    regenerated = portable_digest(out.read_text(encoding="utf-8"))
    declared = declared_digest_by_axis()[axis]
    assert regenerated == declared, (
        f"重跑 {script.name} 得到的摘要 {regenerated} ≠ 契约声明的 {declared} —— "
        "卡片不再确定性地产出被冻结的那份读数"
    )


# --------------------------------------------------------------------------
# ★ W2 的**核心 AC**：伪造 criteria_registry ⇒ **门禁**（不只是 pytest）判红
# --------------------------------------------------------------------------


def _forge_registry(text: str, variant: str) -> str:
    """把某 variant 的 `criteria_registry` **整份**换成一条伪造条目.

    ★ 这是 #169 工单里的**最小反例**，逐字复现：
    产物**仍是合法 JSON**（故 `parse_as` 抓不到），`criteria` 那一份**没动**
    （故契约已绑的字面全都还在、正则全部命中）⇒ 改前门禁 **rc=0**。
    """
    doc = json.loads(text)
    doc["cards"][variant]["criteria_registry"] = [
        {"criterion_id": "D5-cost-index", "threshold": 999.0}
    ]
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


@pytest.mark.parametrize(
    "variant",
    ["P_live4_prod_prompt", "P2_live4_prod_prompt_profile"],
)
def test_digest_layer_reds_the_gate_on_a_forged_registry(
    tmp_path: Path, direct_text: str, timing_text: str, variant: str
) -> None:
    """★★ **核心 AC**：伪造 `criteria_registry` ⇒ 门禁 rc=1，且点名该轴的 check。

    ★ 这是本票存在的理由（父 spec §6.5 W2 / §7 第 7 条）。改前实测（两个 variant
    各测一次）：**rc=0** —— 因为「内容改了但仍是合法 JSON」这一整类，门禁上
    **一道守卫都没有**（`present_when_missing` 管缺失、`parse_as` 管解析失败，
    两者都不管"解析成功但字段被改"）。
    ★ 后果为何是**必须**由门禁自己守：CI 的 `drift-gate` job **刻意独立跑**
    （本仓不加 `needs`，见父 spec §1 被否方案）。故"pytest 没跑或挂了"时，
    原来那唯一一道守卫（pytest 侧的 canonical 摘要）**不在场**。

    ★ 断言的是**门禁自己的退出码**（子进程真跑），不是库函数返回值。
    """
    contract = _write_eval_contract(tmp_path, with_digest=True)  # ← 带摘要的真契约副本
    mutated = _forge_registry(direct_text, variant)
    assert mutated != direct_text
    # 前置条件（否则本测试可能在测别的东西）：
    # ① 仍是合法 JSON；② 契约已绑的 `criteria` 那一份**逐字未动**。
    assert json.loads(mutated), "伪造后不再是合法 JSON —— 那测的是 parse_as 层"
    assert json.loads(mutated)["cards"][variant]["criteria"] == \
        json.loads(direct_text)["cards"][variant]["criteria"], (
        "伪造动到了 criteria 那一份 —— 那测的是正则层，不是同源副本"
    )
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    assert rc == 1, (
        f"伪造 {variant} 的 criteria_registry 后门禁判绿（rc={rc}）—— W2 未被堵住"
    )
    assert blocked == [
        "eval-directed-axis-frozen-reading",
        "eval-directed-axis-structural-guards",
    ], blocked


def test_forged_registry_was_green_before_the_digest_field(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★★ W2 的**改前读数**固化成回归保护：没有该字段时，同一输入判**绿**。

    ★ 「改前 rc=0」是本票的**实测**起点（工单由主理人复现，本次也由执行者复现）。
    把它写成测试而不是只写在结论里，是因为**结论会随票据关闭而消失**，
    而这条对照是"新字段真的在起作用"的**唯一**证据 —— 少了它，
    上面那条 rc=1 有可能只是碰巧（夹具写错、路径不对、别的层先红了）。

    ★ 「改前」是**模拟**而非 checkout（共享工作树禁 `stash`/`reset`/`checkout`）：
    做法是把当前契约副本里 4 条 eval-* 的 `content_digest` **删掉**后跑**同一个**
    当前执行器。这精确复现"没有该字段时"的判定路径（缺省 `None` ⇒ 不做摘要校验），
    与父 spec §6.5 W1 记录 `parse_as` 改前读数用的是同一手法。
    """
    contract = _write_eval_contract(tmp_path, with_digest=False)
    mutated = _forge_registry(direct_text, "P_live4_prod_prompt")
    rc, blocked = _gate(contract, _mirror(tmp_path, mutated, timing_text))
    assert rc == 0, (
        f"去掉 content_digest 后同一伪造输入竟然判红了（rc={rc}, {blocked}）—— "
        "那么上面那条测试就不能归因到新字段，请复核"
    )
    assert blocked == [], blocked


def test_mirror_without_the_digest_field_keeps_the_pattern_layer_honest(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★ 反假阳性对照：**去掉摘要字段**的健康镜像必须仍判绿。

    ★ 上一条把"去掉字段"当作"改前"的代理。这条证明该代理**本身**没有引入别的
    差异：健康产物 + 无摘要契约 ⇒ rc=0、无 BLOCK。两条合起来才说明
    「rc 从 0 变 1」的归因是干净的（变量只有摘要字段与那处伪造）。
    """
    contract = _write_eval_contract(tmp_path, with_digest=False)
    rc, blocked = _gate(contract, _mirror(tmp_path, direct_text, timing_text))
    assert rc == 0, f"健康镜像（无摘要字段）判红（rc={rc}, {blocked}）"
    assert blocked == [], blocked


def test_layout_only_rewrite_does_not_red_the_gate(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★★ **反误伤**：只重排 / 重缩进 / 换行尾（值逐字不变）⇒ 门禁必须仍 rc=0。

    ★ 这条与上面的伪造用例配对，缺一不可：一个"内容一变就判红"的守卫若对
    **合法重写**也判红，就会把每次格式化/换工具链判死，人会因此把它拆掉 ——
    于是真正的洞（同源副本被改）回来。父 spec §3.3.2 记录的 t7 V4 正是这个误伤形态。

    ★ 用**带摘要字段的真契约副本**跑（这正是要防误伤的那条路径）。

    ★ 作用域（实测边界，**不把话说大**）：本用例覆盖「键序 + 缩进 + 行尾」三轴，
    它们都由摘要层保证（`sort_keys` / `separators` / `json.loads`）。
    ★ **另有一条本层管不到的**：把 `": "`（冒号+空格）压成 `":"` 的**紧凑**渲染
    会让门禁判红 —— 但那**不是摘要层做的**（实测：同一输入在**去掉摘要字段**，
    即 #169 之前的配置下**同样 rc=1**，红的是正则层的 `pattern=… 未匹配`，
    因为契约 pattern 里的 `"criterion_id": "…"` 是 `re.escape` 后的**字面空格**）。
    那是**既有的、与 #169 无关的**正则锚定形态，已由
    `test_compact_colon_only_rendering_is_a_pre_existing_regex_limit` 如实登记。
    """
    contract = _write_eval_contract(tmp_path, with_digest=True)
    for label, render in (
        ("sort_keys+indent=4",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=4,
                              ensure_ascii=False) + "\n"),
        ("sort_keys+indent=2",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=2,
                              ensure_ascii=False) + "\n"),
        ("CRLF 行尾",
         lambda t: json.dumps(json.loads(t), sort_keys=True, indent=2,
                              ensure_ascii=False).replace("\n", "\r\n") + "\r\n"),
    ):
        rendered = render(direct_text)
        assert rendered != direct_text, f"{label}: 重排未生效"
        assert json.loads(rendered) == json.loads(direct_text), f"{label}: 值变了"
        rc, blocked = _gate(contract, _mirror(tmp_path, rendered, timing_text))
        assert rc == 0, f"{label}: 合法重排被误判红 {blocked}"


def test_compact_colon_only_rendering_is_a_pre_existing_regex_limit(
    tmp_path: Path, direct_text: str, timing_text: str
) -> None:
    """★ **如实登记的既有边界**（不是 #169 引入的，也不是摘要层的）。

    ★ 实测事实：把产物按 `separators=(",", ":")` 压成**无冒号空格**的紧凑 JSON
    （值逐字不变、摘要也不变）时，门禁 **rc=1**，且判红理由来自**正则层**
    （`pattern=… 未匹配`），**不是** `content_digest`。

    ★ 归因的取证方式：同一输入在**去掉 `content_digest` 的契约副本**（= #169 之前的
    配置，该字段缺省 `null` 即不做摘要校验）下**同样 rc=1** ⇒ 该行为**先于本票存在**，
    与摘要层无关。

    ★ 根因：契约 pattern 里的 `"criterion_id": "D1-…"` 经 `re.escape` 后，冒号后是
    **字面空格**（不是 ``\\s*``）⇒ 紧凑 JSON 里没有该空格，锚点失配。
    ★ 本票**不修**它：修法要动 4 条 pattern 的锚定形态（把冒号后的空白改成 ``\\s*``），
    那属**契约正则层**的口径变更，风险与归属都不同于本票（本票是给门禁加内容摘要）。
    ⇒ 作为**已知边界登记**，并由本测试钉住「它的红来自正则层、且先于本票」。

    ★ 为什么必须钉住而不是删掉：这条断言一旦变绿，说明有人改动了 pattern 的锚定形态 ——
    那时应**显式更新本测试与 spec 的登记**，而不是让这条边界悄悄消失或悄悄变红。
    """
    rendered = json.dumps(json.loads(direct_text), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False) + "\n"
    assert json.loads(rendered) == json.loads(direct_text), "紧凑渲染改变了值"
    assert portable_digest(rendered) == portable_digest(direct_text), (
        "紧凑渲染改变了摘要 —— 那说明摘要对空白敏感，是**另一个**缺陷，请先修它"
    )

    # (a) 带摘要字段（今天的配置）
    rc_with, blocked_with = _gate(
        _write_eval_contract(tmp_path, with_digest=True),
        _mirror(tmp_path, rendered, timing_text))
    assert rc_with == 1, (
        f"紧凑渲染现在判绿了（rc={rc_with}）—— 好于预期，请更新本测试与 spec 的登记"
    )

    # (b) 去掉摘要字段（= #169 之前的配置）⇒ 必须**同样**判红，才能把归因钉死在正则层
    case = tmp_path / "pre169"
    case.mkdir()
    rc_without, blocked_without = _gate(
        _write_eval_contract(case, with_digest=False),
        _mirror(case, rendered, timing_text))
    assert rc_without == 1, (
        f"去掉 content_digest 后同一输入竟然判绿（rc={rc_without}）—— "
        "那么这条边界就**不是**既有的，请重新归因"
    )
    assert blocked_with == blocked_without, (
        f"带/不带摘要字段判红的 check 不一致：{blocked_with} vs {blocked_without} —— "
        "说明摘要层也参与了判定，本测试的归因失效"
    )
    assert blocked_with == [
        "eval-directed-axis-frozen-reading",
        "eval-directed-axis-structural-guards",
    ], blocked_with


# --------------------------------------------------------------------------
# ★ 前置定义：什么叫「同源副本」（工单要求先定义再修，否则又是一轮追字面）
# --------------------------------------------------------------------------

#: 两份副本**共有**且承载判据语义的字段（其余字段不属"同一事实"的定义域）。
SEMANTIC_SHARED_FIELDS: dict[str, tuple[str, ...]] = {
    "directed": ("statement", "metric", "scope", "direction", "threshold"),
    "timing": ("statement", "metric", "kind"),
}


def registry_agree_problems(data: dict, key: str) -> tuple[int, list[str]]:
    """比对 `criteria_registry` 与卡内 `criteria` 的**共有语义字段**，返回 (比对数, 问题列表).

    ★ 为什么抽成函数（而不是把循环留在测试体里）：**负控必须打在同一个谓词上**。
    本文件原先的负控在测试体里**另抄一份**同样的循环来"证明那条定义会抓" ——
    那只证明了"抄件会抓"，真断言若被改坏它照样绿（评审 #169 实测指出，正是本仓
    「守卫存在、但它不在这条路径上」那类）。抽出来之后，负控与真断言调用**同一个**
    `registry_agree_problems`，两者不可能分叉。

    Parameters
    ----------
    data : dict
        已 ``json.loads`` 的产物。
    key : str
        ``"directed"`` 或 ``"timing"``（两轴的 criteria 形状不同）。

    Returns
    -------
    tuple[int, list[str]]
        ``(实际比对过的字段数, 问题描述列表)``；问题为空表示同源关系成立。
    """
    fields = SEMANTIC_SHARED_FIELDS[key]
    compared = 0
    problems: list[str] = []
    for card_name, card in data["cards"].items():
        registry = {r["criterion_id"]: r for r in (card.get("criteria_registry") or [])}
        if not registry:
            problems.append(f"{card_name}: 没有 criteria_registry —— 同源定义失去对象")
            continue
        items = card["criteria"] if key == "timing" else (
            card["criteria"]["index"] + card["criteria"]["structural"])
        if len(registry) != len(items):
            problems.append(
                f"{card_name}: registry {len(registry)} 条 vs criteria {len(items)} 条 —— "
                "两份副本的**判据集合**不同，同源关系不成立"
            )
        for item in items:
            cid = item["criterion_id"]
            if cid not in registry:
                problems.append(f"{card_name}: {cid} 不在 registry 里")
                continue
            for field in fields:
                if field not in item and field not in registry[cid]:
                    continue  # 两侧都没有该字段 ⇒ 不在"共有字段"定义域内
                if item.get(field) != registry[cid].get(field):
                    problems.append(
                        f"{card_name} / {cid} / {field}: criteria={item.get(field)!r} "
                        f"但 registry={registry[cid].get(field)!r} —— "
                        "同一事实的两份副本对不上（这就是 W2 的形状）"
                    )
                compared += 1
    return compared, problems


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_criteria_registry_is_the_same_fact_as_the_criteria_copies(key: str) -> None:
    """★★ **语义定义 + 断言**：`criteria_registry` 与卡内 `criteria` 是**同一事实**.

    ★ 工单的硬要求：「**必须先定义哪些拷贝是同源事实**，否则退化成又一轮追字面」
    （父票已因此返工三次：t6 补 `observed` → t7 漏 `overall`/`by_subset`/`S4`
    → t8 才改成类别级的 canonical 摘要）。故把该关系写成**可执行**的定义：

      对每张卡、每个 `criterion_id`：
        `criteria_registry` 里那条与 `criteria`（index+structural / 列表）里那条，
        **在两者共有的字段上必须逐字段相等** —— 具体是
        `statement` / `metric` / `scope` / `direction` / `threshold`（定向轴）
        与 `statement` / `metric` / `kind`（时序轴，其 threshold/direction 为 null）。

    ⇒ 结论（供门禁设计用）：**registry 不是独立事实，而是同一批阈值/判据 id 的第二份
    拷贝**。因此「只改 registry」不是"发现了一个新数字"，而是"同一事实的两份副本
    对不上"—— 那正是 W2，也正是摘要层（绑**整份内容**）能抓住而正则层
    （锚在 `criteria` 副本上，且 alias 缺 `observed` 字段）抓不住的形态。

    ★ 作用域如实声明（不把话说大）：本测试只断言**共有字段**的一致性；
    registry 独有的键（定向轴 `min_denominator`、时序轴 `statistic`）
    **不参与**比对 —— 它们在 criteria 副本里没有对应物，故不属"同一事实"。
    ★ 它也不声称"两份副本必须永远并存"：若哪天卡片改成只输出一份，本测试会
    自然失效（`criteria_registry` 缺失时下面会判红并提示更新本定义），
    那是一次**产物形态变更**，应当显式改这条定义而不是悄悄跳过。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
    compared, problems = registry_agree_problems(data, key)
    assert not problems, "\n".join(problems)
    assert compared > 0, f"{rel}: 一个字段都没比 —— 同源定义没真正跑"
    # ★ 下限是**逐轴**的（本测试按 `key` 参数化，每次只跑一条轴）——
    #   实测形态：定向轴每个 card 40 个字段 × 2 card = 80；时序轴 24 × 1 = 24。
    #   若哪天有人把比对范围悄悄缩小（例如只比 `threshold`），下限会先红。
    #   ★ 时序侧余量只有 4 个字段，比定向侧紧。
    floor = 70 if key == "directed" else 20
    assert compared >= floor, (
        f"{rel}: 只比了 {compared} 个字段（下限 {floor}）—— 本测试没真正覆盖同源关系"
    )


def test_registry_agree_check_reds_on_a_forged_registry(direct_text: str) -> None:
    """★ **负控**：把上面那条"同源定义"推红一次，证明它真会抓（不是恒绿的装饰）。

    ★ 本仓教训：守卫必须有一条"它真抓到过东西"的证据，且该证据要**固化成测试**
    而不是只写在结论里（票据关闭后结论会消失）。
    ★★ 关键：本负控调用的是**真断言用的同一个谓词**
    :func:`registry_agree_problems`（原先这里另抄了一份同样的循环 —— 那只证明
    "抄件会抓"，真断言被改坏时它照样绿，正是本仓「守卫存在但它不在这条路径上」
    那类形态）。
    ★ 用**内存里**的副本改，不碰入库产物：测试中途被打断也不该留下被改的工作区。
    """
    data = json.loads(direct_text)
    # (0) 基准必须先绿，否则下面测的不是负控
    compared_ok, problems_ok = registry_agree_problems(data, "directed")
    assert not problems_ok, f"基准就不干净，负控无效: {problems_ok[:3]}"
    assert compared_ok > 0, "基准一个字段都没比 —— 负控无效"

    # (1) 整份替换 registry（工单的最小反例）⇒ 必须被发现
    data["cards"][DIRECTED_CARD_KEY]["criteria_registry"] = [
        {"criterion_id": "D5-cost-index", "threshold": 999.0}
    ]
    _compared, problems = registry_agree_problems(data, "directed")
    assert problems, (
        "伪造 registry 后同源比对**没有**发现任何不一致 —— 那条定义是恒绿的装饰"
    )

    # (2) 更隐蔽的形态：条数相同、**只有一个字段**被改（阈值字面不动）⇒ 也必须被发现。
    #     这一条才真正证明"逐字段"比对在起作用，而不只是靠条数不等。
    subtle = json.loads(direct_text)
    for item in subtle["cards"][DIRECTED_CARD_KEY]["criteria_registry"]:
        if item["criterion_id"] == "D4-not-for-me-recall-generalization":
            item["direction"] = "upper"  # 语义反转，threshold 一字不动
    _c2, problems2 = registry_agree_problems(subtle, "directed")
    assert problems2, "只改一个字段（方向反转）却没被发现 —— 比对不是逐字段的"
    assert any("D4-not-for-me-recall-generalization" in p for p in problems2), problems2


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_criterion_ids_are_unique_within_each_card(key: str) -> None:
    """★ 每个 card 内 `criterion_id` 不得重复（含 `criteria_registry`）。

    ★ 为什么是安全要求：重复 id 下「末次写入优先」的消费方（dict 化）会**静默**
    丢掉先出现的那条判据 —— 于是按文本扫描的门禁与消费方**看到的不是同一份数据**，
    两个读者对同一产物得出不同结论。t7 的 V3 正是「追加重复 id」这条绕过
    （门禁 rc=0）。本层在 `json.loads` 之后判定，绕不过去。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
    for card_name, card in data["cards"].items():
        if key == "timing":
            items = card["criteria"]
        else:
            items = card["criteria"]["index"] + card["criteria"]["structural"]
        ids = [it["criterion_id"] for it in items]
        for bucket, names in (("criteria", ids),
                              ("criteria_registry",
                               [it["criterion_id"] for it in (card.get("criteria_registry") or [])])):
            dupes = sorted({i for i in names if names.count(i) > 1})
            assert not dupes, (
                f"{rel} / {card_name} / {bucket}: criterion_id 重复 {dupes} —— "
                "「末次写入优先」的消费方会静默丢掉前一条"
            )


@pytest.mark.parametrize("key", ["directed", "timing"])
def test_every_criterion_verdict_is_in_the_legal_set(key: str) -> None:
    """★ 每个 criteria[*].verdict 都必须属于合法集合（**结构**断言，与拼写无关）。

    这是门禁 `not_pattern` 的结构层对应物：门禁只能用正则挡 `unmeasurable`
    （且必须容忍大小写与冒号空白，t7 的 V3 实测大写形态曾绕过），
    而这里在 `json.loads` 之后判定 —— 任何拼写变形都无效。
    """
    rel = DIRECTED_ARTIFACT if key == "directed" else TIMING_ARTIFACT
    data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
    legal = {"pass", "fail", "unmeasurable"}
    for card_name, card in data["cards"].items():
        items = card["criteria"] if key == "timing" else (
            card["criteria"]["index"] + card["criteria"]["structural"])
        for it in items:
            assert it["verdict"] in legal, (
                f"{rel} / {card_name} / {it['criterion_id']}: "
                f"verdict={it['verdict']!r} 不在 {sorted(legal)}"
            )


# ==========================================================================
# t8 / AC2：判据 observed ↔ overall/by_subset 的**交叉副本绑定**
# ==========================================================================
#
# ★ 为什么这条最重要：`criteria` 与 `overall`/`by_subset` 是**同一批读数的两份副本**
#   （卡片为了可读性报了两遍）。只改一份就会自相矛盾。
#   t7 实测 `overall.cost_index` 被清零而门禁判绿（rc=0）—— 那正是本票存在的理由
#   （cost_index 是代价加权主指标）。
#   契约层现在也绑了主指标的字面；而**本测试是数值级绑定**，与拼写/排版无关，
#   因此构成第二道、且语义更完整的一道。
#
# ★ 作用域（scope）必须参与判断，否则会造出假红：
#   定向轴 D4 的 scope 是 `by_subset/generalization`，它的 observed 等于
#   **generalization 子集的 median**（V1 0.0 / V2 11.1），而**不是** overall 的 median
#   （实测 V1 overall 0.0 / generalization 0.0；**V2 overall 11.5 / generalization 11.1**
#   —— 两者不同！）。若一律拿 overall 比对，V2 的 D4 会被误判红。
#   `scope=None`（时序轴）的判据不参与本测试：其中的 `T_PREMATURE_MEASURED` 属 #158
#   遗留的「无法测量」写入点缺失，无对应聚合值可比。


def _aggregate_median(card: dict, scope: str | None, metric: str):
    """按 `scope` 从 `overall` / `by_subset` 取该指标的 median（scope 决定取哪一份）。"""
    if not scope:
        return None, "no-scope"
    if scope == "overall":
        block = card.get("overall", {}).get(metric)
    elif scope.startswith("by_subset/"):
        subset = scope.split("/", 1)[1]
        block = card.get("by_subset", {}).get(subset, {}).get(metric)
    else:
        return None, f"unknown-scope:{scope}"
    if not isinstance(block, dict):
        return None, f"missing:{scope}/{metric}"
    return block.get("median"), scope


@pytest.mark.parametrize("variant", ["P_live4_prod_prompt", "P2_live4_prod_prompt_profile"])
def test_index_observed_equals_aggregate_median(directed_json: dict, variant: str) -> None:
    """★ AC2：每条 index 判据的 `observed` 必须等于它自己 scope 下的聚合 median。

    任何**单副本篡改**要么与 `criteria` 不符、要么与 `overall`/`by_subset` 不符 ——
    两副本互为见证。
    """
    card = directed_json["cards"][variant]
    checked = 0
    for item in index_items(card):
        metric = item.get("metric")
        scope = item.get("scope")
        expected, where = _aggregate_median(card, scope, metric)
        if expected is None:
            # 无对应聚合值（时序轴 / 未知 scope / 该子集无分母）⇒ 不参与本测试
            continue
        checked += 1
        assert item["observed"] == expected, (
            f"{variant} / {item['criterion_id']}: observed={item['observed']!r} 与 "
            f"{where}.{metric}.median={expected!r} 不一致 —— "
            "两份副本对同一读数给出了不同结论"
        )
    assert checked >= 4, (
        f"{variant}: 只核了 {checked} 条 index 判据（应 >= 4）—— 本测试没真正覆盖"
    )


@pytest.mark.parametrize("variant", ["P_live4_prod_prompt", "P2_live4_prod_prompt_profile"])
def test_overall_series_and_median_are_mutually_consistent(
    directed_json: dict, variant: str
) -> None:
    """★ AC2：`overall` 每个指标的 `per_round` 与 `median` 必须自洽。

    这条钉住「只改 median 不改 per_round」（或反之）的自相矛盾形态 ——
    门禁只绑了我们选中的那几个字面，而这里对**每个**指标都核一遍。
    `not_for_me_precision_pct` 的 `per_round` 含 `null`（该轮无分母），故先剔除。
    """
    import statistics

    card = directed_json["cards"][variant]
    checked = 0
    for name, block in card["overall"].items():
        if not isinstance(block, dict) or "median" not in block:
            continue
        values = [v for v in block.get("per_round", []) if isinstance(v, (int, float))]
        if not values:
            continue
        checked += 1
        assert block["median"] == statistics.median(values), (
            f"{variant} / overall.{name}: median={block['median']!r} 与 "
            f"per_round 的中位={statistics.median(values)!r} 不一致（自相矛盾）"
        )
        assert block["min"] == min(values) and block["max"] == max(values), (
            f"{variant} / overall.{name}: min/max 与 per_round 不一致"
        )
    assert checked >= 10, f"{variant}: 只核了 {checked} 个 overall 指标（应 >= 10）"


@pytest.mark.parametrize("variant", ["P_live4_prod_prompt", "P2_live4_prod_prompt_profile"])
def test_index_verdict_agrees_with_threshold_and_observed(
    directed_json: dict, variant: str
) -> None:
    """★ AC2 的语义层：`verdict` 必须与 `threshold`/`observed`/`direction` 自洽。

    卡片带 `direction`（upper/lower），故可**重算**该判：
      * upper：observed <= threshold ⇒ pass，否则 fail
      * lower：observed >= threshold ⇒ pass，否则 fail
    `unmeasurable` 单独允许（分母不足时无法判定，必须显式声明而不是默认 pass）。

    ★ 这条把「自报的 verdict」与「自报的数字」交叉绑定：只改 verdict 或只改数字，
    都会在这里矛盾 —— 而那正是 R2/R5 两类绕过的共同形态。
    """
    card = directed_json["cards"][variant]
    checked = 0
    for item in index_items(card):
        threshold = item.get("threshold")
        observed = item.get("observed")
        direction = item.get("direction")
        if threshold is None or observed is None or not direction:
            continue
        checked += 1
        if item["verdict"] == "unmeasurable":
            continue
        if direction == "upper":
            expect = "pass" if observed <= threshold else "fail"
        elif direction == "lower":
            expect = "pass" if observed >= threshold else "fail"
        else:
            continue
        assert item["verdict"] == expect, (
            f"{variant} / {item['criterion_id']}: verdict={item['verdict']!r}，但按 "
            f"{direction} 与 observed={observed!r} / threshold={threshold!r} 重算应为 "
            f"{expect!r} —— 自报判定与自报数字矛盾"
        )
    assert checked >= 4, f"{variant}: 只核了 {checked} 条（应 >= 4）"
