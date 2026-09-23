# ruff: noqa: RUF001, RUF002
"""定向轴阈值的**出处核验**（工单 #157）.

为什么不把阈值核验留在卡片模块里
--------------------------------
卡片的职责是「出分数」；阈值核验的职责是「证明那些分数所依据的线有出处、
且不会漂移」。两者变化的原因不同（评测口径 vs 基线重定），按
``code-review-checklist.md`` 的 Divergent Change 条本就不该同居一室；
合在一处还让卡片模块越过 ``coding-standards.md`` §7 的 1000 行线。

★ 核心纪律：阈值取自**冻结快照**，**不**从活产物自动派生 ——
否则一次退化 + 一次重跑就能把线一起挪走，那条线便永远拦不住东西
（fail-open，且看起来在工作）。

Run: cd services/webinfer && python -m decision_eval_bounds --verify
"""

from __future__ import annotations

import hashlib
import math
import statistics
from pathlib import Path

from decision_eval_axis import (
    aggregate_axis_blocks,
    axis_block,
)
from decision_eval_criteria import BASELINE_SNAPSHOT, BOUNDS
from decision_eval_set import SUBSET_GENERALIZATION
from decision_eval_sources import DEFAULT_ARTIFACT, REPO_ROOT, load_artifact

# --- 阈值的可证伪核验 -------------------------------------------------------


def derive_bounds(snapshot: dict | None = None) -> dict[str, float]:
    """★ 从**冻结基线快照**重算每个阈值（**不**从活产物派生）.

    取法：每个 variant 的逐轮读数 → ``ceil(median + pstdev)``（容纳基线自身的抖动），
    再跨 variant 取**最大**值。

    ⚠️ **不从当前入库产物派生** —— 那是循环的：门禁要挡质量退化，而退化若伴随
    一次产物重跑，阈值就会跟着退化一起动，那条线永远拦不住东西。快照是冻结的，
    产物重跑只会被 :func:`bound_drift_report` **报出来**，不会悄悄改线。

    两个刻意不派生的阈值（``not_for_me_precision_pct``）原样返回声明值，
    并在 :func:`explain_bounds` 里明说它们是**保守取值而非实测**：
    真机 not-for-me 预测数只有 1–7 例，样本不足以定阈值。
    把保守取值伪造成「派生」正是本工单要消灭的那类谎。
    """
    data = BASELINE_SNAPSHOT if snapshot is None else snapshot
    derived: dict[str, float] = {}
    for key, per_variant in data["series"].items():
        candidates = [
            math.ceil(statistics.median(values) + statistics.pstdev(values))
            for values in per_variant.values()
            if values
        ]
        derived[key] = float(max(candidates))
    derived["not_for_me_precision_pct"] = float(BOUNDS["not_for_me_precision_pct"])
    return derived


def explain_bounds(snapshot: dict | None = None) -> list[dict]:
    """逐条说明阈值：声明值 / 从快照重算值 / 一致与否 / 出处."""
    derived = derive_bounds(snapshot)
    rows = []
    for key, declared in BOUNDS.items():
        recomputed = derived.get(key)
        rows.append(
            {
                "bound": key,
                "declared": declared,
                "recomputed_from_snapshot": recomputed,
                "matches": recomputed == declared,
                "derived": not key.startswith("not_for_me_precision"),
                "source": (
                    "保守下界（真机 not-for-me 预测数只 1–7 例，样本不足以定阈值）"
                    if key.startswith("not_for_me_precision")
                    else f"max over variants of ceil(median + pstdev)，取自 {BASELINE_SNAPSHOT['artifact']} 的冻结快照"
                ),
            }
        )
    return rows


def verify_bounds(snapshot: dict | None = None) -> tuple[bool, list[dict]]:
    """核验「声明的阈值 == 从冻结快照重算的阈值」。不一致即报错（返回 False）."""
    rows = explain_bounds(snapshot)
    return all(row["matches"] for row in rows if row["derived"]), rows


def bound_drift_report(artifact_path: str | Path = DEFAULT_ARTIFACT) -> dict:
    """★ 活产物是否已经偏离阈值所依据的那份基线快照.

    这不是错误检查（重跑产物是**正当操作**），而是**可见性**检查：
    一次「退化 + 重跑」若无人看见，就会变成「线自己挪了」。
    故本函数把三件事一起给出：产物 sha 是否等于快照绑定的 sha、
    按新产物算阈值会是多少、以及差值。

    Returns
    -------
        ``{artifact, sha_matches_snapshot, snapshot_sha256, artifact_sha256,
        declared, recomputed_from_artifact, deltas, note}``。
        产物缺失时 ``artifact_sha256`` 与 ``recomputed_from_artifact`` 为 ``None``
        （缺结果不是通过）。
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.is_absolute():
        artifact_path = REPO_ROOT / artifact_path
    report: dict = {
        "artifact": str(artifact_path),
        "snapshot_sha256": BASELINE_SNAPSHOT["artifact_sha256"],
        "declared": dict(BOUNDS),
        "note": (
            "阈值取自**冻结快照**，不随产物自动漂移 —— 否则一次退化 + 一次重跑"
            "就能把线一起挪走。本报告只做可见性：产物变了就报出来。"
        ),
    }
    if not artifact_path.exists():
        report.update(
            {
                "artifact_sha256": None,
                "sha_matches_snapshot": False,
                "recomputed_from_artifact": None,
                "deltas": None,
                "exists": False,
            }
        )
        return report

    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    report["artifact_sha256"] = digest
    report["sha_matches_snapshot"] = digest == BASELINE_SNAPSHOT["artifact_sha256"]
    report["exists"] = True
    try:
        patterns = _artifact_bound_series(artifact_path)
    except (KeyError, ValueError) as exc:
        report["recomputed_from_artifact"] = None
        report["deltas"] = None
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    recomputed = {
        key: float(
            max(
                math.ceil(statistics.median(values) + statistics.pstdev(values))
                for values in per_variant.values()
                if values
            )
        )
        for key, per_variant in patterns.items()
    }
    recomputed["not_for_me_precision_pct"] = BOUNDS["not_for_me_precision_pct"]
    report["recomputed_from_artifact"] = recomputed
    report["deltas"] = {
        key: round(recomputed[key] - BOUNDS[key], 3) for key in BOUNDS if key in recomputed
    }
    return report


def _artifact_bound_series(artifact_path: Path) -> dict[str, dict[str, list[float]]]:
    """从活产物抽出与快照同形的逐轮序列（供漂移比较用）."""
    loaded = load_artifact(artifact_path)
    mapping = {
        "nondirected_spurious_response_rate_pct": (
            ("overall",),
            "nondirected_spurious_response_rate_pct",
        ),
        "directed_nonresponse_rate_pct": (("overall",), "directed_nonresponse_rate_pct"),
        "cost_index": (("overall",), "cost_index"),
        "not_for_me_recall_pct_generalization": (
            ("by_subset", SUBSET_GENERALIZATION),
            "not_for_me_recall_pct",
        ),
    }
    out: dict[str, dict[str, list[float]]] = {}
    for key, (scope_path, metric) in mapping.items():
        per_variant: dict[str, list[float]] = {}
        for name, payload in loaded["variants"].items():
            blocks = [axis_block(rows, payload["prompt"]) for rows in payload["rounds"]]
            aggregated = blocks[0] if len(blocks) == 1 else aggregate_axis_blocks(blocks)
            scope: object = aggregated
            for part in scope_path:
                scope = scope[part]  # type: ignore[index]
            series = scope[metric]  # type: ignore[index]
            per_variant[name] = [float(v) for v in series["per_round"] if v is not None]
        out[key] = per_variant
    return out
