"""
Post-hand exploit-review engine.

Replays a finished hand, reconstructs each of hero's decisions with the context
that mattered (hand-strength bucket, size faced, which villain archetype was in
the pot, how many players, street, pot), then fires population-exploit rules. The
"correct" baseline is the 1/2-live EXPLOIT, not GTO.

Each finding: {rule, kind: 'leak'|'good'|'info', street, text, confidence, ev}.
Findings are ranked confidence*pot and the top few surface as coaching notes.
"""
from __future__ import annotations

from .evaluator import (AIR, MEDIUM_MADE, NUTTED, STRONG_DRAW, STRONG_MADE,
                        WEAK_DRAW, WEAK_MADE, evaluate)
from .ranges import RANK_ORDER, chart, hand_code

TIER = {AIR: 0, WEAK_DRAW: 1, WEAK_MADE: 2, STRONG_DRAW: 3, MEDIUM_MADE: 4, STRONG_MADE: 5, NUTTED: 6}
SB, BB = 1, 2


def _size_bucket(frac: float) -> str:
    if frac <= 0.33:
        return "SMALL"
    if frac <= 0.66:
        return "MED"
    if frac <= 1.0:
        return "LARGE"
    return "OVERBET"


def replay(view: dict, hero_hole: list[str]) -> list[dict]:
    """Reconstruct hero decision records from the timeline."""
    seats = {s["seat"]: s for s in view["seats"]}
    hero_seat = view["hero_seat"]
    timeline = view["timeline"]
    n_seats = len(view["seats"])

    board: list[str] = []
    street = "preflop"
    street_bets: dict[int, int] = {}
    last_aggressor = None
    preflop_raise_seen = False
    limpers = 0
    folded: set[int] = set()
    records: list[dict] = []

    for ev in timeline:
        t = ev.get("type")
        if t == "blinds":
            street_bets = {ev["sb_seat"]: SB, ev["bb_seat"]: BB}
            last_aggressor = ev["bb_seat"]
        elif t == "board":
            board = board + ev["cards"]
            street = ev["street"]
            street_bets = {}
            last_aggressor = None
        elif t == "action":
            seat = ev["seat"]
            # the engine records the exact price it offered (capped at stack);
            # fall back to reconstruction for timelines that predate that field
            to_call = ev.get("to_call")
            if to_call is None:
                cur_max = max(street_bets.values()) if street_bets else 0
                to_call = max(0, cur_max - street_bets.get(seat, 0))
            pot_before = ev["pot"] - ev["committed"]

            if seat == hero_seat:
                bucket = evaluate(hero_hole, board)["bucket"] if board else None
                # size the bet against the pot BEFORE the outstanding street bets:
                # a pot-size bet must read as ~1.0, not B/(P+B)
                pot_ex_bets = max(1, pot_before - sum(street_bets.values()))
                frac = (to_call / pot_ex_bets) if to_call > 0 else 0.0
                remaining = [s for s in range(n_seats) if s not in folded and s != hero_seat]
                villain_arch = seats[remaining[0]]["archetype"] if len(remaining) == 1 else None
                records.append({
                    "opponent_archs": [seats[s]["archetype"] for s in remaining],
                    "street": street,
                    "action": ev["action"],
                    "committed": ev["committed"],
                    "to_call": to_call,
                    "pot_before": pot_before,
                    "facing_frac": frac,
                    "facing_size": _size_bucket(frac) if to_call > 0 else None,
                    "bucket": bucket,
                    "board": list(board),
                    "aggressor_seat": last_aggressor,
                    "aggressor_arch": seats[last_aggressor]["archetype"] if last_aggressor is not None and last_aggressor in seats else None,
                    "villain_arch": villain_arch,
                    "players_in": n_seats - len(folded),
                    "hero_pos": seats[hero_seat]["pos"],
                    "code": hand_code(hero_hole),
                    "first_in": (street == "preflop" and not preflop_raise_seen and limpers == 0),
                    "facing_raise": (street == "preflop" and preflop_raise_seen),
                    "limpers": limpers,
                })

            # update state after the action
            street_bets[seat] = ev["to"]
            if ev["action"] == "fold":
                folded.add(seat)
            if ev["action"] in ("bet", "raise"):
                last_aggressor = seat
                if street == "preflop":
                    preflop_raise_seen = True
            if street == "preflop" and ev["action"] == "call" and not preflop_raise_seen:
                limpers += 1   # any preflop call before a raise is a limp (BTN included)

    return records


# ------------------------------------------------------------------------- rules
def _find(rule, kind, street, text, conf, ev=""):
    return {"rule": rule, "kind": kind, "street": street, "text": text, "confidence": conf, "ev": ev}


def _last_pre_raiser_pos(view):
    for ev in view["timeline"]:
        if ev.get("type") == "action" and ev.get("street") == "preflop" and ev.get("action") == "raise":
            return ev["pos"]
    return "MP"


def _limp_reraiser(view):
    """Seat that limped (called with NO prior raise) then re-raised preflop."""
    limped = set()
    raise_seen = False
    for ev in view["timeline"]:
        if ev.get("type") != "action" or ev.get("street") != "preflop":
            continue
        s = ev["seat"]
        if ev["action"] == "call" and not raise_seen:
            limped.add(s)   # a call AFTER a raise is a cold-call, not a limp
        elif ev["action"] == "raise":
            if s in limped:
                return s
            raise_seen = True
    return None


def rule_preflop(records, view):
    out = []
    c = chart()
    pre = [r for r in records if r["street"] == "preflop"]
    if not pre:
        return out
    r = pre[0]
    code, pos, act = r["code"], r["hero_pos"], r["action"]
    in_rfi = c.is_rfi(pos, code)

    if r["first_in"]:
        if act == "raise":
            if not in_rfi:
                out.append(_find("RFI", "leak", "preflop",
                    f"{code} is below a standard {pos} open in this rake-tight full-ring game — folding is the play.", 0.7))
            else:
                out.append(_find("RFI", "good", "preflop", f"Standard {pos} open with {code}. ✔", 0.5))
        elif act in ("check", "call"):
            if in_rfi and pos != "BB":
                out.append(_find("RFI", "leak", "preflop",
                    f"{code} wants to open-RAISE from {pos}, not limp — limping invites the field and forfeits fold equity.", 0.7))
        elif act == "fold":
            if in_rfi:
                out.append(_find("RFI", "leak", "preflop",
                    f"{code} is a fine open from {pos} — you can raise-first-in here.", 0.55))
    elif r["limpers"] > 0 and act in ("check", "call") and in_rfi and pos in ("HJ", "CO", "BTN", "SB"):
        out.append(_find("R7-iso", "leak", "preflop",
            f"{r['limpers']} limper(s) and you have {code} in position — ISOLATE (raise ~{4 + r['limpers']}bb), don't just call. Punishing limpers is a top EV source.", 0.8))
    elif r["limpers"] > 0 and act == "raise" and in_rfi:
        out.append(_find("R7-iso", "good", "preflop", f"Nice — isolating the limper(s) with {code}. ✔", 0.5))
    elif r["facing_raise"]:
        opener = _last_pre_raiser_pos(view)
        want = c.threebet_action(opener, code)
        if act in ("call", "check") and want == "value":
            out.append(_find("vsRFI", "leak", "preflop",
                f"{code} prefers a value 3-bet vs that open, not a flat.", 0.5))
        if act == "raise" and want == "fold":
            out.append(_find("vsRFI", "leak", "preflop",
                f"3-betting {code} vs that open is thin for a call-happy pool — value-lean your 3-bets.", 0.6))
    return out


def rule_overfold_big(records, view):
    out = []
    for r in records:
        if r["street"] in ("turn", "river") and r["to_call"] > 0 and r["facing_size"] in ("LARGE", "OVERBET"):
            if r["bucket"] is not None and TIER[r["bucket"]] <= TIER[MEDIUM_MADE] and r["aggressor_arch"] != "maniac":
                conf = 0.9 if r["aggressor_arch"] in ("nit", "station", "rec") else 0.7
                if r["action"] == "call":
                    out.append(_find("R1", "leak", r["street"],
                        f"You called a {r['facing_size'].lower()} {r['street']} bet with a bluff-catcher ({r['bucket'].replace('_',' ').lower()}). The 1/2 pool badly under-bluffs big bets — overfold here.", conf, "-1 big bet"))
                elif r["action"] == "fold":
                    out.append(_find("R1", "good", r["street"],
                        f"Good fold vs the big {r['street']} bet — this population is nowhere near bluffing enough to call. ✔", conf))
    return out


def rule_dont_bluff_stations(records, view):
    out = []
    for r in records:
        if r["street"] in ("flop", "turn", "river") and r["action"] in ("bet", "raise") and r["to_call"] == 0:
            if r["bucket"] in (AIR, WEAK_DRAW) and (r["villain_arch"] in ("station", "rec") or r["aggressor_arch"] in ("station", "rec")):
                out.append(_find("R3", "leak", r["street"],
                    "Bluffing into a calling station — they don't fold. Check and give up; take the free card / showdown instead.", 0.85, "burned bluff"))
    return out


def rule_thin_value_station(records, view):
    out = []
    for r in records:
        if r["street"] in ("turn", "river") and r["action"] == "check" and r["to_call"] == 0:
            opps = r.get("opponent_archs")
            if opps is None:
                opps = [r["villain_arch"]] if r["villain_arch"] else []
            sticky = bool(opps) and all(a in ("station", "rec") for a in opps)
            if r["bucket"] in (MEDIUM_MADE, STRONG_MADE) and sticky:
                out.append(_find("R4", "leak", r["street"],
                    f"You checked {r['bucket'].replace('_',' ').lower()} on the {r['street']} — vs sticky live players, bet BIG for thin value. They call too wide; sizing up prints.", 0.6, "missed value"))
    return out


def rule_thin_value_nit(records, view):
    out = []
    for r in records:
        if r["street"] in ("turn", "river") and r["action"] in ("bet", "raise") and r["to_call"] == 0:
            if r["bucket"] == MEDIUM_MADE and r["villain_arch"] == "nit":
                out.append(_find("R5", "leak", r["street"],
                    "Betting a medium hand into a nit — they only continue with better and fold worse, so you get no value and get raised when beat. Check it down.", 0.6, "thin vs nit"))
    return out


def rule_multiway_cbet_bluff(records, view):
    out = []
    for r in records:
        if r["street"] == "flop" and r["action"] == "bet" and r["to_call"] == 0:
            if r["bucket"] in (AIR, WEAK_DRAW) and r["players_in"] >= 3:
                out.append(_find("R13", "leak", "flop",
                    f"C-bet bluffing into {r['players_in'] - 1} opponents — multiway + a sticky pool kills fold equity. Check air multiway; bet for value or real equity.", 0.65, "spew"))
    return out


def rule_limp_reraise_premium(records, view):
    out = []
    lr = _limp_reraiser(view)
    if lr is None:
        return out
    for r in records:
        if r["street"] == "preflop" and r["action"] in ("call", "raise") and r["aggressor_seat"] == lr:
            if r["code"] not in ("AA", "KK", "QQ", "AKs", "AKo"):
                out.append(_find("R9", "leak", "preflop",
                    "A limp-reraise from the rec pool is AA/KK almost every time — get away from anything but a premium (or a small pair with clear set-mine odds).", 0.8, "trap"))
    return out


def rule_passive_raise_is_nuts(records, view):
    out = []
    for r in records:
        if r["street"] in ("turn", "river") and r["to_call"] > 0 and r["aggressor_arch"] in ("nit", "station", "rec"):
            if r["action"] == "call" and r["bucket"] is not None and TIER[MEDIUM_MADE] <= TIER[r["bucket"]] <= TIER[STRONG_MADE]:
                if r["facing_size"] in ("LARGE", "OVERBET"):
                    out.append(_find("R10", "leak", r["street"],
                        "A passive player firing big on a late street is the nuts, not a bluff — one pair / an overpair should fold here.", 0.75))
    return out


def rule_stack_off_vs_maniac(records, view):
    out = []
    for r in records:
        if r["aggressor_arch"] == "maniac" and r["action"] == "fold" and r["bucket"] is not None:
            if TIER[r["bucket"]] >= TIER[MEDIUM_MADE]:
                out.append(_find("R11", "info", r["street"],
                    "Careful overfolding vs the maniac — they bluff far too much. Widen your calls and let them barrel into your made hands.", 0.6))
    return out


def rule_rake_bloated_setmine(records, view):
    out = []
    for r in records:
        if r["street"] == "preflop" and r["facing_raise"] and r["action"] == "call":
            code = r["code"]
            small_pair = len(code) == 2 and RANK_ORDER[code[0]] <= RANK_ORDER["7"]
            if small_pair and r["hero_pos"] in ("SB", "BB"):
                out.append(_find("R18", "info", "preflop",
                    f"Cold-calling a raise OOP with {code} to set-mine leaks under heavy rake — you rarely get paid enough. Prefer 3-bet-or-fold from the blinds.", 0.55, "rake"))
    return out


RULES = [rule_preflop, rule_overfold_big, rule_dont_bluff_stations, rule_thin_value_station,
         rule_thin_value_nit, rule_multiway_cbet_bluff, rule_limp_reraise_premium,
         rule_passive_raise_is_nuts, rule_stack_off_vs_maniac, rule_rake_bloated_setmine]


def review(view: dict, hero_hole: list[str]) -> dict:
    if not hero_hole or not view.get("hand_over"):
        return {"findings": [], "summary": "Hand in progress."}
    records = replay(view, hero_hole)
    pot = (view.get("result") or {}).get("pot", 0)
    findings: list[dict] = []
    for rule in RULES:
        try:
            findings.extend(rule(records, view))
        except Exception:
            continue

    weight = max(1.0, pot / 40.0)
    for f in findings:
        f["_score"] = f["confidence"] * (weight if f["kind"] == "leak" else 1.0)
    findings.sort(key=lambda f: (f["kind"] != "leak", -f["_score"]))

    leaks = [f for f in findings if f["kind"] == "leak"]
    if leaks:
        summary = f"{len(leaks)} leak(s) flagged — top fix: {leaks[0]['text']}"
    elif any(f["kind"] == "good" for f in findings):
        summary = "Clean hand — you took the exploit line. ✔"
    else:
        summary = "No major leaks. Standard spot."
    for f in findings:
        f.pop("_score", None)
    return {"findings": findings[:4], "summary": summary}


# --------------------------------------------------------------------- self-test
if __name__ == "__main__":
    base_view = {"seats": [{"seat": i, "pos": "BTN", "archetype": "station", "active": True} for i in range(9)],
                 "hero_seat": 0, "hand_over": True, "button_seat": 8, "timeline": [], "result": {"pot": 120}}

    def rec(**kw):
        d = dict(street="river", action="call", to_call=60, pot_before=80, facing_frac=0.75,
                 facing_size="LARGE", bucket=MEDIUM_MADE, board=["Ah", "7c", "2d", "Ts", "3h"],
                 aggressor_seat=3, aggressor_arch="station", villain_arch="station", players_in=2,
                 hero_pos="BTN", code="A5o", first_in=False, facing_raise=False, limpers=0)
        d.update(kw)
        return d

    r = rule_overfold_big([rec()], base_view)
    assert r and r[0]["rule"] == "R1" and r[0]["kind"] == "leak"
    print("ok  R1 fires on hero-call of big river with bluffcatcher")

    r = rule_overfold_big([rec(action="fold")], base_view)
    assert r and r[0]["kind"] == "good"
    print("ok  R1 rewards the disciplined fold")

    r = rule_dont_bluff_stations([rec(action="bet", bucket=AIR, to_call=0)], base_view)
    assert r and r[0]["rule"] == "R3"
    print("ok  R3 fires on bluffing a station")

    r = rule_thin_value_station([rec(action="check", to_call=0, bucket=STRONG_MADE)], base_view)
    assert r and r[0]["rule"] == "R4"
    print("ok  R4 fires on missed thin value vs station")

    r = rule_thin_value_nit([rec(action="bet", to_call=0, bucket=MEDIUM_MADE, villain_arch="nit")], base_view)
    assert r and r[0]["rule"] == "R5"
    print("ok  R5 fires on betting medium into a nit")

    r = rule_multiway_cbet_bluff([rec(street="flop", action="bet", to_call=0, bucket=AIR, players_in=4,
                                      board=["Ah", "7c", "2d"])], base_view)
    assert r and r[0]["rule"] == "R13"
    print("ok  R13 fires on multiway air c-bet")

    # R9: villain seat 5 limps then reraises; hero flats with QJs
    v9 = {**base_view, "timeline": [
        {"type": "action", "street": "preflop", "seat": 5, "action": "call", "to": 2, "pos": "MP"},
        {"type": "action", "street": "preflop", "seat": 5, "action": "raise", "to": 20, "pos": "MP"},
    ]}
    r = rule_limp_reraise_premium([rec(street="preflop", action="call", aggressor_seat=5, code="QJs", board=[])], v9)
    assert r and r[0]["rule"] == "R9"
    print("ok  R9 fires on continuing vs a limp-reraise with a non-premium")

    r = rule_rake_bloated_setmine([rec(street="preflop", action="call", facing_raise=True, code="44",
                                       hero_pos="SB", board=[])], base_view)
    assert r and r[0]["rule"] == "R18"
    print("ok  R18 fires on OOP small-pair set-mine call vs rake")

    v2 = {**base_view, "seats": [{"seat": i, "pos": "UTG" if i == 0 else "BTN", "archetype": "station", "active": True} for i in range(9)]}
    pre = rec(street="preflop", action="call", to_call=2, bucket=None, code="AA", hero_pos="UTG", first_in=True, board=[])
    r = rule_preflop([pre], v2)
    assert any(f["rule"] == "RFI" and f["kind"] == "leak" for f in r)
    print("ok  preflop rule flags limping a premium")

    print("ALL REVIEW UNIT TESTS PASSED")
