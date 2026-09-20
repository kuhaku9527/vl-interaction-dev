"""显存诊断：逐项测量 JoyAI 全栈的显存构成（服务逐个拉起，测增量）。

目的：回答「到底哪里占得多、哪块还能压」。
方法：从干净桌面开始，逐个启动服务并测显存增量（差分法）。
      不依赖 per-process（本机非管理员，nvidia-smi 报 N/A）。

⚠️ 安全设计（吸取上次跑爆教训）：
  - 每步都测显存与 RAM 水位，超阈值立即中止
  - 单步超时保护
  - 结束必清理

用法: /d/AI/envs/joyai-main/python.exe scripts/diagnose-vram.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "research" / "data" / "vram_diagnosis.json"

# 安全阈值
RAM_FLOOR_GIB = 4.0        # 内存低于此值立即中止
VRAM_FLOOR_MIB = 1500      # 显存剩余低于此值立即中止


def vram() -> tuple[int, int]:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    used, free = [int(x.strip()) for x in out.split(",")]
    return used, free


def ram_free_gib() -> float:
    ps = ("(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True)
    try:
        return int(out.stdout.strip()) / 1048576
    except Exception:
        return -1.0


def guard(step: str) -> None:
    used, free = vram()
    ram = ram_free_gib()
    print(f"    [guard] VRAM used={used} free={free} | RAM free={ram:.1f} GiB",
          flush=True)
    if free < VRAM_FLOOR_MIB:
        raise SystemExit(f"中止：显存剩余 {free} MiB < {VRAM_FLOOR_MIB}（{step}）")
    if 0 < ram < RAM_FLOOR_GIB:
        raise SystemExit(f"中止：内存剩余 {ram:.1f} GiB < {RAM_FLOOR_GIB}（{step}）")


def sample(n: int = 4, gap: float = 1.0) -> dict:
    vals = []
    for _ in range(n):
        vals.append(vram()[0])
        time.sleep(gap)
    return {"median": int(statistics.median(vals)), "samples": vals}


def wait_health(port: int, timeout_s: int = 90) -> bool:
    for _ in range(timeout_s // 2):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                        timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False


def kill_all() -> None:
    for name in ("llama-server.exe", "python.exe"):
        pass  # 不误杀用户进程；各服务由调用方显式管理
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                   capture_output=True, text=True)
    time.sleep(2)


def main() -> None:
    print("=" * 70)
    print("显存诊断：JoyAI 全栈逐项构成")
    print("=" * 70)

    kill_all()
    guard("基线")
    base = sample()
    print(f"[1] 桌面基线 = {base['median']} MiB\n", flush=True)

    results: dict = {"desktop_baseline": base, "steps": []}

    # 步骤表：(标签, 端口, 启动命令)
    LLAMA_DIR = Path("D:/AI/bin/llama.cpp")
    llama_args = [
        str(LLAMA_DIR / "llama-server.exe"),
        "-m", "D:/AI/models/main/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF/"
              "joyai-vl-interaction-preview-iq4_nl-imat.gguf",
        "--mmproj", "D:/AI/models/main/mmproj/"
                    "mmproj-joyai-vl-interaction-preview-f16.gguf",
        "--host", "127.0.0.1", "--port", "7060",
        "-c", "16384", "-ngl", "999", "--parallel", "1",
        "-fit", "off", "-ctk", "q8_0", "-ctv", "q8_0",
        "--flash-attn", "on", "--jinja",
    ]

    print("[2] 启动 llama-server（生产配置，含 KV q8_0）…", flush=True)
    logf = open(ROOT / "logs" / "diagnose-llama.log", "w")
    p = subprocess.Popen(llama_args, cwd=str(LLAMA_DIR),
                         stdout=logf, stderr=subprocess.STDOUT)
    if not wait_health(7060):
        print("    llama-server 未就绪，中止", flush=True)
        p.kill()
        sys.exit(1)
    time.sleep(3)
    guard("llama-server")
    st = sample()
    delta = st["median"] - base["median"]
    print(f"    llama-server = {st['median']} MiB（增量 +{delta}）\n", flush=True)
    results["steps"].append({"label": "llama-server (KV q8_0)",
                             "steady": st, "delta": delta})

    # 从日志里取 KV 自报值
    kvinfo = {}
    try:
        log = (ROOT / "logs" / "diagnose-llama.log").read_text(
            encoding="utf-8", errors="ignore")
        for line in log.splitlines():
            if "llama_kv_cache: size =" in line:
                kvinfo["kv_line"] = line.split("I ", 1)[-1].strip()
            if "CUDA0 model buffer size" in line:
                kvinfo["model_buffer"] = line.split("I ", 1)[-1].strip()
            if "CUDA0 compute buffer size" in line:
                kvinfo.setdefault("compute_buffer", []).append(
                    line.split("I ", 1)[-1].strip())
    except Exception as exc:
        kvinfo["error"] = str(exc)
    results["llama_log"] = kvinfo
    print("    llama.cpp 自报：", flush=True)
    for k, v in kvinfo.items():
        print(f"      {k}: {v}", flush=True)

    kill_all()
    guard("收尾")
    results["after_cleanup"] = sample()
    print(f"\n[3] 清理后 = {results['after_cleanup']['median']} MiB", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
