#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
"""帧链路探针（`scripts/frame_link_probe.mjs`）的**行为测试**（工单 #163）。

为什么这些测试必须存在
----------------------
本探针的输出会变成台账证据（`doc/standards/test-baseline.md`）。**一条假的绿色行
比没有台账更坏** —— #162 的出发点正是「残留审计在静态服务器服务错目录时报 139
假数字，真值 0，而脚本不报错」。帧链路探针面临**同样的病**，本轮实测已撞上四处，
每一条都在下面有对应的**行为**回归（对纯函数 `judgeRound` 断言真实输入输出，
以及真跑探针的 CLI 断言退出码 —— 不靠 grep 源码字符串）：

1. **读错判据层级 ⇒ 假红**：`api_call_ms` 在 `metrics.latency_breakdown_ms.api_call_ms`。
2. **失败轮读到上一轮的成功数字 ⇒ 假绿**：`analyze_image` 异常时返回 `Error: …`
   且**不更新** `last_latency_breakdown_ms`。
3. **拿了别人的响应 ⇒ 假绿**：必须按 `frame_seq` 配对。
4. **装置不可用时报绿** ⇒ 必须退出 2。

运行：
    python -m pytest scripts/tests/test_frame_link_probe.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "frame_link_probe.mjs"
NODE = shutil.which("node")

# ★ 刻意**不用** `skipif(node is None)`：那会让整个文件在 node 缺失时静默变绿
#   （「全 skip」看起来与「全通过」一样绿 —— 本仓 §6.4 的 fails-open 一类）。
#   这里取 **fail-closed**：node 不在就判红并说清原因。node 在本机与 CI 的
#   `scripts-tests` job 里都有（该 job 显式 setup-node），故这不是多余的严格。
if NODE is None:  # pragma: no cover - CI 与本机都有 node
    raise RuntimeError(
        "找不到 node：帧链路探针是 .mjs，其行为测试必须有 node。"
        "CI 的 scripts-tests job 已 setup-node；本地请装 node 后重跑。"
        "（此处刻意让收集期就失败，而不是 skip —— skip 会伪装成绿。）"
    )

# ---------------------------------------------------------------------------
# 驱动纯函数 judgeRound（离线；不碰网络/文件）
# ---------------------------------------------------------------------------

_HARNESS = """
import fs from 'node:fs';
import { judgeRound } from %(probe)s;
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = cases.map((c) => judgeRound({
  judged: c.judged,
  windowFrames: c.windowFrames,
  unmatched: c.unmatched || [],
  expectNoResponse: !!c.expectNoResponse,
  requireContent: !!c.requireContent,
  prompt: c.prompt === undefined ? null : c.prompt,
}));
console.log(JSON.stringify(out));
"""


def judge_cases(cases: list[dict], tmp_path: Path) -> list[dict]:
    """在 node 里对 judgeRound 跑若干用例，返回 `[{problems, notes}, …]`。

    ★ 经**临时脚本文件**（而不是 `node -e`）驱动：`-e` 下 `process.argv`
    的排布与普通脚本不同，实测会把用例 JSON 读成 undefined —— 那会让所有
    断言在「输入根本没送到」的情况下通过或失败，等于没测。
    """
    harness = tmp_path / "judge_harness.mjs"
    harness.write_text(_HARNESS % {"probe": json.dumps(PROBE.as_uri())}, encoding="utf-8")
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(cases), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(cases_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"node 失败：{proc.stderr[:600]}"
    parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(parsed) == len(cases), "用例数与结果数不符（输入没完整送到）"
    return parsed


def _healthy(**over):
    """一个「健康轮」的输入：模型真被调用、有内容。"""
    judged = {
        "text": "屏幕上是本仓库的编辑器窗口，右侧有一段对话。",
        "frame_seq": 42,
        "metrics": {
            "total_inferences": 7,
            "latency_breakdown_ms": {"api_call_ms": 420.87, "total_ms": 422.49},
            "user_prompt": "请描述当前画面内容，一句话。",
        },
    }
    base = {
        "judged": judged,
        "windowFrames": [{"seq": 42}],
        "unmatched": [],
        "expectNoResponse": False,
        "requireContent": True,
        "prompt": "请描述当前画面内容，一句话。",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 正向：健康轮必须判 PASS（否则探针会造假红，让人不敢信它的绿）
# ---------------------------------------------------------------------------


def test_healthy_round_has_no_problems(tmp_path):
    """★ 先把「正常」钉住：有内容 + api_call_ms 正数 ⇒ 无问题项。

    没有这一条，下面所有「判红」的测试都可能是因为判据恒红而假通过。
    """
    (res,) = judge_cases([_healthy()], tmp_path)
    assert res["problems"] == [], f"健康轮不该有问题项：{res['problems']}"


def test_healthy_round_reads_api_call_ms_from_latency_breakdown(tmp_path):
    """★ 钉住「读错层级 ⇒ 假红」：`api_call_ms` 在 latency_breakdown_ms 下。

    实测：探针首版读 `metrics.api_call_ms`（该键不存在），把一次**成功**推理
    （420.87 ms）判成「模型未被调用」。
    """
    (res,) = judge_cases([_healthy()], tmp_path)
    assert not any("api_call_ms" in p for p in res["problems"]), res["problems"]


def test_missing_latency_breakdown_is_judged_not_silently_passed(tmp_path):
    """判据字段整体缺失 ⇒ 必须报「无法证明模型真被调用」，不得判绿。"""
    case = _healthy()
    case["judged"]["metrics"] = {"total_inferences": 7, "user_prompt": ""}
    (res,) = judge_cases([case], tmp_path)
    assert any("api_call_ms" in p for p in res["problems"]), res["problems"]


def test_zero_api_call_ms_fails(tmp_path):
    """`api_call_ms=0` ⇒ 模型未被真实调用 ⇒ 判红。"""
    case = _healthy()
    case["judged"]["metrics"]["latency_breakdown_ms"] = {"api_call_ms": 0}
    (res,) = judge_cases([case], tmp_path)
    assert res["problems"], "api_call_ms=0 必须判红"


# ---------------------------------------------------------------------------
# 反向：四种真实缺陷形态必须判红
# ---------------------------------------------------------------------------


def test_content_empty_placeholder_fails_with_require_content(tmp_path):
    """★ 本票的核心形态：`Empty model response: stop` ⇒ 判红。

    这是 2026-09-22 首次真机测与 #163 本轮都复现到的读数。**刻意不带
    `--require-content`** —— 它是「用户可见面出现了非答案」，不是「内容不合格」。
    """
    case = _healthy(requireContent=False)
    case["judged"]["text"] = "Empty model response: stop"
    (res,) = judge_cases([case], tmp_path)
    assert any("Empty model response" in p for p in res["problems"]), res["problems"]


def test_placeholder_never_coexists_with_all_pass(tmp_path):
    """★ 台账行不得出现「ALL PASS + 诊断串」这种自相矛盾的读数。

    #163 实测差点产出这一行（首版判据只对 `--require-content` 档判红，
    而「无 prompt」那一轮不带该旗标）⇒ 后人会把「内容空」误读成「链路正常」。
    """
    case = _healthy(requireContent=False)
    case["judged"]["text"] = "Empty model response: stop"
    (res,) = judge_cases([case], tmp_path)
    assert res["problems"], "出现诊断串时 problems 必须非空（否则会被印成 ALL PASS）"


def test_empty_text_fails_with_require_content(tmp_path):
    """返回文本为空 ⇒ 判红（`--require-content` 档）。"""
    case = _healthy()
    case["judged"]["text"] = ""
    (res,) = judge_cases([case], tmp_path)
    assert any("为空" in p for p in res["problems"]), res["problems"]


def test_error_text_fails_even_without_require_content(tmp_path):
    """★ 钉住「失败轮读到上一轮成功数字 ⇒ 假绿」。

    `VLMService.analyze_image` 异常时返回 `f"Error: {e}"` 且**不更新**
    `last_latency_breakdown_ms` ⇒ metrics 里可能还挂着上一轮成功的 `api_call_ms`。
    只看那个数字，8070 挂掉反而会判绿。故错误串必须在**不看 --require-content**
    的情况下也判红 —— 这里刻意把 metrics 造成「看起来完全正常」。
    """
    case = _healthy(requireContent=False)
    case["judged"]["text"] = "Error: Error code: 502 - {'error': {'message': 'Bad Gateway'}}"
    # metrics 故意留着上一轮的成功值（复现真实故障形态）
    (res,) = judge_cases([case], tmp_path)
    assert any("错误串" in p for p in res["problems"]), res["problems"]


def test_error_text_fails_with_metrics_never_updated(tmp_path):
    """更狠的一档：错误轮里 metrics **完全没被更新**（空 breakdown）⇒ 仍判红。"""
    case = _healthy(requireContent=False)
    case["judged"]["text"] = "Error: Error code: 502"
    case["judged"]["metrics"] = {
        "total_inferences": 0,
        "latency_breakdown_ms": {},
        "user_prompt": "",
    }
    (res,) = judge_cases([case], tmp_path)
    assert len(res["problems"]) >= 2, f"应同时报缺 api_call_ms 与错误串：{res['problems']}"


def test_unmatched_response_is_not_used_as_the_round_result(tmp_path):
    """★ 钉住「拿了别人的响应 ⇒ 假绿」。

    应用自身 1fps 采集会持续回响应；若本轮 seq 没有任何配对响应，即便窗口里
    有别的响应，也必须判红（不得拿别人的帧给自己判绿）。
    """
    case = _healthy(judged=None)
    case["unmatched"] = [
        {"frame_seq": 100, "text": "别人的帧的回复", "metrics": {}},
        {"frame_seq": 101, "text": "另一条", "metrics": {}},
    ]
    (res,) = judge_cases([case], tmp_path)
    assert any("未收到任何**配对**" in p for p in res["problems"]), res["problems"]


def test_no_frames_sent_fails(tmp_path):
    """装置未生效（一帧都没发出去）⇒ 判红，不得判绿。"""
    case = _healthy(judged=None, windowFrames=[])
    (res,) = judge_cases([case], tmp_path)
    assert any("未发出任何帧" in p for p in res["problems"]), res["problems"]


def test_total_inferences_zero_fails(tmp_path):
    """推理计数为 0 ⇒ 判红。"""
    case = _healthy()
    case["judged"]["metrics"]["total_inferences"] = 0
    (res,) = judge_cases([case], tmp_path)
    assert any("total_inferences" in p for p in res["problems"]), res["problems"]


# ---------------------------------------------------------------------------
# 负控轮：两个方向都要能判
# ---------------------------------------------------------------------------


def test_negative_control_passes_only_when_no_response_arrives(tmp_path):
    """负控轮收到**零**配对响应 ⇒ PASS（这正是负控成立的样子）。"""
    case = _healthy(judged=None, expectNoResponse=True)
    (res,) = judge_cases([case], tmp_path)
    assert res["problems"] == [], res["problems"]


def test_negative_control_fails_when_a_response_still_arrives(tmp_path):
    """★ 负控反向：若停掉 8070 后**仍**收到配对响应 ⇒ 负控失败，必须判红。

    没有这一条，负控就是个永远为真的摆设（fail-open）。
    """
    case = _healthy(expectNoResponse=True)
    (res,) = judge_cases([case], tmp_path)
    assert any("负控失败" in p for p in res["problems"]), res["problems"]


def test_unmatched_responses_are_only_noted_not_judged(tmp_path):
    """不配对的响应只进 notes，不进 problems（不干扰判定，但不静默丢弃）。"""
    case = _healthy()
    case["unmatched"] = [{"frame_seq": 999, "text": "x", "metrics": {}}]
    (res,) = judge_cases([case], tmp_path)
    assert res["problems"] == []
    assert any("999" in n or "不等于本轮" in n for n in res["notes"]), res["notes"]


# ---------------------------------------------------------------------------
# CLI 层：装置不可用 ⇒ 退出 2（绝不给「通过」）
# ---------------------------------------------------------------------------


def _run_probe(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """跑探针（`--cdp 1` 指向必然连不上的端口，保证它不做真机动作）。"""
    return subprocess.run(
        [NODE, str(PROBE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=timeout,
        check=False,
    )


def test_unreachable_cdp_exits_2_not_0():
    """★ CDP 连不上 ⇒ 退出码 2（装置不可用），**不是** 0。

    装置缺失时返回 0 就等于「没测到却报通过」，与 139 假数字同病。
    """
    proc = _run_probe("--cdp", "1", "--json", "--observe-ms", "1000")
    assert proc.returncode == 2, f"期望 2，实得 {proc.returncode}；stderr={proc.stderr[:400]}"


def test_unreachable_cdp_explains_itself():
    """退出 2 时必须在**输出**里说清是装置不可用（不得静默）。"""
    proc = _run_probe("--cdp", "1", "--json", "--observe-ms", "1000")
    assert "装置不可用" in (proc.stderr + proc.stdout)


def test_unknown_flag_fails_loudly():
    """未知旗标 ⇒ 不得静默忽略（拼错旗标会得到一个看似正常的轮次）。"""
    proc = _run_probe("--cdp", "1", "--definitely-not-a-flag")
    assert proc.returncode == 2
    assert "unknown flag" in (proc.stderr + proc.stdout)


def test_missing_frame_file_exits_2(tmp_path):
    """`--frame-file` 指向不存在的文件 ⇒ 退出 2 并说清怎么补前置。"""
    proc = _run_probe("--cdp", "1", "--json", "--frame-file", str(tmp_path / "nope.jpg"))
    assert proc.returncode == 2
    assert "frame-file" in (proc.stderr + proc.stdout) or "不存在" in (proc.stderr + proc.stdout)


def test_frame_file_dimensions_are_read_from_the_jpeg(tmp_path):
    """★ `--frame-file` 档必须报出**真实**宽高，不得印 0x0。

    采集参数（分辨率）是本票 AC4 明确要求记录的证据。`--frame-file` 档只有字节，
    没有 screen_capture.js 在 payload 里另带的 width/height，故必须解析 JPEG 的
    SOF 段 —— 不解析就会在台账行里印 `0x0`，把一项证据变成噪声。
    """
    # 造一张**合成**小 JPEG（仅用于测解析函数；真实帧取证不用合成帧）
    jpeg = _minimal_jpeg(37, 11)
    frame = tmp_path / "probe.jpg"
    frame.write_bytes(jpeg)

    harness = tmp_path / "size_harness.mjs"
    harness.write_text(
        "import fs from 'node:fs';\n"
        f"import {{ jpegSize }} from {json.dumps(PROBE.as_uri())};\n"
        "console.log(JSON.stringify(jpegSize(fs.readFileSync(process.argv[2]))));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(harness), str(frame)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[:400]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert (got["w"], got["h"]) == (37, 11), got


# ---------------------------------------------------------------------------
# 配对：必须按 frame_seq 挑「本轮那一帧」的响应
# ---------------------------------------------------------------------------


def pick_cases(cases: list, tmp_path: Path) -> list[dict]:
    """驱动纯函数 `pickPairedResponse`（离线）。"""
    harness = tmp_path / "pick_harness.mjs"
    harness.write_text(
        "import fs from 'node:fs';\n"
        f"import {{ pickPairedResponse }} from {json.dumps(PROBE.as_uri())};\n"
        "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
        "console.log(JSON.stringify(cases.map((c) => {\n"
        "  const r = pickPairedResponse(c.responses, c.seq);\n"
        "  return { judged: r.judged ? r.judged.frame_seq : null, unmatched: r.unmatched.length };\n"
        "})));\n",
        encoding="utf-8",
    )
    cases_file = tmp_path / "pick_cases.json"
    cases_file.write_text(json.dumps(cases), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(cases_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"node 失败：{proc.stderr[:600]}"
    parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(parsed) == len(cases)
    return parsed


def test_paired_response_picks_only_the_rounds_own_frame(tmp_path):
    """★ 钉住「拿了别人的响应 ⇒ 假绿」：必须只挑 frame_seq === 本轮 seq 的那条。

    实测形态：应用自身 1fps 采集在推帧，一轮里 `frames_sent_in_window=1`
    但 `vlm_responses` 有 6 条。不配对就会拿别的帧的响应给自己判绿。

    这条测试打在**纯函数** `pickPairedResponse` 上 —— 即 main() 实际调用的那个
    （此前只测了 `judgeRound` 的输入，等于没测配对逻辑本身）。
    """
    (res,) = pick_cases(
        [
            {
                "seq": 42,
                "responses": [
                    {"frame_seq": 7, "text": "别人的帧"},
                    {"frame_seq": 42, "text": "我的帧"},
                    {"frame_seq": 99, "text": "也是别人的"},
                ],
            }
        ],
        tmp_path,
    )
    assert res["judged"] == 42, res
    assert res["unmatched"] == 2, res


def test_paired_response_returns_none_when_only_foreign_frames_replied(tmp_path):
    """★ 反向：窗口里全是别人的响应 ⇒ 必须返回 None（而不是退而取一条）。

    这是「假绿」最容易溜进来的形态 —— 一旦 `judged` 非空，`judgeRound` 就会
    开始读它的 metrics 并可能判绿。
    """
    (res,) = pick_cases(
        [{"seq": 42, "responses": [{"frame_seq": 7}, {"frame_seq": 8}]}],
        tmp_path,
    )
    assert res["judged"] is None, res
    assert res["unmatched"] == 2, res


def test_paired_response_takes_the_last_match_not_the_first(tmp_path):
    """同 seq 收到多条时取**最后一条**（最新读数；与 `slice(-1)[0]` 语义一致）。"""
    (res,) = pick_cases(
        [{"seq": 5, "responses": [{"frame_seq": 5, "text": "旧"}, {"frame_seq": 5, "text": "新"}]}],
        tmp_path,
    )
    assert res["judged"] == 5
    assert res["unmatched"] == 0


def test_paired_response_tolerates_empty_input(tmp_path):
    """空输入 / 非数组 ⇒ 不得抛（探针在窗口内没收到任何响应是**正常形态**）。"""
    (res,) = pick_cases([{"seq": 1, "responses": []}], tmp_path)
    assert res["judged"] is None
    assert res["unmatched"] == 0


def _minimal_jpeg(width: int, height: int) -> bytes:
    """拼一个只含 SOI + SOF0 + EOI 的最小 JPEG（够 jpegSize 解析即可）。"""
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")  # 段长
        + b"\x08"  # 精度
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"  # 分量数
        + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"
