"""
Lightweight postflop hand-strength + board-texture evaluator.

Not a full equity calculator (PokerKit resolves actual showdowns). Its job is to
bucket hero/villain holdings the way the exploit-review rules and bots reason:
AIR / WEAK_DRAW / STRONG_DRAW / WEAK_MADE / MEDIUM_MADE / STRONG_MADE / NUTTED,
plus a board-texture label. Classification is relative to the board and gated on
hole-card involvement (so a paired board the hero doesn't share reads as air).
"""
from __future__ import annotations

from collections import Counter

from .ranges import RANK_ORDER

A_IDX = RANK_ORDER["A"]

AIR = "AIR"
WEAK_DRAW = "WEAK_DRAW"
STRONG_DRAW = "STRONG_DRAW"
WEAK_MADE = "WEAK_MADE"
MEDIUM_MADE = "MEDIUM_MADE"
STRONG_MADE = "STRONG_MADE"
NUTTED = "NUTTED"

# ordering for comparisons
BUCKET_ORDER = [AIR, WEAK_DRAW, WEAK_MADE, STRONG_DRAW, MEDIUM_MADE, STRONG_MADE, NUTTED]


def _ri(card: str) -> int:
    return RANK_ORDER[card[0]]


def _straight_high(rank_set: set[int]) -> int | None:
    s = set(rank_set)
    if A_IDX in s:
        s.add(-1)  # wheel: A plays low
    for hi in range(A_IDX, 2, -1):
        if all((hi - k) in s for k in range(5)):
            return hi
    return None


def _straight_draws(rank_set: set[int], board_ranks: set[int]) -> tuple[bool, bool]:
    """Outs-based draw detection: a rank is an out only if it completes a straight
    for the hero that beats both the hero's current straight (if any) and whatever
    that same card would put on the board for everyone. 2+ out-ranks (8 outs:
    OESD, double-gutter, wheel-side open-ender) = strong; 1 out-rank = gutshot."""
    cur = _straight_high(rank_set)
    outs = set()
    for x in range(0, A_IDX + 1):
        if x in rank_set:
            continue
        s_all = _straight_high(rank_set | {x})
        if s_all is None:
            continue
        if cur is not None and s_all <= cur:
            continue                      # doesn't improve the hero's own straight
        s_board = _straight_high(board_ranks | {x})
        if s_board is not None and s_board >= s_all:
            continue                      # that card plays the board, not the hero
        outs.add(x)
    return len(outs) >= 2, len(outs) == 1


def board_texture(board: list[str]) -> str:
    if not board:
        return "preflop"
    ranks = [_ri(c) for c in board]
    suits = [c[1] for c in board]
    suit_counts = Counter(suits)
    rank_counts = Counter(ranks)
    if any(v >= 2 for v in rank_counts.values()):
        return "PAIRED"
    if max(suit_counts.values()) == len(board):
        return "MONOTONE"
    two_tone = max(suit_counts.values()) >= 2
    uniq = sorted(set(ranks))
    connected = any(uniq[i + 1] - uniq[i] <= 2 for i in range(len(uniq) - 1))
    span = uniq[-1] - uniq[0]
    straighty = connected and span <= 4
    if two_tone and straighty:
        return "WET"
    if two_tone or straighty:
        return "SEMIWET"
    return "DRY"


def evaluate(hole: list[str], board: list[str]) -> dict:
    """Bucket a 2-card hand against a 3-5 card board."""
    if not board:
        return {"category": "preflop", "bucket": AIR, "draw": "none", "desc": "preflop"}

    hr = [_ri(c) for c in hole]
    hs = [c[1] for c in hole]
    br = [_ri(c) for c in board]
    bs = [c[1] for c in board]
    all_r = hr + br
    all_s = hs + bs
    hole_ranks = set(hr)
    n_board = len(board)

    suit_counts = Counter(all_s)
    flush_suit = next((s for s, c in suit_counts.items() if c >= 5), None)
    hero_flush = False
    if flush_suit:
        board_fs = sorted((r for r, s2 in zip(br, bs) if s2 == flush_suit), reverse=True)
        hero_fs = [r for r, s2 in zip(hr, hs) if s2 == flush_suit]
        # on a 5-flush board the hero card must actually play (beat the board's
        # lowest flush card), else the hero just has the board
        hero_flush = bool(hero_fs) and (len(board_fs) < 5 or max(hero_fs) > board_fs[4])

    rank_set = set(all_r)
    st_all = _straight_high(rank_set)
    st_board = _straight_high(set(br))
    hero_straight = st_all is not None and (st_board is None or st_all > st_board)

    # hero-involved pair structure
    count_all = Counter(all_r)
    board_counts = Counter(br)
    hero_pair_ranks = {r for r in hole_ranks if count_all[r] >= 2}
    trips_plus = {r for r in hero_pair_ranks if count_all[r] >= 3}
    pocket_pair = hr[0] == hr[1]
    board_trips = {r for r, c in board_counts.items() if c >= 3}
    # hero pair + board trips of another rank = full house (hero plays it)
    hero_boat = bool(board_trips) and any(r not in board_trips for r in hero_pair_ranks)

    board_desc = sorted(set(br), reverse=True)

    # ---- made-hand category (hero-relevant) ----
    made_bucket = None
    category = "high"
    if hero_flush or hero_straight or trips_plus or hero_boat or len(hero_pair_ranks) >= 2:
        # two pair or better with hero contribution
        made_bucket = NUTTED
        if hero_boat:
            category = "full_house"
        elif hero_flush:
            category = "flush"
        elif hero_straight:
            category = "straight"
        elif trips_plus:
            category = "set/trips"
        else:
            category = "two_pair"
    elif len(hero_pair_ranks) == 1:
        category = "pair"
        pr = next(iter(hero_pair_ranks))
        if pocket_pair:
            if not board_desc or pr > board_desc[0]:
                made_bucket = STRONG_MADE          # overpair
            elif len(board_desc) > 1 and pr > board_desc[1]:
                made_bucket = MEDIUM_MADE           # between top and 2nd board card
            else:
                made_bucket = WEAK_MADE             # underpair
        else:
            kicker = max(r for r in hr if r != pr) if any(r != pr for r in hr) else 0
            if board_desc and pr == board_desc[0]:
                made_bucket = STRONG_MADE if kicker >= RANK_ORDER["J"] else MEDIUM_MADE  # top pair
            elif len(board_desc) > 1 and pr == board_desc[1]:
                made_bucket = MEDIUM_MADE           # second pair
            else:
                made_bucket = WEAK_MADE             # third pair or lower

    # ---- draws (flop/turn only) ----
    draw = "none"
    strong_draw = weak_draw = False
    if n_board in (3, 4):
        flush_draw = any(
            c == 4 for s, c in suit_counts.items()
        ) and any(hs[i] in [s for s, c in suit_counts.items() if c == 4] for i in range(2))
        oesd, gut = _straight_draws(rank_set, set(br))
        if flush_draw and (oesd or gut):
            draw, strong_draw = "combo", True
        elif flush_draw:
            draw, strong_draw = "flush", True
        elif oesd:
            draw, strong_draw = "oesd", True
        elif gut:
            draw, weak_draw = "gutshot", True
        else:
            overcards = made_bucket is None and all(h > (board_desc[0] if board_desc else 0) for h in hr)
            if overcards:
                weak_draw = True

    # ---- final bucket ----
    if made_bucket == NUTTED:
        bucket = NUTTED
    elif made_bucket in (STRONG_MADE, MEDIUM_MADE):
        bucket = made_bucket
    elif made_bucket == WEAK_MADE:
        bucket = STRONG_DRAW if strong_draw else WEAK_MADE
    elif strong_draw:
        bucket = STRONG_DRAW
    elif weak_draw:
        bucket = WEAK_DRAW
    else:
        bucket = AIR

    return {"category": category, "bucket": bucket, "draw": draw,
            "desc": f"{category}/{bucket}" + (f"+{draw}" if draw != "none" else "")}


# --------------------------------------------------------------------- self-test
if __name__ == "__main__":
    cases = [
        (["As", "Kd"], ["Ah", "7c", "2d"], STRONG_MADE, "TPTK"),
        (["As", "5d"], ["Ah", "7c", "2d"], MEDIUM_MADE, "top pair weak kicker"),
        (["8s", "8d"], ["Ah", "7c", "2d"], MEDIUM_MADE, "88 middle"),
        (["Jd", "Jh"], ["Ac", "Kd", "5s"], WEAK_MADE, "JJ underpair to two overs"),
        (["Kh", "Qh"], ["Ah", "7h", "2c"], STRONG_DRAW, "nut flush draw"),
        (["9s", "8s"], ["7h", "6c", "2d"], STRONG_DRAW, "OESD"),
        (["5s", "5d"], ["5h", "9c", "2d"], NUTTED, "set"),
        (["Ah", "Kh"], ["Qh", "Jh", "2h"], NUTTED, "flush"),
        (["2c", "7d"], ["Ah", "Kd", "Qs"], AIR, "air"),
        (["Ac", "3c"], ["Kc", "Qc", "5d"], STRONG_DRAW, "flush draw"),
        (["As", "3d"], ["Kh", "Kd", "5s"], AIR, "board pair, hero air"),
        (["Kc", "5c"], ["Kh", "Kd", "2s"], NUTTED, "trips K"),
        (["Qd", "Jc"], ["Th", "9c", "2d"], STRONG_DRAW, "OESD QJT9"),
        (["6s", "6d"], ["7h", "8c", "9d"], STRONG_DRAW, "66+OESD on 789 (pair+draw)"),
    ]
    ok = True
    for hole, board, want, label in cases:
        got = evaluate(hole, board)
        flag = "ok " if got["bucket"] == want else "XX "
        if got["bucket"] != want:
            ok = False
        print(f"{flag} {label:32s} -> {got['bucket']:12s} ({got['desc']})  want {want}")
    print("texture:",
          board_texture(["Ah", "7c", "2d"]),
          board_texture(["9h", "8h", "7c"]),
          board_texture(["Kh", "Kd", "2s"]),
          board_texture(["Qh", "Jh", "2h"]))
    print("ALL EVAL TESTS PASSED" if ok else "SOME EVAL TESTS FAILED")
