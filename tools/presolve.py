"""
Offline pre-solve harness: builds the GTO-anchor solution DB.

Enumerates (preflop-config x flop) spots, solves each with TexasSolver via
server.solver, and stores gzipped strategy dumps + an index under
data/solver_db/. Resumable: existing outputs are skipped.

Usage (from the project root, venv python):
  python -m tools.presolve --dry-run          # show the plan + ETA
  python -m tools.presolve --limit 2          # solve the first 2 missing spots
  python -m tools.presolve                    # run everything missing
  python -m tools.presolve --configs btn_bb --accuracy 0.8
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.ranges import chart
from server.solver import SpotSpec, range_from_chart, solve  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE, "data", "solver_db")

# BB defense vs a single raise (not in the RFI chart — the BB defends far wider
# than any open range; tuned loose-passive for the live pool, ~40%)
BB_DEFEND = ("22,33,44,55,66,77,88,99,"
             "A2s,A3s,A4s,A5s,A6s,A7s,A8s,A9s,ATs,AJs,"
             "K5s,K6s,K7s,K8s,K9s,KTs,KJs,"
             "Q8s,Q9s,QTs,QJs,J8s,J9s,JTs,T7s,T8s,T9s,"
             "96s,97s,98s,86s,87s,75s,76s,64s,65s,54s,43s,"
             "A2o,A3o,A4o,A5o,A6o,A7o,A8o,A9o,ATo,AJo,"
             "K9o,KTo,KJo,Q9o,QTo,QJo,J9o,JTo,T8o,T9o,98o,87o,76o")

# Single-raised pots at $1/$2: open $6, one caller. Pot = 6+6+blind dead money.
# (pot, effective) tuned per config; IP/OOP from the raiser's chart range.
def _cfgs() -> dict:
    ch = chart()
    rfi = {pos: range_from_chart(ch.rfi[pos]) for pos in ch.rfi}
    flat_late = range_from_chart(ch.flat_vs_open["vs_late"])
    flat_mid = range_from_chart(ch.flat_vs_open["vs_middle"])
    return {
        # config key: (range_ip, range_oop, pot, effective_stack, note)
        "btn_bb": (rfi["BTN"], BB_DEFEND, 13, 194, "BTN opens, BB defends"),
        "co_bb":  (rfi["CO"], BB_DEFEND, 13, 194, "CO opens, BB defends"),
        "utg_bb": (rfi["UTG"], BB_DEFEND, 13, 194, "UTG opens, BB defends"),
        "mp_bb":  (rfi["MP"], BB_DEFEND, 13, 194, "MP opens, BB defends"),
        # raiser OOP vs an in-position cold-caller (caller uses the flat charts)
        "utg_vs_ip": (flat_mid, rfi["UTG"], 15, 194, "UTG opens, MP/CO flats IP"),
        "co_vs_btn": (flat_late, rfi["CO"], 15, 194, "CO opens, BTN flats IP"),
    }


# 25 strategically-distinct starter flops covering the main texture classes
# (dry A/K/Q-high, paired, monotone, two-tone connected, low, broadway).
FLOPS = [
    "As,7d,2c", "Ah,Kd,5s", "Ac,Qs,Jd", "Ad,8d,3c", "Ks,7h,2d",
    "Kh,Qd,4s", "Kc,Ts,5h", "Kd,9d,2d", "Qs,8d,7h", "Qh,Jd,2c",
    "Qc,Ts,9s", "Jh,7c,2s", "Js,Ts,8d", "Th,9h,4c", "Ts,6d,2h",
    "9c,8s,4d", "9h,7d,3s", "8s,8d,3h", "7c,6h,2d", "6s,5s,4h",
    "5d,5c,Kh", "As,Ad,7c", "Qd,Qc,6s", "4h,4d,9s", "3c,3s,2d",
]


def spot_path(cfg_key: str, flop: str) -> str:
    return os.path.join(DB_DIR, cfg_key, flop.replace(",", "") + ".json.gz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="*", default=None, help="config keys to run (default all)")
    ap.add_argument("--limit", type=int, default=None, help="max spots to solve this run")
    ap.add_argument("--accuracy", type=float, default=0.5, help="target exploitability %% of pot")
    ap.add_argument("--max-iteration", type=int, default=200)
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--per-spot-min", type=float, default=4.5, help="ETA assumption, minutes/spot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfgs = _cfgs()
    keys = args.configs or list(cfgs)
    for k in keys:
        if k not in cfgs:
            sys.exit(f"unknown config {k!r}; valid: {', '.join(cfgs)}")

    todo = [(k, f) for k in keys for f in FLOPS if not os.path.exists(spot_path(k, f))]
    done = len(keys) * len(FLOPS) - len(todo)
    print(f"plan: {len(keys)} configs x {len(FLOPS)} flops = {len(keys) * len(FLOPS)} spots "
          f"({done} already solved, {len(todo)} to go)")
    print(f"ETA at ~{args.per_spot_min:.1f} min/spot: {len(todo) * args.per_spot_min / 60:.1f} hours")
    if args.dry_run:
        for k, f in todo[:10]:
            print(f"  next: {k}  {f}")
        return
    if args.limit:
        todo = todo[: args.limit]

    index_path = os.path.join(DB_DIR, "index.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as fh:
            index = json.load(fh)

    for i, (k, f) in enumerate(todo, 1):
        ip, oop, pot, eff, note = cfgs[k]
        print(f"[{i}/{len(todo)}] {k} {f} ({note}) ...", flush=True)
        spec = SpotSpec(pot=pot, effective_stack=eff, board=f.split(","),
                        range_ip=ip, range_oop=oop,
                        accuracy=args.accuracy, max_iteration=args.max_iteration,
                        threads=args.threads, dump_rounds=2)
        res = solve(spec, tag=f"db_{k}", timeout=3600)
        out = spot_path(k, f)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(res["tree"], fh)
        os.remove(res["dump_path"])  # keep only the gzipped copy
        index[f"{k}/{f}"] = {"seconds": round(res["seconds"], 1), "pot": pot,
                             "effective": eff, "accuracy": args.accuracy,
                             "solved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=1, sort_keys=True)
        print(f"    done in {res['seconds']:.0f}s -> {os.path.relpath(out, BASE)}", flush=True)

    print("presolve run complete.")


if __name__ == "__main__":
    main()
