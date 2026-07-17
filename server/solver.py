"""
TexasSolver console wrapper — the Phase-2 GTO anchor.

Drives solver/texassolver/console_solver.exe (official v0.2.0 release binary):
builds a command file for a heads-up postflop spot, runs the solve, and parses
the dumped strategy JSON into per-hand action frequencies the review engine can
compare against the exploit baseline ("population says X, GTO says Y").

The solver is NOT part of the request path — solves take seconds to minutes.
It exists for offline pre-solving (tools/presolve.py) and one-off spot checks.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from .ranges import parse_range_string

BASE = os.path.dirname(os.path.dirname(__file__))
SOLVER_DIR = os.path.join(BASE, "solver", "texassolver")
EXE = os.path.join(SOLVER_DIR, "console_solver.exe")
OUT_DIR = os.path.join(BASE, "solver", "outputs")

# one bet + one raise size per street keeps flop solves in the minutes range
DEFAULT_SIZES = {
    ("oop", "flop", "bet"): "33,75",
    ("oop", "flop", "raise"): "60",
    ("ip", "flop", "bet"): "33,75",
    ("ip", "flop", "raise"): "60",
    ("oop", "turn", "bet"): "66",
    ("oop", "turn", "raise"): "60",
    ("ip", "turn", "bet"): "66",
    ("ip", "turn", "raise"): "60",
    ("oop", "river", "bet"): "75",
    ("oop", "river", "donk"): "50",
    ("oop", "river", "raise"): "60",
    ("ip", "river", "bet"): "75",
    ("ip", "river", "raise"): "60",
}


@dataclass
class SpotSpec:
    """A heads-up postflop spot in solver terms. Amounts in $ (1/2 chips)."""
    pot: int
    effective_stack: int
    board: list[str]                    # ["Qs", "8d", "7h"]
    range_ip: str                       # "AA,KK,AQs,..." (weights allowed: "A5s:0.5")
    range_oop: str
    sizes: dict = field(default_factory=lambda: dict(DEFAULT_SIZES))
    allin_threshold: float = 0.67
    accuracy: float = 0.8               # target exploitability, % of pot
    max_iteration: int = 150
    threads: int = max(1, (os.cpu_count() or 4) - 2)
    dump_rounds: int = 2                # how deep the dumped strategy tree goes
    use_isomorphism: bool = True


def range_from_chart(codes: set[str]) -> str:
    """Convert a set of 169 canonical codes (from ranges.py) to solver notation."""
    return ",".join(sorted(codes))


def build_input(spec: SpotSpec, dump_name: str) -> str:
    lines = [
        f"set_pot {spec.pot}",
        f"set_effective_stack {spec.effective_stack}",
        f"set_board {','.join(spec.board)}",
        f"set_range_ip {spec.range_ip}",
        f"set_range_oop {spec.range_oop}",
    ]
    for (who, street, kind), sizes in spec.sizes.items():
        lines.append(f"set_bet_sizes {who},{street},{kind},{sizes}")
    lines += [
        f"set_allin_threshold {spec.allin_threshold}",
        "build_tree",
        f"set_thread_num {spec.threads}",
        f"set_accuracy {spec.accuracy}",
        f"set_max_iteration {spec.max_iteration}",
        "set_print_interval 10",
        f"set_use_isomorphism {1 if spec.use_isomorphism else 0}",
        "start_solve",
        f"set_dump_rounds {spec.dump_rounds}",
        f"dump_result {dump_name}",
    ]
    return "\n".join(lines) + "\n"


def solve(spec: SpotSpec, tag: str = "spot", timeout: int = 3600) -> dict:
    """Run a solve; returns {"tree": <dumped json>, "seconds": float, "dump_path": str}."""
    if not os.path.exists(EXE):
        raise FileNotFoundError(
            f"console_solver.exe not found at {EXE} — run solver/fetch_solver.ps1 first")
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dump_name = f"{tag}_{stamp}.json"
    input_path = os.path.join(OUT_DIR, f"{tag}_{stamp}_input.txt")
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(build_input(spec, dump_name))

    t0 = time.time()
    # cwd must be SOLVER_DIR so the exe finds resources/compairer/;
    # the dump lands there too and is moved into solver/outputs after
    proc = subprocess.run(
        [EXE, "--input_file", input_path],
        cwd=SOLVER_DIR, capture_output=True, text=True, timeout=timeout,
    )
    seconds = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"solver failed (rc={proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")

    src = os.path.join(SOLVER_DIR, dump_name)
    dump_path = os.path.join(OUT_DIR, dump_name)
    if os.path.exists(src):
        os.replace(src, dump_path)
    if not os.path.exists(dump_path):
        raise RuntimeError(f"solver produced no dump:\n{proc.stdout[-2000:]}")
    with open(dump_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    return {"tree": tree, "seconds": seconds, "dump_path": dump_path,
            "log_tail": proc.stdout[-600:]}


# ---------------------------------------------------------------- strategy access
def root_strategy(tree: dict) -> Optional[dict]:
    """The acting player's strategy at the root of the dumped tree:
    {"actions": ["CHECK", "BET 4.29", ...], "combos": {"QsQh": [p0, p1, ...]}}"""
    node = tree
    strat = node.get("strategy")
    if not strat:
        return None
    return {"actions": strat.get("actions", []), "combos": strat.get("strategy", {})}


def combo_to_code(combo: str) -> str:
    """'QsQh' -> 'QQ'; 'AhKh' -> 'AKs'; 'AsKd' -> 'AKo'."""
    from .ranges import canonical
    r1, s1, r2, s2 = combo[0], combo[1], combo[2], combo[3]
    return canonical(r1, r2, s1 == s2)


def strategy_by_code(tree: dict) -> dict:
    """Aggregate the root per-combo strategy into 169-code averages:
    {"AKs": {"CHECK": 0.4, "BET 4.29": 0.6}, ...}"""
    root = root_strategy(tree)
    if not root:
        return {}
    agg: dict[str, list] = {}
    for combo, probs in root["combos"].items():
        code = combo_to_code(combo)
        agg.setdefault(code, []).append(probs)
    out = {}
    for code, rows in agg.items():
        n = len(rows)
        avg = [sum(r[i] for r in rows) / n for i in range(len(rows[0]))]
        out[code] = dict(zip(root["actions"], (round(p, 4) for p in avg)))
    return out


# --------------------------------------------------------------------- self-test
if __name__ == "__main__":
    # tiny toy spot (the release's own sample): proves exe + parse round-trip
    spec = SpotSpec(
        pot=4, effective_stack=10, board=["Qs", "8d", "7h"],
        range_ip="T9s", range_oop="JTs,43s",
        accuracy=0.5, max_iteration=80, dump_rounds=2,
    )
    res = solve(spec, tag="selftest", timeout=300)
    by_code = strategy_by_code(res["tree"])
    print(f"solved in {res['seconds']:.1f}s -> {res['dump_path']}")
    assert by_code, "no root strategy parsed"
    for code, acts in sorted(by_code.items()):
        pretty = "  ".join(f"{a}:{p:.0%}" for a, p in acts.items())
        print(f"  {code:5s} {pretty}")
    total = sum(sum(a.values()) for a in by_code.values())
    assert abs(total - len(by_code)) < 0.05, "strategies don't sum to 1"
    print("SOLVER WRAPPER SELF-TEST PASSED")
