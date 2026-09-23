# ruff: noqa: RUF001, RUF002, RUF003
"""定向轴评测的**输入来源**：入库产物 → 逐轮跑分行（工单 #157）.

为什么单独一个模块
------------------
「怎么把产物读回逐轮行」与「怎么算、怎么判、怎么印」是三种不同的变化原因
（``code-review-checklist.md`` 的 Divergent Change 条）：产物形状变了只该改这里。
另外它也是卡片与阈值核验**共用**的一段（两者都要读产物），
所以它不该住在任何一方里面。

★ 读侧必须能把 ``per_round_rows`` 的**紧凑投影**还原成带 token 证据的行 ——
没有 ``n_tokens`` / ``first_token_id``，产物在读侧就分不开
「模型判定沉默」与「模型什么都没输出」（``</silence>`` 是 special token，
被服务端从 content 剥离后 content 也是空串）。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

from decision_eval_set import production_live_prompt

#: 入库的多轮真机产物（阈值与卡片都以它为准；``doc/research/data/`` 是**入库**目录）。
DEFAULT_ARTIFACT = "doc/research/data/benchmark_production_live_prompt_rounds.json"

#: 仓库根 —— 相对路径的产物按它解析（本模块在 ``services/webinfer/``）。
REPO_ROOT = Path(__file__).resolve().parents[2]


def _include_profile(variant: str) -> bool:
    """该 variant 是否使用带 persona 的组装 prompt.

    与 :func:`decision_eval_score.load_rows_from_results` 同一条规则：
    名字里带 ``profile`` 的就是带 persona 跑的那一份。
    """
    return "profile" in variant or "prod_prompt_profile" in variant


# --- 从产物读回逐轮跑分行 ---------------------------------------------------


def _round_rows_with_evidence(entry: dict, *, source: str) -> list[list[dict]]:
    """把产物里的 ``per_round_rows`` 还原成「每轮一组完整跑分行」.

    ``per_round_rows`` 是 #165 为「产物自足」而落的**投影**（每行只留
    ``id`` / ``expected`` / ``decision`` / ``ok``）。#157 追加了 token 级证据字段
    （``first_token_id`` / ``n_tokens``）—— 没有它，「判定沉默」与「空输出」
    在多轮上就分不开（:mod:`decision_eval_axis` 的 ``quiet_evidence`` 会判
    ``no_token_evidence`` 而不是假装知道）。

    Raises
    ------
        KeyError: 产物里既没有 ``per_round_rows`` 也没有 ``rows`` —— 宁可不给卡片，
            也不拿空轮算出一个像结论的数字。
    """
    stored = entry.get("per_round_rows")
    if stored:
        rounds = []
        for index, rows in enumerate(stored, 1):
            usable = [dict(r) for r in rows if r.get("id")]
            if not usable:
                raise KeyError(f"{source} 的第 {index} 轮没有任何跑分行")
            rounds.append(usable)
        return rounds
    rows = [dict(r) for r in entry.get("rows", []) if r.get("id")]
    if not rows:
        raise KeyError(f"{source} 里既没有 per_round_rows 也没有 rows")
    return [rows]


def load_artifact(path: str | Path, variants: list[str] | None = None) -> dict:
    """读入库的多轮产物，返回 ``{variant: {"rounds": [[row…]…], "meta": {...}}}``.

    Raises
    ------
        FileNotFoundError: 产物不存在（**不**静默给空卡片：缺结果就是没测）。
        KeyError: 指定的 variant 不存在。
    """
    artifact_path = Path(path)
    if not artifact_path.is_absolute():
        artifact_path = REPO_ROOT / artifact_path
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"定向轴记分卡的输入产物不存在：{artifact_path} —— "
            "缺结果等于「没测」，不判绿（这正是本工单要消灭的那类假绿）"
        )
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    results = data.get("results") or {}
    if not results:
        raise KeyError(f"{artifact_path} 里没有任何 variant")
    wanted = list(variants) if variants else list(results)
    missing = [v for v in wanted if v not in results]
    if missing:
        raise KeyError(f"{artifact_path} 里没有 variant {missing}（有 {sorted(results)}）")

    return {
        "path": str(artifact_path),
        "model": data.get("model"),
        "test_set_size": data.get("test_set_size"),
        "decoding": data.get("decoding"),
        "variants": {
            name: {
                "rounds": _round_rows_with_evidence(
                    results[name], source=f"{artifact_path}#{name}"
                ),
                "prompt": production_live_prompt(include_profile=_include_profile(name)),
            }
            for name in wanted
        },
    }
