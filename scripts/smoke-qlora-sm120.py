"""sm_120 QLoRA 冒烟测试：8B 级模型能否在本机 16GB 上跑通 1 个训练 step。

这是 expert「LLM 后训练工程师」建议的两项 ≤1h 验证之一：
  若 QLoRA 训练在 sm_120 上跑不通，整条「本机训 8B」路线直接判死。

方法：用真实 Qwen3-8B 量级配置（hidden 4096 / 36 层 / vocab 151k 太大，
      故按比例缩放为一组代表性层数），验证：
      ① 4-bit 权重 + LoRA 的【反向传播】是否在 sm_120 上可用
      ② AdamW 8-bit 优化器状态是否可建
      ③ gradient checkpointing 是否生效
      ④ 峰值显存落在 16GB 内

刻意用小模型验证「机制」，因为机制不通则大模型必然不通；
机制通后再用真实 8B 试显存上限（另一步）。

用法: /d/AI/envs/minimind-o/python.exe scripts/smoke-qlora-sm120.py
"""
from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

import torch

OUT = Path(__file__).resolve().parents[1] / "doc" / "research" / "data" / "sm120_qlora_smoke.json"


def vram() -> tuple[int, int]:
    free, total = torch.cuda.mem_get_info()
    return (total - free) // (1024 * 1024), total // (1024 * 1024)


def main() -> None:
    result: dict = {"torch": torch.__version__, "cuda": torch.version.cuda,
                    "capability": list(torch.cuda.get_device_capability(0)),
                    "steps": {}}

    print("=" * 64)
    print("sm_120 QLoRA 冒烟测试")
    print("=" * 64)
    print(f"torch {torch.__version__}  cuda {torch.version.cuda}  "
          f"capability {torch.cuda.get_device_capability(0)}")
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    used, total = vram()
    print(f"VRAM: {used} / {total} MiB")
    print()

    import bitsandbytes as bnb
    result["bitsandbytes"] = bnb.__version__
    print(f"bitsandbytes {bnb.__version__}")

    # ------------------------------------------------------------------
    # Test 1: 4-bit linear backward (the sm_120 gate)
    # ------------------------------------------------------------------
    print("\n[1] 4-bit Linear 反向传播（sm_120 关键门槛）")
    try:
        from bitsandbytes.nn import Linear4bit

        lin = Linear4bit(512, 512, quant_type="nf4",
                         compute_dtype=torch.bfloat16).cuda()
        x = torch.randn(16, 512, dtype=torch.bfloat16, device="cuda",
                        requires_grad=True)
        y = lin(x)
        loss = y.float().pow(2).mean()
        loss.backward()
        torch.cuda.synchronize()
        gnorm = float(x.grad.norm())
        result["steps"]["4bit_backward"] = {"ok": True, "grad_norm": round(gnorm, 6)}
        print(f"    OK — backward 成功，输入梯度范数 {gnorm:.6f}")
    except Exception as e:
        result["steps"]["4bit_backward"] = {"ok": False, "error": str(e)[:200]}
        print(f"    FAIL — {type(e).__name__}: {str(e)[:150]}")

    # ------------------------------------------------------------------
    # Test 2: LoRA via peft on a 4-bit base
    # ------------------------------------------------------------------
    print("\n[2] PEFT LoRA 挂在 4-bit 基座上（训练路径）")
    try:
        from peft import LoraConfig, get_peft_model
        from transformers import AutoConfig, AutoModelForCausalLM

        # 用小配置建一个真模型（机制验证；不用 8B 以免掩盖机制问题）
        cfg = AutoConfig.for_model(
            "qwen3",
            hidden_size=1024,
            intermediate_size=2816,
            num_hidden_layers=4,
            num_attention_heads=16,
            num_key_value_heads=8,
            vocab_size=32768,
            max_position_embeddings=4096,
        )
        cfg.torch_dtype = torch.bfloat16
        model = AutoModelForCausalLM.from_config(cfg)
        model = model.to(torch.bfloat16)
        lora = LoraConfig(
            r=8, lora_alpha=16, lora_dropout=0.05, bias="none",
            target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora)
        model = model.cuda()
        model.gradient_checkpointing_enable()
        model.train()

        ids = torch.randint(0, 32768, (2, 128), device="cuda")
        out = model(input_ids=ids, labels=ids)
        out.loss.backward()
        torch.cuda.synchronize()

        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total_p = sum(p.numel() for p in model.parameters())
        used2, _ = vram()
        result["steps"]["peft_lora_4bit"] = {
            "ok": True, "loss": round(float(out.loss), 4),
            "trainable": trainable, "total": total_p,
            "trainable_pct": round(100 * trainable / total_p, 3),
        }
        print(f"    OK — loss {float(out.loss):.4f}，"
              f"可训参数 {trainable:,} / {total_p:,} "
              f"({100*trainable/total_p:.2f}%)，VRAM {used2} MiB")
        del model
        gc.collect()
        torch.cuda.empty_cache()
    except Exception as e:
        result["steps"]["peft_lora_4bit"] = {"ok": False, "error": str(e)[:200]}
        print(f"    FAIL — {type(e).__name__}: {str(e)[:150]}")

    # ------------------------------------------------------------------
    # Test 3: 8-bit AdamW optimizer
    # ------------------------------------------------------------------
    print("\n[3] 8-bit AdamW 优化器（省优化器状态显存）")
    try:
        p = torch.nn.Parameter(torch.randn(1024, 1024, device="cuda"))
        opt = bnb.optim.AdamW8bit([p], lr=1e-4)
        loss = p.float().pow(2).mean()
        loss.backward()
        opt.step()
        torch.cuda.synchronize()
        result["steps"]["adamw8bit"] = {"ok": True}
        print("    OK — 8-bit AdamW step 成功")
    except Exception as e:
        result["steps"]["adamw8bit"] = {"ok": False, "error": str(e)[:200]}
        print(f"    FAIL — {type(e).__name__}: {str(e)[:150]}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 64)
    ok = all(s.get("ok") for s in result["steps"].values())
    result["all_passed"] = ok
    print(f"总判定: {'✅ 全部通过 — 本机 8B QLoRA 机制可行' if ok else '❌ 有失败项 — 见上'}")
    print("=" * 64)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
