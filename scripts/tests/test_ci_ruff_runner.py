# ruff: noqa: RUF001, RUF002, RUF003
"""CI ruff 运行器 —— 行为测试（#158 收口时新增）.

★ 为什么这个文件存在
--------------------
`/implement #158` 在 `services/webui/tests/` 留了一个 `# noqa: PLC0415`，
它在 **webui 自己的 ruff 配置**下变成 `RUF100 unused-directive` ⇒ CI 判红。
而我 push 前只验了 `services/webinfer` —— **本地比 CI 更宽松**，于是绿着推、
红了才发现。这正是 #151 已付费学过的形态（「CI 比裸跑更宽松，不是更严」）。

根因很具体也很好笑：每个 ruff job 各带自己的 `--extend-ignore`，
且 webui 从**自己的 working-directory** 跑（因而用**它自己的** `[tool.ruff.lint]`）。
**手抄那些 ignore 集一定会抄错** —— 实测三次手抄给出三个不同答案，
其中两次是在 `memory-store` / `asr` / `tts` 上**假红**。

故本文件钉住四件事，而不是靠人记得：

1. **命令是从 workflow 提取的**，不是重打的 —— 因而**不可能与 CI 漂移**；
2. **提取为空 ⇒ 判红**（解析器或 workflow 形状变了时，绿必须是不可达的）；
3. **workflow 缺失 ⇒ 判红**（「没有可检查的东西」不得读成「通过」）；
4. **加入一个真失败 ⇒ 退出码非 0**（负控：证明它真的会红）。

Run: python -m pytest scripts/tests/test_ci_ruff_runner.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_ci_ruff as runner  # noqa: E402

# --- 1. 提取：命令来自 workflow，不是重打的 ---------------------------------


def test_extracts_a_plausible_number_of_commands():
    """提取必须真的抓到命令（不是 0、也不是 1 条）.

    ★ 断言「数量 > 1」而不是写字面量：workflow 增减 job 是本脚本**应当**
    跟随的事，钉死数字会让它每次都假红 —— 而假红会诱使人改断言，
    那比漏检更危险（#158 的负控里已就同一形态写过一次）。

    ★ 但「> 1」本身**不足以**证明没漏步：旧版把一步静默丢掉时，
    这个断言照样通过（13 > 1）。故期望数必须另有来源，见
    :func:`test_real_workflow_extracts_every_ruff_step`。
    """
    jobs = runner.extract_commands()
    assert len(jobs) > 1, f"只提取到 {len(jobs)} 条命令 —— 解析器或 workflow 形状变了"
    assert all(name.startswith("Ruff") for name, _wd, _cmd in jobs)


def test_every_command_targets_ruff():
    """每条提取出的命令都必须是 ruff 调用（防止误抓别的 run: 行）."""
    for name, _wd, cmd in runner.extract_commands():
        assert cmd.startswith("ruff "), f"{name} 提取到的不是 ruff 命令: {cmd!r}"


def test_webui_is_linted_from_its_own_directory():
    """★ 这是那次事故的**具体根因**，故单独钉住.

    webui 的 ruff job 带 ``working-directory: services/webui``，因而解析的是
    **webui 自己的** `[tool.ruff.lint]`（`line-length=100`、`target py310`），
    与仓库根配置**不同**。从仓库根跑 ``ruff check services/webui`` 会得到
    另一个答案 —— 那正是不够的那次验证。
    """
    webui = [
        (name, wd, cmd)
        for name, wd, cmd in runner.extract_commands()
        if "webui" in name
    ]
    assert webui, "workflow 里找不到 webui 的 ruff job"
    for name, wd, _cmd in webui:
        assert wd == "services/webui", (
            f"{name} 的 working-directory 是 {wd!r}，不再是 services/webui —— "
            "本脚本因而会在错的配置下跑它"
        )


def test_ignore_sets_are_carried_through_verbatim():
    """★ ``--extend-ignore`` 必须**原样**带过来.

    ★ 这是本脚本存在的全部理由：每个 job 的 ignore 集不同，手抄必错
    （实测三次抄出三个答案）。若提取把它们丢掉了，脚本就会比 CI 更严
    （假红）或更松（漏检），两种都错。
    """
    commands = [cmd for _n, _w, cmd in runner.extract_commands()]
    with_ignore = [c for c in commands if "--extend-ignore" in c]
    assert len(with_ignore) >= 5, (
        f"只有 {len(with_ignore)} 条命令带 --extend-ignore —— 提取把它们丢了"
    )
    # 至少有一条真的带着一串规则码（不只是空标志）。
    assert any(len(c.split("--extend-ignore", 1)[1].strip()) > 5 for c in with_ignore), (
        "--extend-ignore 后面的规则码没有被带出来"
    )


# --- 2 & 3. 缺输入 ⇒ 判红（fail-closed） -----------------------------------


def test_missing_workflow_is_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture):
    """★ workflow 缺失 ⇒ 判红，且**明说原因**.

    ★ 「没有可检查的东西」不得读成「通过」—— 本仓最贵的那一类缺陷。
    """
    missing = tmp_path / "quality.yml"
    with pytest.raises(FileNotFoundError, match="CI workflow not found"):
        runner.extract_commands(missing)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "extract_commands", lambda *a, **k: (_ for _ in ()).throw(
            FileNotFoundError("CI workflow not found: nope")
        ))
        assert runner.run_all() == 1
    assert "CI workflow not found" in capsys.readouterr().err


def test_empty_extraction_is_a_failure(monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture):
    """★ 提取到 0 条命令 ⇒ 判红（不许「0 条全过」）."""
    monkeypatch.setattr(runner, "extract_commands", lambda *a, **k: [])
    assert runner.run_all() == 1
    err = capsys.readouterr().err
    assert "ZERO ruff commands" in err


# --- 4. 负控：真失败必须让退出码非 0 ----------------------------------------


def _fake_jobs(*commands: str) -> list[tuple[str, str | None, str]]:
    """构造提取结果.

    ★ 每条命令都**带 ``ruff `` 前缀** —— 那是 ``extract_commands`` 的真实契约
    （``run_all`` 会把它拆成 ``python -m ruff …``）。裸写 ``check …`` 会让
    ``python -m check`` 报 "No module named check"，于是**负控测的就不是
    ruff 的退出码，而是夹具自身的错**。实测踩到过。
    """
    return [(f"Ruff lint (fake {i})", None, c) for i, c in enumerate(commands)]


def test_a_failing_command_makes_the_runner_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
):
    """★ 负控：注入一个**真会失败**的 ruff 调用 ⇒ 退出码 1.

    ★ 没有这条，「本脚本是绿的」只证明它跑完了，不证明它会红 ——
    而那正是它要替代的那种「本地比 CI 宽松」。

    ★ 夹具必须是**真违规的文件**，不能是「不存在的路径」：实测 ruff 对
    不存在的路径**退出 0**（`no/such/path.py` → exit 0），故用不存在的路径
    做负控会让这条测试测的是别的东西（甚至直接假绿）。见
    :func:`test_ruff_exits_zero_on_a_missing_path`。
    """
    bad = REPO_ROOT / ".tmp_ci_ruff_negative_control.py"
    bad.write_text("x = undefined_name_xyz\n", encoding="utf-8")
    try:
        monkeypatch.setattr(
            runner,
            "extract_commands",
            lambda *a, **k: _fake_jobs(f"ruff check --select F821 {bad.name}"),
        )
        assert runner.run_all() == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "0/1" in out
    finally:
        bad.unlink(missing_ok=True)


def test_a_passing_command_exits_zero(monkeypatch: pytest.MonkeyPatch):
    """对照：真会通过的命令 ⇒ 退出码 0（防止本脚本恒红）."""
    monkeypatch.setattr(
        runner,
        "extract_commands",
        lambda *a, **k: _fake_jobs("ruff check --select F821 scripts/run_ci_ruff.py"),
    )
    assert runner.run_all() == 0


# --- ★ 一条实测发现：ruff 对不存在的路径退出 0（fail-open） -----------------


def _ruff_available() -> bool:
    """Is ruff importable in THIS interpreter?"""
    import importlib.util

    return importlib.util.find_spec("ruff") is not None


def test_ruff_exits_zero_on_a_missing_path():
    """★ 实测钉住：``ruff check <不存在的路径>`` **退出 0**.

    ★ 这条是**对 ruff 自身行为的取证**，不是对被测代码的断言。它存在的理由：
    一个笔误的路径会让 ruff「lint 了 0 个文件」而报绿 —— 与「门禁在输入缺失时
    判绿」是同一类 fail-open。本脚本因此加了 :func:`runner.preflight` 前置守卫。

    ★★ **ruff 缺席时必须 skip 得明明白白，而不是断言失败。**
    实测（CI run 35835923523）：`scripts-tests` job 当时只装 pytest、不装 ruff，
    于是本测试以「ruff 对不存在的路径不再退出 0」失败 —— 一个**指错方向**的
    告警：真正的原因是「ruff 不存在」（`No module named ruff`），却报成
    「ruff 改了行为」。告警指错方向比不报更坏：它会诱使人去改一条**本来正确**
    的断言或守卫。

    两道保险合起来才有意义：① 本测试在 ruff 缺席时 skip（并说明原因）；
    ② `run_ci_ruff.py` 自身在 ruff 缺席时**判红**（见
    :func:`test_runner_refuses_to_run_without_ruff`），故「缺席」不会被读成绿。
    """
    if not _ruff_available():
        pytest.skip(
            "ruff 未安装在本解释器中 ⇒ 无法取证其行为（不是失败）。"
            "CI 的 scripts-tests job 已显式装上钉版 ruff；本地请确保 ruff 在 PATH。"
        )
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", "no/such/path.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "ruff 对不存在的路径不再退出 0 —— 若它改成非 0，preflight 守卫的前提变了，"
        "请重新评估（但守卫本身仍然正确）"
    )


def test_runner_refuses_to_run_without_ruff(monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture):
    """★ ruff 缺席 ⇒ 运行器**判红**，绝不报绿.

    ★ 这是上一条 skip 的**配对**：一条 skip 若无此配对，就会把「环境缺 ruff」
    静默成一次绿色运行 —— 正是本仓最贵的那类缺陷（缺输入被当成通过）。
    """
    monkeypatch.setattr(runner, "ruff_available", lambda *a, **k: False)
    monkeypatch.setattr(
        runner,
        "extract_commands",
        lambda *a, **k: _fake_jobs("ruff check --select F821 scripts/run_ci_ruff.py"),
    )
    assert runner.run_all() == 1
    err = capsys.readouterr().err
    assert "没有 ruff" in err, err
    assert "无法复现 CI" in err, err


def test_preflight_catches_a_mistyped_path(monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture):
    """★ 前置守卫：目标路径不存在 ⇒ 判红，而不是「lint 了 0 个文件」的假绿."""
    monkeypatch.setattr(
        runner,
        "extract_commands",
        lambda *a, **k: _fake_jobs("ruff check --select F821 no/such/path.py"),
    )
    assert runner.run_all() == 1
    err = capsys.readouterr().err
    assert "前置检查失败" in err
    assert "no/such/path.py" in err


def test_preflight_does_not_mistake_rule_codes_for_paths():
    """★ ``--extend-ignore D101,D102`` 的值是**规则码**，不是路径.

    ★ 不按形状排除它们，前置守卫会把每条带 ``--extend-ignore`` 的命令全判红
    —— 那会让本脚本恒红，进而诱使人拆掉守卫。
    """
    jobs = runner.extract_commands()
    assert runner.preflight(jobs) == [], runner.preflight(jobs)


def test_list_mode_prints_without_running(capsys: pytest.CaptureFixture):
    """``--list`` 只打印提取结果，不跑（供人核对提取是否正确）."""
    assert runner.run_all(list_only=True) == 0
    out = capsys.readouterr().out
    assert "ruff check" in out or "ruff format" in out


# --- 边界：本脚本**不**覆盖其余 job（防误读成「门禁通过」） ----------------


def test_script_documents_that_it_is_ruff_only():
    """★ 脚本必须**自己说清**它只覆盖 ruff job.

    ★ 否则一次绿读会被误读成「质量门禁通过」—— 而 pytest 矩阵 / eslint /
    drift-gate / package-smoke 都有各自的服务清单，本脚本一条都没复现。
    """
    doc = runner.__doc__ or ""
    assert "does NOT cover" in doc or "不覆盖" in doc, (
        "docstring 里没有说明它只覆盖 ruff job"
    )
    for other in ("pytest", "eslint", "drift-gate", "package-smoke"):
        assert other in doc, f"docstring 没点名它不覆盖 {other}"


# --- ★★ 回归：静默丢步（本文件新增的**核心**断言） ---------------------------
#
# 背景：旧版 docstring 自称「cannot drift from CI because it reads CI」。
# 一句对抗式复核证伪了它 —— 旧提取器只认一种拼法（`- name: Ruff …` 紧跟
# 单行 `run: ruff …`），不合的步骤被**静默丢掉**：计数变小、摘要仍写
# `N/N PASS`、退出码仍 0；若丢的正是红的那一步，红就**消失**了。
#
# 下面这组测试用**临时 workflow 夹具**驱动真实的解析路径（不是 monkeypatch
# 掉提取器 —— 那样测的就不是解析），复现那三种变异并断言「判红 + 点名」。


def _write_workflow(tmp_path: Path, body: str) -> Path:
    """把一段 steps 正文包成一个最小可解析的 job，写到临时 .yml."""
    path = tmp_path / "quality.yml"
    path.write_text(
        "jobs:\n"
        "  ruff:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Install ruff\n"
        "        run: pip install ruff==0.15.22\n" + body,
        encoding="utf-8",
    )
    return path


def test_real_workflow_extracts_every_ruff_step():
    """★★ 在**真 workflow**上 ``extracted == expected``，并钉住当前数量 14.

    ★ 这是整个 fail-closed 设计的落点：期望数由 workflow 自身导出
    （``Ruff …`` 命名约定 + 「run 里真的调用了 ruff」这一结构信号），
    若提取器漏掉任何一步，这两个数就会不一致。

    ★ 为什么仍要写死 14（而上面那条测试刻意不写死）：**仅仅**比较
    ``extracted == expected`` 挡不住「ruff 步骤被整段删掉」—— 被删的步骤两边
    都不再计数，等式依旧成立。只有这个字面量能挡住它。代价如模块文档所记：
    新增 ruff 步骤时此断言会假红，必须由人同步这个数字。这是**已知且接受**
    的取舍：假红会诱人改数字，而漏步会静默放过一个红。

    ★ 为什么用 ``audit_workflow`` 而不是 ``extract_commands``：前者即使不匹配
    也会返回完整账本（含未能解析的步骤名），断言失败时能直接打印差异，
    而不是抛异常只留一句「提取不完整」。
    """
    audit = runner.audit_workflow()
    assert audit.unparsed == [], f"有步骤没能解析：{audit.unparsed}"
    assert audit.extracted_step_count == audit.expected_step_count, (
        f"提取到 {audit.extracted_step_count} 步，workflow 按约定有 "
        f"{audit.expected_step_count} 步 —— 有步骤被静默丢掉了"
    )
    # 真 workflow 的实测数量。改动 workflow 时此数字必须同步更新。
    assert audit.expected_step_count == 14, (
        f"真 workflow 的 ruff 步骤数变成了 {audit.expected_step_count}（应为 14）—— "
        "若这是**有意**增减步骤，请同步本数字；若是**无意**，那就刚刚抓到一个漏步"
    )


def test_extract_is_fail_closed_on_a_renamed_step(tmp_path: Path):
    """★ 回归 ①：步骤被改名（不再匹配命名约定）⇒ 仍须抽出，或**判红点名**.

    旧版在这里丢掉整步。注意改名步骤的 ``run:`` **原样带 ruff**，故结构信号
    仍然认得它 —— 这正是双信号设计的意义：一次改名不该让期望数一起缩水。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Format check (renamed, no longer starts with Ruff)\n"
        "        run: ruff format --check services/asr\n",
    )
    jobs = runner.extract_commands(workflow)
    assert [cmd for _n, _w, cmd in jobs] == ["ruff format --check services/asr"], jobs
    assert jobs.expected == 1, "改名后的步骤没被算进期望数 —— 差异会消失、绿会照旧"


def test_extract_handles_a_block_scalar_run(tmp_path: Path):
    """★ 回归 ②：``run: |`` 块标量必须被解析（旧版整步丢弃）."""
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (block scalar)\n"
        "        run: |\n"
        "          ruff check services/asr --extend-ignore D103\n",
    )
    jobs = runner.extract_commands(workflow)
    assert [cmd for _n, _w, cmd in jobs] == ["ruff check services/asr --extend-ignore D103"], jobs
    assert jobs.expected == 1


def test_extract_joins_a_backslash_continued_block(tmp_path: Path):
    """★ 回归 ②b：块标量里的 shell 续行（``\\``）必须合并成一条命令.

    ★ 多行 ruff 命令在 CI 里就是这么写的。若不合并，会得到两条半截命令，
    而其中任何一条都可能**恰好退出 0** —— 于是又一次「跑了但没真跑」。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (continued)\n"
        "        run: |\n"
        "          ruff check services/asr \\\n"
        "            --extend-ignore D103,D101\n",
    )
    jobs = runner.extract_commands(workflow)
    assert [cmd for _n, _w, cmd in jobs] == ["ruff check services/asr --extend-ignore D103,D101"], (
        jobs
    )


def test_extract_handles_python_m_ruff(tmp_path: Path):
    """★ 回归 ③：``python -m ruff …`` 必须被解析（旧版整步丢弃）.

    ★ 归一化必须剥掉启动壳：留下 ``python -m ruff …`` 会让运行器执行
    ``python -m python -m ruff``，而那看起来像「ruff 报错了」。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (module invocation)\n"
        "        run: python -m ruff check services/asr --extend-ignore D103\n",
    )
    jobs = runner.extract_commands(workflow)
    assert [cmd for _n, _w, cmd in jobs] == ["ruff check services/asr --extend-ignore D103"], jobs


def test_extract_refuses_an_unsupported_shape_and_names_the_step(tmp_path: Path):
    """★★ 回归核心：**不支持**的形状必须判红并**点名**，绝不静默跳过.

    ★ 这是缺陷的原始形态：旧版把不合拼法的步骤丢掉，仍打印 `N/N PASS`
    并 exit 0 —— 若丢的正是红的那步，红会**消失**。故这里断言三件事：
    抛 :class:`ExtractionMismatchError`、异常里含**步骤名**、且 ``run_all`` 退出非 0。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Lint with ruff (chained, unsupported)\n"
        "        run: cd services/asr && ruff check .\n",
    )
    with pytest.raises(runner.ExtractionMismatchError) as excinfo:
        runner.extract_commands(workflow)
    message = str(excinfo.value)
    assert "Lint with ruff (chained, unsupported)" in message, (
        f"判红信息里没有点名那个没能解析的步骤：{message}"
    )
    assert "cd services/asr && ruff check ." in message, (
        f"判红信息里没有给出原始 run 内容：{message}"
    )
    # 端到端：整个运行器也必须退出非 0（而不只是底层函数抛异常）。
    assert runner.run_all(workflow_path=workflow) == 1


def test_extract_refuses_two_commands_in_one_step(tmp_path: Path):
    """★ 一个 ``run: |`` 里两条独立命令 ⇒ 判红，而不是拼成一条.

    ★ 拼接会跑出一条 **CI 里不存在**的命令 ── 那是另一种撒谎，
    比丢步更隐蔽（它会以非 0 退出，让人以为「CI 也会红」）。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (two commands)\n"
        "        run: |\n"
        "          ruff check services/asr\n"
        "          ruff format --check services/asr\n",
    )
    with pytest.raises(runner.ExtractionMismatchError) as excinfo:
        runner.extract_commands(workflow)
    assert "Ruff lint (two commands)" in str(excinfo.value)


def test_incomplete_extraction_exits_nonzero(capsys: pytest.CaptureFixture, tmp_path: Path):
    """★★ 端到端负控：提取不完整 ⇒ 退出码 1，且**不打印任何 PASS**.

    ★ 这一条直接对住缺陷的输出层：旧版在丢步时仍打印 `N/N PASS` 并 exit 0。
    故这里断言退出非 0 且 stdout 里**没有**「PASS」字样 —— 一个「丢了一步」
    的运行不得在外观上像一次成功。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (fine)\n"
        "        run: ruff check services/asr\n"
        "      - name: Ruff lint (broken shape)\n"
        "        run: cd services/asr && ruff check .\n",
    )
    assert runner.run_all(workflow_path=workflow) == 1
    captured = capsys.readouterr()
    assert "提取不完整" in captured.err, captured.err
    assert "broken shape" in captured.err, "判红时没有点名丢掉的步骤"
    assert "PASS" not in captured.out, (
        f"提取不完整却仍然打印了 PASS —— 这正是缺陷的输出形态：{captured.out!r}"
    )


def test_expected_count_comes_from_the_workflow_itself(tmp_path: Path):
    """★ 期望数必须**由 workflow 导出**，不是脚本里的常数.

    ★ 若期望数是写死的常数，它就会和 workflow 各自漂移 —— 于是又回到
    「手抄必错」那个原始病根（实测三次抄出三个答案）。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (one)\n"
        "        run: ruff check services/asr\n"
        "      - name: Ruff format check (two)\n"
        "        run: ruff format --check services/asr\n"
        "      - name: Ruff lint (three)\n"
        "        run: ruff check services/tts\n",
    )
    jobs = runner.extract_commands(workflow)
    assert jobs.expected == 3, f"期望数是 {jobs.expected}，应为 3（workflow 里有 3 步）"
    assert len(jobs) == 3


def test_a_non_ruff_step_is_not_counted_as_expected(tmp_path: Path):
    """对照：与 ruff 无关的步骤不得被算进期望数（否则本脚本恒红）.

    ★ 真 workflow 里 ``pip install ruff==0.15.22`` 就长这样 —— `ruff` 后面
    跟的是 `=`。把它算成 ruff 步骤会让正确的 workflow 假红。
    """
    workflow = _write_workflow(
        tmp_path,
        "      - name: Ruff lint (real)\n"
        "        run: ruff check services/asr\n"
        "      - name: Install something else\n"
        "        run: pip install ruff==0.15.22\n",
    )
    jobs = runner.extract_commands(workflow)
    assert jobs.expected == 1, (
        f"期望数 {jobs.expected} 应为 1 —— `pip install ruff==…` 被误算成了 ruff 步骤"
    )
