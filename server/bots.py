"""
Population bot archetypes for live $1/$2 9-max.

Five heuristic policies calibrated to the recon stat targets:
  station 45/8, nit 12/9, TAG 22/18, maniac 55/38, rec 40/6  (VPIP/PFR).
Each is a pure function of the engine's decision context; randomness gives realism.
Preflop uses the range chart; postflop uses the hand-strength evaluator.
"""
from __future__ import annotations

import random

from .evaluator import (AIR, MEDIUM_MADE, NUTTED, STRONG_DRAW, STRONG_MADE,
                        WEAK_DRAW, WEAK_MADE, evaluate)
from .ranges import RANK_ORDER, chart, hand_code

A_IDX = RANK_ORDER["A"]
TIER = {AIR: 0, WEAK_DRAW: 1, WEAK_MADE: 2, STRONG_DRAW: 3, MEDIUM_MADE: 4, STRONG_MADE: 5, NUTTED: 6}


# ------------------------------------------------------------- hand feature helpers
def _rv(ch: str) -> int:
    return RANK_ORDER[ch]


def is_pair(code: str) -> bool:
    return len(code) == 2


def suited(code: str) -> bool:
    return code.endswith("s")


def is_premium(code: str) -> bool:
    return (is_pair(code) and _rv(code[0]) >= RANK_ORDER["Q"]) or code in ("AKs", "AKo")


def is_strong(code: str) -> bool:
    if is_pair(code) and _rv(code[0]) >= RANK_ORDER["J"]:
        return True
    return code in ("AKs", "AKo", "AQs", "AQo", "AJs", "KQs")


def has_ace(code: str) -> bool:
    return "A" in (code[0], code[1])


def both_broadway(code: str) -> bool:
    return _rv(code[0]) >= RANK_ORDER["T"] and _rv(code[1]) >= RANK_ORDER["T"]


def station_playable(code: str) -> bool:
    if is_pair(code) or suited(code):
        return True   # stations play any pair and any two suited
    hi, lo = _rv(code[0]), _rv(code[1])
    gap = hi - lo
    return (has_ace(code) or both_broadway(code)
            or (code[0] == "K" and lo >= RANK_ORDER["9"])   # K9o+
            or (gap <= 1 and lo >= RANK_ORDER["6"])          # offsuit connectors 65o+
            or (gap <= 2 and lo >= RANK_ORDER["8"]))         # 86o/97o/T8o types


def maniac_playable(code: str) -> bool:
    if is_pair(code) or suited(code):
        return True
    hi, lo = _rv(code[0]), _rv(code[1])
    return has_ace(code) or hi >= RANK_ORDER["T"] or (hi - lo) <= 2


def nit_open(code: str, pos: str) -> bool:
    late = pos in ("CO", "BTN", "SB")
    if is_pair(code):
        return _rv(code[0]) >= (RANK_ORDER["5"] if late else RANK_ORDER["7"])
    if late:
        return code in ("AKs", "AKo", "AQs", "AQo", "AJs", "ATs", "KQs", "KJs", "QJs", "AJo", "KQo")
    return code in ("AKs", "AKo", "AQs", "AQo", "AJs", "KQs")


# ------------------------------------------------------------------ sizing helpers
def _n_limpers(ctx: dict) -> int:
    bb = ctx["big_blind"]
    return max(0, sum(1 for b in ctx["bets"] if b == bb) - 1)


def _raise_to(ctx: dict, target: int) -> dict:
    if not ctx["can_raise"]:
        return {"action": "call"} if ctx["to_call"] > 0 else {"action": "check"}
    lo = ctx["min_raise_to"] or target
    hi = ctx["max_raise_to"] or target
    return {"action": "raise", "to": max(lo, min(hi, int(target)))}


def _open_size(ctx: dict, base_bb: int = 3) -> int:
    bb = ctx["big_blind"]
    return (base_bb + _n_limpers(ctx)) * bb


def _threebet_size(ctx: dict) -> int:
    return int(3 * max(ctx["bets"]))


def _bet(ctx: dict, frac: float) -> dict:
    target = int(round(frac * ctx["pot"])) + ctx["bet"]
    return _raise_to(ctx, target)


def _postflop_raise(ctx: dict, frac: float = 0.9) -> dict:
    target = max(ctx["bets"]) + int(round(frac * ctx["pot"]))
    return _raise_to(ctx, target)


def _last_raiser_pos(ctx: dict) -> str:
    pos = None
    for ev in ctx["timeline"]:
        if ev.get("type") == "action" and ev.get("action") == "raise" and ev.get("street") == "preflop":
            pos = ev["pos"]
    return pos or "MP"


# ---------------------------------------------------------------------- archetypes
def _fold_or_check(ctx: dict) -> dict:
    return {"action": "check"} if ctx["to_call"] == 0 else {"action": "fold"}


def _facing_big(ctx: dict) -> bool:
    return ctx["to_call"] > 0.66 * max(1, ctx["pot"])


def _facing_big_pre(ctx: dict) -> bool:
    """A preflop price that plays like a stack decision (big 3-bet/4-bet/jam)."""
    bb = ctx["big_blind"]
    start_stack = ctx["stack"] + ctx["bet"]
    return ctx["to_call"] > max(9 * bb, 0.35 * start_stack)


def _size_decay(ctx: dict) -> float:
    """Live players call raises less the bigger the price — scale call frequencies."""
    bb = ctx["big_blind"]
    tc = ctx["to_call"]
    if tc <= 5 * bb:
        return 1.0
    if tc <= 10 * bb:
        return 0.75
    if tc <= 20 * bb:
        return 0.45
    return 0.25


def _jam_response(arch: str, ctx: dict, code: str) -> dict:
    """Facing a huge preflop raise: everyone tightens to a jam-continue range."""
    premium = code in ("AA", "KK", "QQ", "AKs", "AKo")
    strong2 = code in ("JJ", "TT", "AQs", "AQo")
    call = {"action": "call"}
    if arch == "nit":
        return call if premium else _fold_or_check(ctx)
    if arch == "tag":
        if premium or (code == "JJ" and random.random() < 0.5):
            return call
        return _fold_or_check(ctx)
    if arch == "station":
        if premium or (strong2 and random.random() < 0.5):
            return call
        return _fold_or_check(ctx)
    if arch == "rec":
        if premium or (strong2 and random.random() < 0.6):
            return call
        return _fold_or_check(ctx)
    # maniac: widest, but still not any-two for 100bb
    if premium or strong2 or (code in ("99", "88", "AJs", "KQs") and random.random() < 0.4):
        return call
    return _fold_or_check(ctx)


# ---- PREFLOP ----
def _preflop(arch: str, ctx: dict, code: str) -> dict:
    bb = ctx["big_blind"]
    to_call = ctx["to_call"]
    # "opened" must key on whether anyone RAISED, not on the actor's price:
    # the BB facing a min-raise has to_call == bb exactly.
    opened = max(ctx["bets"]) > bb
    ch = chart()

    if opened and _facing_big_pre(ctx):
        return _jam_response(arch, ctx, code)

    if arch == "station":
        if opened:
            if is_premium(code) and ctx["can_raise"] and random.random() < 0.25:
                return _raise_to(ctx, _threebet_size(ctx))
            if is_premium(code) or (station_playable(code) and random.random() < 0.70 * _size_decay(ctx)):
                return {"action": "call"}
            return _fold_or_check(ctx)
        if is_premium(code):
            return _raise_to(ctx, _open_size(ctx))
        if station_playable(code):
            return {"action": "call"} if to_call > 0 else {"action": "check"}
        return _fold_or_check(ctx)

    if arch == "rec":
        if opened:
            if is_premium(code) and random.random() < 0.35 and ctx["can_raise"]:
                return _raise_to(ctx, _threebet_size(ctx))  # limp/limp-reraise tell
            if is_premium(code) or (station_playable(code) and random.random() < 0.66 * _size_decay(ctx)):
                return {"action": "call"}
            return _fold_or_check(ctx)
        if is_premium(code) and random.random() < 0.5:
            return _raise_to(ctx, _open_size(ctx))
        if station_playable(code) or is_premium(code):
            return {"action": "call"} if to_call > 0 else {"action": "check"}
        return _fold_or_check(ctx)

    if arch == "nit":
        if opened:
            if is_premium(code) and _rv(code[0] if is_pair(code) else "A") >= RANK_ORDER["Q"] and ctx["can_raise"] and random.random() < 0.7:
                return _raise_to(ctx, _threebet_size(ctx))
            # set-mining needs implied odds: cap the price, don't call any size
            if is_pair(code) and to_call <= min(10 * bb, ctx["stack"] // 12):
                return {"action": "call"}
            if code in ("AQs", "AKs", "AKo") and to_call <= 12 * bb:
                return {"action": "call"}
            return _fold_or_check(ctx)
        if nit_open(code, ctx["pos"]):
            return _raise_to(ctx, _open_size(ctx))
        return _fold_or_check(ctx)

    if arch == "maniac":
        if opened:
            if is_strong(code) and ctx["can_raise"] and random.random() < 0.6:
                return _raise_to(ctx, _threebet_size(ctx))
            if maniac_playable(code) and random.random() < 0.55 * _size_decay(ctx):
                if ctx["can_raise"] and random.random() < 0.3:
                    return _raise_to(ctx, _threebet_size(ctx))  # light 3bet
                return {"action": "call"}
            return _fold_or_check(ctx)
        if maniac_playable(code):
            return _raise_to(ctx, _open_size(ctx, base_bb=4))
        return _fold_or_check(ctx)

    # TAG (default)
    if opened:
        opener = _last_raiser_pos(ctx)
        act = ch.threebet_action(opener, code)
        if act == "value" and ctx["can_raise"]:
            return _raise_to(ctx, _threebet_size(ctx))
        if act == "bluff" and ctx["can_raise"] and random.random() < 0.5:
            return _raise_to(ctx, _threebet_size(ctx))
        if act == "flat" and to_call > 12 * bb:
            return _fold_or_check(ctx)   # flats are for normal opens, not big 3-bets
        if act in ("flat", "value"):
            return {"action": "call"}
        return _fold_or_check(ctx)
    if ctx["pos"] == "BB":
        # the rfi chart has no BB entry — raise the option with strong hands
        if is_strong(code):
            base = 4 if _n_limpers(ctx) > 0 else 3
            return _raise_to(ctx, _open_size(ctx, base_bb=base))
        return {"action": "check"} if to_call == 0 else {"action": "call"}
    if ch.is_rfi(ctx["pos"], code):
        base = 4 if _n_limpers(ctx) > 0 else 3   # iso bigger over limpers
        return _raise_to(ctx, _open_size(ctx, base_bb=base))
    return _fold_or_check(ctx)


# ---- POSTFLOP ----
def _postflop(arch: str, ctx: dict, ev: dict) -> dict:
    tier = TIER[ev["bucket"]]
    to_call = ctx["to_call"]
    street = ctx["street"]
    facing = to_call > 0
    big = _facing_big(ctx)
    r = random.random

    # per-archetype knobs
    knobs = {
        "station": dict(cbet=0.30, bluff=0.03, value_tier=4, call_tier=2, raise_tier=6, raise_p=0.4,
                        bet=0.4, sticky=0.85),
        "rec":     dict(cbet=0.40, bluff=0.12, value_tier=4, call_tier=2, raise_tier=6, raise_p=0.4,
                        bet=0.5, sticky=0.65),
        "nit":     dict(cbet=0.55, bluff=0.08, value_tier=5, call_tier=4, raise_tier=6, raise_p=0.8,
                        bet=0.6, sticky=0.30),
        "tag":     dict(cbet=0.62, bluff=0.30, value_tier=4, call_tier=3, raise_tier=5, raise_p=0.5,
                        bet=0.6, sticky=0.50),
        "maniac":  dict(cbet=0.80, bluff=0.60, value_tier=3, call_tier=1, raise_tier=4, raise_p=0.5,
                        bet=0.9, sticky=0.60),
    }.get(arch, None)
    if knobs is None:
        knobs = dict(cbet=0.6, bluff=0.2, value_tier=4, call_tier=3, raise_tier=5, raise_p=0.4, bet=0.6, sticky=0.5)

    # street tightening for calls: one tier tighter on the river
    street_bump = {"flop": 0, "turn": 0, "river": 1}.get(street, 0)
    call_thr = knobs["call_tier"] + (1 if big else 0) + street_bump
    # sticky archetypes resist folding on early streets
    sticky_ok = r() < knobs["sticky"] * (1.0 if street == "flop" else (0.7 if street == "turn" else 0.5))

    if facing:
        # raise strong hands sometimes
        if tier >= knobs["raise_tier"] and ctx["can_raise"] and r() < knobs["raise_p"]:
            return _postflop_raise(ctx)
        # maniac bluff-raise
        if arch == "maniac" and tier <= 1 and ctx["can_raise"] and r() < 0.22:
            return _postflop_raise(ctx)
        if tier >= call_thr:
            return {"action": "call"}
        # sticky station/rec peel one more with any piece (not vs big on late streets)
        if arch in ("station", "rec") and tier >= 1 and sticky_ok and not (big and street == "river"):
            return {"action": "call"}
        return {"action": "fold"}

    # checked to us — option to bet
    if tier >= knobs["value_tier"]:
        return _bet(ctx, knobs["bet"])
    if tier <= 1 and r() < knobs["bluff"]:
        return _bet(ctx, knobs["bet"])
    if arch == "tag" and ev["bucket"] == STRONG_DRAW and r() < 0.6:
        return _bet(ctx, knobs["bet"])   # semibluff
    return {"action": "check"}


# --------------------------------------------------------------------- entry point
def bot_policy(archetype: str, ctx: dict) -> dict:
    try:
        if ctx["street"] == "preflop" or not ctx["board"]:
            code = hand_code(ctx["hole"])
            action = _preflop(archetype, ctx, code)
        else:
            ev = evaluate(ctx["hole"], ctx["board"])
            action = _postflop(archetype, ctx, ev)
    except Exception:
        action = {"action": "check"} if ctx["to_call"] == 0 else {"action": "fold"}
    # legality guards
    if action["action"] == "raise" and not ctx["can_raise"]:
        action = {"action": "call"} if ctx["to_call"] > 0 else {"action": "check"}
    if action["action"] == "fold" and ctx["to_call"] == 0:
        action = {"action": "check"}
    return action


# --------------------------------------------------------- lightweight calibration
if __name__ == "__main__":
    from .engine import Player, Table

    def measure(arch: str, hands: int = 1500):
        players = [Player(seat=i, name=f"{arch}{i}", archetype=arch, stack=200, is_hero=(i == 0))
                   for i in range(9)]
        # everyone (incl seat0) uses the same bot policy; seat0 flagged hero but we auto-fold-check it out
        players[0].is_hero = False
        table = Table(players, bot_policy)
        vpip = pfr = 0
        for _ in range(hands):
            table.start_hand()
            # inspect preflop actions of seat 2 (UTG-ish rotating) — sample all seats instead
        # simpler: replay and count from a fresh sim tracking a fixed physical seat
        return None

    # direct VPIP/PFR sampling: play hands, look at each seat's first preflop action
    def sample(arch: str, hands: int = 2000):
        players = [Player(seat=i, name=f"p{i}", archetype=arch, stack=200, is_hero=False) for i in range(9)]
        table = Table(players, bot_policy)
        seat_vol = 0
        seat_raise = 0
        opportunities = 0
        for _ in range(hands):
            v = table.start_hand()
            # count from timeline: each seat's preflop involvement
            acted = {}
            for evn in table.timeline:
                if evn.get("type") != "action" or evn["street"] != "preflop":
                    continue
                s = evn["seat"]
                if s in acted:
                    continue
                acted[s] = evn["action"]
            # opportunities = seats that got to act preflop (approx all non-blind auto)
            for s, a in acted.items():
                opportunities += 1
                if a in ("call", "raise"):
                    seat_vol += 1
                if a == "raise":
                    seat_raise += 1
        vpip = 100 * seat_vol / max(1, opportunities)
        pfr = 100 * seat_raise / max(1, opportunities)
        return vpip, pfr

    targets = {"station": (45, 8), "nit": (12, 9), "tag": (22, 18), "maniac": (55, 38), "rec": (40, 6)}
    print(f"{'arch':8s} {'VPIP':>12s} {'PFR':>12s}")
    for arch, (tv, tp) in targets.items():
        vpip, pfr = sample(arch)
        print(f"{arch:8s}  {vpip:5.1f} (t{tv:>2}) {pfr:6.1f} (t{tp:>2})")
