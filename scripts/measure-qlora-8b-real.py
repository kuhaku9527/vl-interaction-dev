"""8B 完整几何 QLoRA 显存实测（回答「我的显卡行不行」）。

此前 expert 估算「QLoRA 需 16.7GB，超卡」，但未实测。
本脚本用 Qwen3-8B 真实几何（36层/hidden 4096/32 heads/8 kv heads/vocab 151936）
逐档加压，测真实峰值显存。

★ 用随机初始化权重（from_config），不下载 8B 权重：
  显存占用由「架构几何」决定，与权重数值无关，故结论可外推。

用法: /d/AI/envs/minimind-o/python.exe scripts/measure-qlora-8b-real.py
"""
from __future__ import annotations

import gc
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import torch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "doc" / "research" / "data" / "qlora_8b_real_vram.json"

# Qwen3-8B 真实几何
GEOM = dict(hidden=4096, layers=36, heads=32, kv_heads=8, vocab=151936)


def vram() -> tuple[int, int]:
    free, total = torch.cuda.mem_get_info()
    return (total - free) // 1048576, total // 1048576


def quantize_4bit(model):
    """Replace nn.Linear with bitsandbytes NF4 layers."""
    import torch.nn as nn
    from bitsandbytes.nn import Linear4bit

    n = 0
    for name, module in list(model.named_modules()):
        if not isinstance(module, nn.Linear) or isinstance(module, Linear4bit):
            continue
        new = Linear4bit(module.in_features, module.out_features,
                         bias=module.bias is not None,
                         quant_type="nf4", compute_dtype=torch.bfloat16)
        with torch.no_grad():
            new.weight = type(new.weight)(module.weight.data.to(torch.bfloat16),
                                          quant_type="nf4")
            if module.bias is not None:
                new.bias = nn.Parameter(module.bias.data.clone())
        parent = model.get_submodule(name.rsplit(".", 1)[0]) if "." in name else model
        setattr(parent, name.rsplit(".", 1)[-1], new)
        n += 1
    return model, n


def trial(seq: int, batch: int, lora_r: int, quantize: bool,
          grad_ckpt: bool, opt8bit: bool) -> dict:
    from peft import LoraConfig, get_peft_model
    from transformers import AutoConfig, AutoModelForCausalLM

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    gc.collect()

    rec = {"seq": seq, "batch": batch, "lora_r": lora_r,
           "quantize": quantize, "grad_ckpt": grad_ckpt, "opt8bit": opt8bit}
    try:
        cfg = AutoConfig.for_model(
            "qwen3", hidden_size=GEOM["hidden"],
            intermediate_size=int(GEOM["hidden"] * 2.75),
            num_hidden_layers=GEOM["layers"],
            num_attention_heads=GEOM["heads"],
            num_key_value_heads=GEOM["kv_heads"],
            vocab_size=GEOM["vocab"], max_position_embeddings=4096,
        )
        model = AutoModelForCausalLM.from_config(cfg).to(torch.bfloat16)

        n_repl = 0
        if quantize:
            model, n_repl = quantize_4bit(model)
        rec["linear_replaced"] = n_repl

        lora = LoraConfig(r=lora_r, lora_alpha=lora_r * 2, lora_dropout=0.05,
                          bias="none", target_modules=["q_proj", "v_proj"],
                          task_type="CAUSAL_LM")
        model = get_peft_model(model, lora).cuda()
        if grad_ckpt:
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()
        model.train()

        used_after, _ = vram()
        rec["vram_after_model"] = used_after
        rec["trainable"] = sum(p.numel() for p in model.parameters() if p.requires_grad)

        params = [p for p in model.parameters() if p.requires_grad]
        if opt8bit:
            import bitsandbytes as bnb
            opt = bnb.optim.AdamW8bit(params, lr=1e-4)
        else:
            opt = torch.optim.AdamW(params, lr=1e-4)

        ids = torch.randint(0, GEOM["vocab"], (batch, seq), device="cuda")
        out = model(input_ids=ids, labels=ids)
        rec["loss"] = round(float(out.loss), 3)
        out.loss.backward()
        opt.step()
        torch.cuda.synchronize()

        rec["peak_mib"] = torch.cuda.max_memory_allocated() // 1048576
        rec["ok"] = True
        del model, opt
    except RuntimeError as exc:
        rec["ok"] = False
        rec["error"] = str(exc)[:200]
        rec["peak_mib"] = torch.cuda.max_memory_allocated() // 1048576
    torch.cuda.empty_cache()
    gc.collect()
    return rec


def main() -> None:
    print("=" * 72)
    print("8B 完整几何 QLoRA 显存实测")
    print("=" * 72)
    _, total = vram()
    print(f"torch {torch.__version__} | {torch.cuda.get_device_name(0)}")
    print(f"显存总量 {total} MiB")
    print(f"几何: {GEOM}\n")

    plan = [
        dict(seq=512, batch=1, lora_r=8, quantize=True, grad_ckpt=True, opt8bit=True),
        dict(seq=1024, batch=1, lora_r=8, quantize=True, grad_ckpt=True, opt8bit=True),
        dict(seq=2048, batch=1, lora_r=16, quantize=True, grad_ckpt=True, opt8bit=True),
        dict(seq=2048, batch=2, lora_r=16, quantize=True, grad_ckpt=True, opt8bit=True),
    ]

    results = []
    for p in plan:
        tag = "seq%-5d bs%d r%-3d %s" % (
            p["seq"], p["batch"], p["lora_r"],
            "4bit+gckpt+8bit" if p["quantize"] else "bf16")
        print(f"[{tag}] 构建中…", flush=True)
        r = trial(**p)
        r["tag"] = tag
        results.append(r)
        if r.get("ok"):
            print(f"    ✅ OK  峰值 {r['peak_mib']:>6} MiB  loss={r['loss']}  "
                  f"可训 {r['trainable']:,}", flush=True)
        else:
            print(f"    ❌ FAIL 峰值 {r.get('peak_mib')} MiB — {r.get('error','')[:120]}",
                  flush=True)
        print(flush=True)

    payload = {"gpu": torch.cuda.get_device_name(0), "total_mib": total,
               "geometry": GEOM, "results": results}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    ok = [r for r in results if r.get("ok")]
    print(f"通过 {len(ok)}/{len(results)}；本卡上限 {total} MiB")
    for r in results:
        s = "✅" if r.get("ok") else "❌"
        print(f"  {s} {r['tag']:28s} 峰值 {r.get('peak_mib',0):>6} MiB")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
