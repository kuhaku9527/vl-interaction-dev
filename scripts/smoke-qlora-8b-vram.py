"""sm_120 QLoRA 显存上限测试：真实 8B 级配置能否塞进 16GB。

前置：smoke-qlora-sm120.py 已证明机制可行（4bit backward / LoRA / AdamW8bit 全通）。
本脚本测「容量」这一关 —— expert 估计优化后 6–8GB，需实测验证。

⚠️ 关键工程细节（冒烟测试中实测发现）：
   gradient checkpointing 下必须调用 enable_input_require_grads()，
   否则 embedding 冻结会导致「element 0 of tensors does not require grad」。

用法: /d/AI/envs/minimind-o/python.exe scripts/smoke-qlora-8b-vram.py
"""
from __future__ import annotations

import gc
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import torch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "doc" / "research" / "data" / "sm120_qlora_8b_vram.json"


def vram_mib() -> tuple[int, int]:
    free, total = torch.cuda.mem_get_info()
    return (total - free) // (1024 * 1024), total // (1024 * 1024)


def peak_mib() -> int:
    return torch.cuda.max_memory_allocated() // (1024 * 1024)


def _quantize_linear_layers_4bit(model):
    """Replace every nn.Linear with a bitsandbytes Linear4bit (NF4).

    ``from_pretrained(load_in_4bit=True)`` is the usual route, but this script
    builds a randomly-initialised model from config (no checkpoint download),
    so we swap the layers explicitly. Weights are re-quantized from the
    already-bf16 tensors, which reproduces the same VRAM footprint as a real
    NF4 load.
    """
    import torch.nn as nn
    from bitsandbytes.nn import Linear4bit

    replaced = 0
    for name, module in list(model.named_modules()):
        if not isinstance(module, nn.Linear) or isinstance(module, Linear4bit):
            continue
        new = Linear4bit(
            module.in_features,
            module.out_features,
            bias=module.bias is not None,
            quant_type="nf4",
            compute_dtype=torch.bfloat16,
        ).to(module.weight.device)
        with torch.no_grad():
            new.weight = type(new.weight)(
                module.weight.data.to(torch.bfloat16), quant_type="nf4"
            )
            if module.bias is not None:
                new.bias = nn.Parameter(module.bias.data.clone())
        parent = model.get_submodule(name.rsplit(".", 1)[0]) if "." in name else model
        setattr(parent, name.rsplit(".", 1)[-1], new)
        replaced += 1
    print(f"    (quantized {replaced} Linear layers -> NF4)", flush=True)
    return model


def build_and_train(
    *,
    layers: int,
    hidden: int,
    heads: int,
    kv_heads: int,
    vocab: int,
    seq: int,
    batch: int,
    lora_r: int,
    grad_ckpt: bool,
    quantize: bool,
    use_8bit_opt: bool,
    label: str,
) -> dict:
    """Build a Qwen3-shaped model and run one fwd+bwd+step; report peak VRAM."""
    from peft import LoraConfig, get_peft_model
    from transformers import AutoConfig, AutoModelForCausalLM

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    gc.collect()

    base_used, total = vram_mib()
    rec: dict = {"label": label, "cfg": {
        "layers": layers, "hidden": hidden, "vocab": vocab, "seq": seq,
        "batch": batch, "lora_r": lora_r, "grad_ckpt": grad_ckpt,
        "quantize": quantize, "8bit_opt": use_8bit_opt,
    }, "vram_before": base_used, "vram_total": total}

    try:
        cfg = AutoConfig.for_model(
            "qwen3", hidden_size=hidden,
            intermediate_size=int(hidden * 2.75),
            num_hidden_layers=layers, num_attention_heads=heads,
            num_key_value_heads=kv_heads, vocab_size=vocab,
            max_position_embeddings=4096,
        )
        model = AutoModelForCausalLM.from_config(cfg).to(torch.bfloat16)

        if quantize:
            model = _quantize_linear_layers_4bit(model)

        model = model.cuda()

        lora = LoraConfig(
            r=lora_r, lora_alpha=lora_r * 2, lora_dropout=0.05, bias="none",
            target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora)
        model = model.cuda()
        if grad_ckpt:
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()  # ← 必需，否则梯度断裂
        model.train()

        after_model, _ = vram_mib()
        rec["vram_after_model"] = after_model

        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        rec["trainable_params"] = trainable

        params = [p for p in model.parameters() if p.requires_grad]
        if use_8bit_opt:
            import bitsandbytes as bnb
            opt = bnb.optim.AdamW8bit(params, lr=1e-4)
        else:
            opt = torch.optim.AdamW(params, lr=1e-4)

        ids = torch.randint(0, vocab, (batch, seq), device="cuda")
        out = model(input_ids=ids, labels=ids)
        loss = out.loss
        rec["loss"] = round(float(loss), 4)
        loss.backward()
        opt.step()
        torch.cuda.synchronize()

        rec["peak_mib"] = peak_mib()
        rec["ok"] = True
        del model, opt
        torch.cuda.empty_cache()
        gc.collect()
    except RuntimeError as e:
        rec["ok"] = False
        rec["error"] = str(e)[:300]
        rec["peak_mib"] = peak_mib()
        torch.cuda.empty_cache()
        gc.collect()

    return rec


def main() -> None:
    print("=" * 70)
    print("sm_120 QLoRA 8B 级显存上限测试")
    print("=" * 70)
    print(f"torch {torch.__version__}  GPU {torch.cuda.get_device_name(0)}")
    used, total = vram_mib()
    print(f"VRAM 起始 {used} / {total} MiB\n")

    # 真实 Qwen3-8B 几何：36 层 / hidden 4096 / 32 heads / 8 kv heads / vocab 151936
    # 逐档加压，观察峰值显存
    plan = [
        dict(layers=36, hidden=4096, heads=32, kv_heads=8, vocab=151936,
             seq=512, batch=1, lora_r=8, grad_ckpt=True, quantize=True,
             use_8bit_opt=True, label="8B真实几何 seq512 r8 4bit gckpt"),
        dict(layers=36, hidden=4096, heads=32, kv_heads=8, vocab=151936,
             seq=1024, batch=1, lora_r=8, grad_ckpt=True, quantize=True,
             use_8bit_opt=True, label="8B真实几何 seq1024 r8 4bit gckpt"),
        dict(layers=36, hidden=4096, heads=32, kv_heads=8, vocab=151936,
             seq=2048, batch=1, lora_r=16, grad_ckpt=True, quantize=True,
             use_8bit_opt=True, label="8B真实几何 seq2048 r16 4bit gckpt"),
    ]

    results = []
    for p in plan:
        label = p.pop("label")
        print(f"[{label}] 构建中…", flush=True)
        r = build_and_train(label=label, **p)
        results.append(r)
        if r.get("ok"):
            print(f"    ✅ OK  loss={r['loss']}  峰值 VRAM = {r['peak_mib']} MiB "
                  f"(模型后 {r['vram_after_model']}, 可训 {r['trainable_params']:,})", flush=True)
        else:
            print(f"    ❌ FAIL  峰值 {r.get('peak_mib')} MiB — {r.get('error','')[:140]}", flush=True)
        print(flush=True)

    payload = {"gpu": torch.cuda.get_device_name(0),
               "torch": torch.__version__, "results": results}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    ok = [r for r in results if r.get("ok")]
    print(f"通过 {len(ok)}/{len(results)} 档；16GB 卡上限 = 16311 MiB")
    for r in results:
        status = "✅" if r.get("ok") else "❌"
        print(f"  {status} {r['label'][:34]:36s} 峰值 {r.get('peak_mib',0):>6} MiB")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
