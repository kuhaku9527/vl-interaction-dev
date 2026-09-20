"""Self-check: does the dump actually support the "hypervisor bug" conclusion?

The prior verdict claimed the call stack proves "the hypervisor itself hit a
fatal error". This script re-examines that claim against the raw log, because
there is a real alternative reading:

  HvlSkCrashdumpCallbackRoutine is the routine that HANDLES an NMI and decides
  to bugcheck. It is part of the crash-REPORTING path, not necessarily the
  crash CAUSE. A stack that ends in it proves "an NMI arrived and Windows
  decided to bugcheck", not "the hypervisor's internals mis-compared state".

What the dump actually pins down:
  * the bugcheck code/args
  * the stack AT THE MOMENT of the NMI (i.e. what was interrupted)
  * which modules were loaded

What it does NOT contain:
  * hypervisor-internal state (that lives in hvix64's own memory)
  * any faulting instruction inside the hypervisor

So the honest claim is narrower than the previous report made it.

Usage: python scripts/selfcheck-bsod-claim.py
"""
from __future__ import annotations

import re
from pathlib import Path

LOG = Path("D:/AI/workspace/JoyAI-VL-Interaction-main/logs/blue-log.txt")


def main() -> None:
    txt = LOG.read_text(encoding="utf-8", errors="replace")

    print("=" * 72)
    print("SELF-CHECK of the BSOD verdict")
    print("=" * 72)

    # --- 1. Stack frames in order -----------------------------------------
    print("\n[1] Stack, in call order (bottom = what was interrupted) ---")
    frames = re.findall(r": (nt![A-Za-z0-9_]+(?:\+0x[0-9a-f]+)?)\s*$",
                        txt, re.M)
    seen = []
    for f in frames:
        if f not in seen:
            seen.append(f)
    for i, f in enumerate(seen):
        print(f"    {i:>2}. {f}")

    # --- 2. Which claim does this support? --------------------------------
    print("\n[2] What the stack does and does NOT prove ---")
    has_idle = any("Idle" in f for f in seen)
    has_nmi = any("Nmi" in f for f in seen)
    has_cbdump = any("Crashdump" in f for f in seen)
    print(f"    interrupted while idle       : {has_idle}   (PpmIdle/PoIdle)")
    print(f"    an NMI interrupt arrived     : {has_nmi}")
    print(f"    bugcheck raised from the NMI handler : {has_cbdump}")

    print("\n    => PROVEN: an NMI arrived while the CPU was idling, and")
    print("       Windows' crashdump callback raised HYPERVISOR_ERROR.")
    print("    => NOT PROVEN: why the hypervisor raised that NMI.")
    print("       The hypervisor's own state is NOT in a kernel triage dump.")

    # --- 3. Modules: loaded vs actually on the stack ----------------------
    print("\n[3] Third-party modules LOADED vs. frames actually ON THE STACK ---")
    interesting = ["vmx86", "hcmon", "vmnetbridge", "VMNET", "vmnetuserif",
                   "sysdiag", "hrdevmon", "hrndis6", "hrwfpdrv", "ndisrd",
                   "nvlddmkm", "RTKVHD64"]
    for name in interesting:
        loaded = re.search(rf"^\s*[0-9a-f`]+\s[0-9a-f`]+\s+{re.escape(name)}\s",
                           txt, re.M) is not None
        on_stack = any(name.lower() in f.lower() for f in seen)
        mark = "loaded" if loaded else "ABSENT"
        stk = "ON STACK" if on_stack else "not on stack"
        print(f"    {name:<14} {mark:<8} {stk}")

    print("\n    => A module being LOADED is not evidence it caused the crash.")
    print("       Listing vmx86/hcmon as evidence was an overreach.")

    # --- 4. Arg1 comparison ----------------------------------------------
    print("\n[4] Arg1 value ---")
    m = re.search(r"BUGCHECK_P1:\s*([0-9a-f]+)", txt)
    print(f"    this machine Arg1 = 0x{m.group(1) if m else '?'}")
    print("    public cases report Arg1 = 0x32 (varies by hypervisor path),")
    print("    which means Arg1 is a hypervisor-internal code with no public")
    print("    dictionary. It cannot be used to name a culprit.")

    # --- 5. Emptiness of the real question -------------------------------
    print("\n[5] What WOULD have named the culprit ---")
    print("    * a FULL memory dump (MEMORY.DMP) + hypervisor symbols")
    print("    * Event Viewer 'Hyper-V-Hypervisor' operational log around 17:11")
    print("    * reproducing with one hypervisor removed (the controlled test)")

    print("\n" + "=" * 72)
    print("VERDICT: the stack is a REPORTING path, not a culprit.")
    print("The evidence excludes drivers; it does NOT identify a specific")
    print("third-party module. The controlled removal test is required.")
    print("=" * 72)


if __name__ == "__main__":
    main()
