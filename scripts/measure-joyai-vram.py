"""#145 实测：JoyAI 全栈显存构成逐项分解。

目的：把 ~9.3GB 拆成「必要权重 / 可变配置 / 可用余量」，
回答「JoyAI 能压到多少」，从而反推「能留多少给游戏」。

方法：起 llama-server，用不同参数组合测稳态显存，差值即各配置项的成本。
      不依赖 per-process 显存（本机非管理员，nvidia-smi 报 N/A），
      改用「总占用 − 已知桌面基线」的差分法。

用法:
  /d/AI/envs/joyai-main/python.exe scripts/measure-joyai-vram.py [--quick]

注意：脚本会**反复重启 llama-server**，每次等模型加载完再测。
     请先确保 7060 端口空闲。
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
OUT = ROOT / "doc" / "research" / "data" / "joyai_vram_breakdown.json"
LLAMA_DIR = Path("D:/AI/bin/llama.cpp")
LLAMA = LLAMA_DIR / "llama-server.exe"
GGUF = Path("D:/AI/models/main/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF/"
            "joyai-vl-interaction-preview-iq4_nl-imat.gguf")
MMPROJ = Path("D:/AI/models/main/mmproj/mmproj-joyai-vl-interaction-preview-f16.gguf")
PORT = 7060
HEALTH = f"http://127.0.0.1:{PORT}/health"

# (标签, 覆盖项 dict, 说明)  —— 覆盖项按 key 替换，不做追加，避免歧义
MATRIX: list[tuple[str, dict, str]] = [
    ("base_16384", {}, "基线：n_ctx=16384，全层 GPU（当前生产配置）"),
    ("ctx_8192", {"-c": "8192"}, "上下文减半"),
    ("ctx_4096", {"-c": "4096"}, "上下文 1/4"),
    ("no_mmproj", {"__mmproj__": None}, "纯文本模式：去掉视觉投影器 F16"),
    ("ngl_24", {"-ngl": "24"}, "24/37 层上 GPU，其余 CPU"),
    ("ngl_16", {"-ngl": "16"}, "16/37 层上 GPU"),
]


def vram() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    return int(out)


def kill_llama() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                   capture_output=True, text=True)
    time.sleep(2)


def build_args(overrides: dict) -> list[str]:
    """显式构造参数表；overrides 按 key 覆盖，__mmproj__=None 表示去掉视觉投影器。"""
    opts: dict = {
        "-m": str(GGUF),
        "--mmproj": str(MMPROJ),
        "--host": "127.0.0.1",
        "--port": str(PORT),
        "-c": "16384",
        "-ngl": "999",
        "--parallel": "1",
        "-fit": "off",
    }
    flags = ["--jinja"]
    for k, v in overrides.items():
        if k == "__mmproj__":
            if v is None:
                opts.pop("--mmproj", None)
            else:
                opts["--mmproj"] = v
        elif v is None:
            opts.pop(k, None)
        else:
            opts[k] = v
    args = [str(LLAMA)]
    for k, v in opts.items():
        args += [k, v]
    return args + flags


def start_llama(overrides: dict):
    args = build_args(overrides)
    logf = open(ROOT / "logs" / "vram-probe-llama.log", "w")
    p = subprocess.Popen(args, cwd=str(LLAMA_DIR),
                         stdout=logf, stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(2)
        if p.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as r:
                if b"ok" in r.read():
                    return p
        except Exception:
            continue
    return p if p.poll() is None else None


def sample_steady(n: int = 5, gap: float = 1.0) -> dict:
    vals = []
    for _ in range(n):
        vals.append(vram())
        time.sleep(gap)
    return {"median": int(statistics.median(vals)), "min": min(vals),
            "max": max(vals), "samples": vals}


def main() -> None:
    quick = "--quick" in sys.argv
    matrix = MATRIX[:3] if quick else MATRIX

    print("[vram] === 桌面基线 ===", flush=True)
    kill_llama()
    time.sleep(3)
    baseline = sample_steady(5)
    print("[vram] desktop baseline: median=%d MiB %s"
          % (baseline["median"], baseline["samples"]), flush=True)

    results: dict = {
        "desktop_baseline_mib": baseline,
        "gpu_total_mib": 16311,
        "variants": {},
    }

    for label, overrides, desc in matrix:
        print("\n[vram] === %s (%s) ===" % (label, desc), flush=True)
        kill_llama()
        time.sleep(2)
        p = start_llama(overrides)
        if p is None:
            print("[vram]   FAILED to start", flush=True)
            results["variants"][label] = {
                "desc": desc, "overrides": {k: str(v) for k, v in overrides.items()},
                "error": "start_failed",
            }
            continue
        time.sleep(3)
        st = sample_steady(5)
        delta = st["median"] - baseline["median"]
        props = {}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/props",
                                        timeout=5) as r:
                props = json.loads(r.read())
        except Exception:
            pass
        n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        vision = (props.get("modalities") or {}).get("vision")
        results["variants"][label] = {
            "desc": desc,
            "overrides": {k: str(v) for k, v in overrides.items()},
            "args": build_args(overrides)[1:],
            "steady": st,
            "delta_vs_desktop_mib": delta,
            "n_ctx": n_ctx,
            "vision": vision,
        }
        print("[vram]   steady=%d MiB  delta_vs_desktop=%+d MiB  n_ctx=%s  vision=%s"
              % (st["median"], delta, n_ctx, vision), flush=True)

    kill_llama()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[vram] === SUMMARY ===", flush=True)
    print(json.dumps({k: v for k, v in results.items() if k != "variants"},
                     ensure_ascii=False, indent=2), flush=True)
    print("[vram] written to %s" % OUT, flush=True)


if __name__ == "__main__":
    main()
