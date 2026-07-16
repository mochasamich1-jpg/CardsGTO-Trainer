"""
Poker range notation parser + preflop answer-key lookup.

Parses standard notation ("22+", "ATs+", "A5s-A2s", "KQo") into sets of the 169
canonical hands ("AA", "AKs", "AKo", ...), and loads data/preflop_ranges.json for
position lookups used by the bots and the review engine.
"""
from __future__ import annotations

import json
import os

RANKS = "23456789TJQKA"
RANK_ORDER = {r: i for i, r in enumerate(RANKS)}      # 2=0 .. A=12
INV_RANK = {i: r for r, i in RANK_ORDER.items()}
A_IDX = RANK_ORDER["A"]

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "preflop_ranges.json")


# --------------------------------------------------------------------- canonical
def canonical(a: str, b: str, suited: bool) -> str:
    """Canonical 169 code from two rank chars + suitedness (higher rank first)."""
    if RANK_ORDER[a] < RANK_ORDER[b]:
        a, b = b, a
    if a == b:
        return a + b
    return a + b + ("s" if suited else "o")


def hand_code(hole: list[str]) -> str:
    """['As','Kd'] -> 'AKo'; ['Ts','9s'] -> 'T9s'; ['Ad','Ah'] -> 'AA'."""
    a, b = hole[0], hole[1]
    ra, sa = a[0], a[1]
    rb, sb = b[0], b[1]
    return canonical(ra, rb, sa == sb)


# ------------------------------------------------------------------------ parser
def _expand_pair(rank: str, plus: bool) -> set[str]:
    i = RANK_ORDER[rank]
    if plus:
        return {INV_RANK[j] + INV_RANK[j] for j in range(i, A_IDX + 1)}
    return {rank + rank}


def _expand_nonpair(a: str, b: str, suit: str, plus: bool) -> set[str]:
    ia, ib = RANK_ORDER[a], RANK_ORDER[b]
    if ia < ib:
        a, b, ia, ib = b, a, ib, ia
    if plus:
        # fix the top card, raise the kicker up to one below the top card
        return {a + INV_RANK[j] + suit for j in range(ib, ia)}
    return {a + b + suit}


def _parse_hyphen_range(left: str, right: str) -> set[str]:
    left = left.rstrip("+").strip()
    right = right.rstrip("+").strip()
    # pair range e.g. 22-99
    if len(left) == 2 and left[0] == left[1] and len(right) == 2 and right[0] == right[1]:
        lo, hi = sorted([RANK_ORDER[left[0]], RANK_ORDER[right[0]]])
        return {INV_RANK[j] + INV_RANK[j] for j in range(lo, hi + 1)}
    # suited/offsuit range with a shared top card e.g. A5s-A2s
    if len(left) >= 3 and len(right) >= 3 and left[0] == right[0] and left[2] == right[2]:
        top, suit = left[0], left[2]
        jlo, jhi = sorted([RANK_ORDER[left[1]], RANK_ORDER[right[1]]])
        return {canonical(top, INV_RANK[j], suit == "s") for j in range(jlo, jhi + 1)}
    return set()


def parse_token(tok: str) -> set[str]:
    tok = tok.strip()
    if not tok:
        return set()
    try:
        if "-" in tok and not tok.endswith("-"):
            left, right = tok.split("-", 1)
            return _parse_hyphen_range(left, right)
        plus = tok.endswith("+")
        core = tok[:-1].strip() if plus else tok
        if len(core) == 2 and core[0] == core[1]:
            return _expand_pair(core[0], plus)
        if len(core) >= 3 and core[2] in ("s", "o"):
            return _expand_nonpair(core[0], core[1], core[2], plus)
    except KeyError:
        pass   # malformed rank char: ignore the token instead of crashing chart()
    return set()


def parse_range_string(s: str) -> set[str]:
    out: set[str] = set()
    if not s:
        return out
    for tok in s.split(","):
        out |= parse_token(tok)
    return out


# --------------------------------------------------------------- answer-key lookup
class PreflopChart:
    def __init__(self, path: str = _DATA_PATH):
        with open(path, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.rfi = {pos: parse_range_string(s) for pos, s in self.data["rfi"].items()}
        self.threebet_value = {
            k: parse_range_string(v["value"]) for k, v in self.data["threebet"].items()
        }
        self.threebet_bluff = {
            k: parse_range_string(v["bluff"]) for k, v in self.data["threebet"].items()
        }
        self.flat_vs_open = {
            k: parse_range_string(v)
            for k, v in self.data["flat_vs_open"].items()
            if k != "comment"
        }
        self.bucket = self.data["position_bucket"]

    def is_rfi(self, pos: str, code: str) -> bool:
        return code in self.rfi.get(pos, set())

    def bucket_for(self, opener_pos: str) -> str:
        return self.bucket.get(opener_pos, "vs_late")

    def threebet_action(self, opener_pos: str, code: str) -> str:
        """Return 'value', 'bluff', 'flat', or 'fold' for a hand facing an open."""
        b = self.bucket_for(opener_pos)
        if code in self.threebet_value.get(b, set()):
            return "value"
        if code in self.threebet_bluff.get(b, set()):
            return "bluff"
        if code in self.flat_vs_open.get(b, set()):
            return "flat"
        return "fold"


_CHART: PreflopChart | None = None


def chart() -> PreflopChart:
    global _CHART
    if _CHART is None:
        _CHART = PreflopChart()
    return _CHART


# --------------------------------------------------------------------- self-test
if __name__ == "__main__":
    def check(got, want, label):
        assert got == want, f"{label}: got {sorted(got)} want {sorted(want)}"
        print(f"ok  {label} ({len(got)})")

    check(parse_range_string("22+"),
          {r + r for r in RANKS}, "22+")
    check(parse_token("ATs+"), {"ATs", "AJs", "AQs", "AKs"}, "ATs+")
    check(parse_token("A5s-A2s"), {"A5s", "A4s", "A3s", "A2s"}, "A5s-A2s")
    check(parse_token("A2s+"),
          {"A2s", "A3s", "A4s", "A5s", "A6s", "A7s", "A8s", "A9s", "ATs", "AJs", "AQs", "AKs"},
          "A2s+")
    check(parse_token("AJo+"), {"AJo", "AQo", "AKo"}, "AJo+")
    check(parse_token("K8o+"), {"K8o", "K9o", "KTo", "KJo", "KQo"}, "K8o+")
    check(parse_token("22-99"), {"22", "33", "44", "55", "66", "77", "88", "99"}, "22-99")
    check(parse_token("J7s+"), {"J7s", "J8s", "J9s", "JTs"}, "J7s+")
    check(parse_token("A5o"), {"A5o"}, "A5o single")

    assert hand_code(["As", "Kd"]) == "AKo"
    assert hand_code(["Ts", "9s"]) == "T9s"
    assert hand_code(["Ad", "Ah"]) == "AA"
    assert hand_code(["2c", "7d"]) == "72o"
    assert hand_code(["Ks", "As"]) == "AKs"
    print("ok  hand_code")

    c = chart()
    assert c.is_rfi("UTG", "AA")
    assert c.is_rfi("UTG", "AQo")
    assert not c.is_rfi("UTG", "72o")
    assert not c.is_rfi("UTG", "KJo")   # UTG is tight: KJo folds
    assert c.is_rfi("BTN", "KJo")
    assert c.threebet_action("UTG", "KK") == "value"
    assert c.threebet_action("BTN", "TT") == "value"
    assert c.threebet_action("UTG", "72o") == "fold"
    print("ok  chart lookups")
    print("ALL RANGE TESTS PASSED")
