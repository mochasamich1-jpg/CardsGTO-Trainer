"""
Persistent 9-max NLHE cash table built on top of PokerKit.

PokerKit models ONE hand per State object, so this wraps it into a durable Table
that carries stacks between hands, rotates the button, applies our own live rake
(10% capped at $6, no-flop-no-drop), and records a full action timeline (PokerKit
keeps no semantic history of its own).

Bot decisions are injected via a `bot_policy(archetype, ctx) -> action` callable so
this module stays independent of the bot logic.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pokerkit import Automation, Mode, NoLimitTexasHoldem
from pokerkit.state import ChipsPushing

AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
)

# PokerKit seat order after create_state is SB, BB, UTG, ... , BTN.
POSITION_NAMES_9 = ["SB", "BB", "UTG", "UTG1", "MP", "LJ", "HJ", "CO", "BTN"]

SB = 1
BB = 2
MIN_BET = 2


def card_str(card) -> str:
    """PokerKit Card -> compact 'Qc' code."""
    return f"{card.rank.value}{card.suit.value}"


def street_name(board_len: int) -> str:
    return {0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(board_len, "river")


@dataclass
class Player:
    seat: int          # physical seat 0..8 (fixed)
    name: str
    archetype: str     # 'hero' or a bot archetype key
    stack: int
    is_hero: bool = False


# A bot policy takes (archetype, context dict) and returns an action dict:
#   {"action": "fold"} | {"action": "check"} | {"action": "call"}
#   | {"action": "bet", "to": int} | {"action": "raise", "to": int}
BotPolicy = Callable[[str, dict], dict]


class Table:
    def __init__(
        self,
        players: list[Player],
        bot_policy: BotPolicy,
        rake_pct: float = 0.10,
        rake_cap: int = 6,
        max_buyin: int = 200,
        rebuy_below: int = 40,
    ):
        assert len(players) == 9, "this table is 9-handed"
        self.players = players
        self.bot_policy = bot_policy
        self.rake_pct = rake_pct
        self.rake_cap = rake_cap
        self.max_buyin = max_buyin
        self.rebuy_below = rebuy_below
        self.n = len(players)

        self.hero_seat = next((p.seat for p in players if p.is_hero), None)
        self.button = self.hero_seat if self.hero_seat is not None else 0
        self.hand_number = 0
        self.hero_session_net = 0
        self.hands_completed = 0
        self.session_start = time.time()

        # per-hand state
        self.state = None
        self.physical_of_pk: list[int] = []
        self.pk_of_physical: list[int] = []
        self.timeline: list[dict] = []
        self.pot = 0
        self.prev_board_len = 0
        self.hand_over = True
        self.awaiting_hero = False
        self.result: Optional[dict] = None
        self.hero_hole: list[str] = []
        self.all_holes: dict[int, list[str]] = {}   # physical seat -> hole cards (snapshot at deal)
        self.folded_seats: set[int] = set()          # physical seats that have folded this hand

    # ------------------------------------------------------------------ hands
    def start_hand(self) -> dict:
        if self.state is not None and not self.hand_over:
            raise RuntimeError("hand already in progress")
        self._rebuys()
        self.button = (self.button + 1) % self.n
        self.hand_number += 1

        # PokerKit index k (0=SB..8=BTN) maps to physical seat (button+1+k)
        self.physical_of_pk = [(self.button + 1 + k) % self.n for k in range(self.n)]
        self.pk_of_physical = [0] * self.n
        for k, phys in enumerate(self.physical_of_pk):
            self.pk_of_physical[phys] = k

        stacks = tuple(self.players[self.physical_of_pk[k]].stack for k in range(self.n))
        self.state = NoLimitTexasHoldem.create_state(
            AUTOMATIONS, False, 0, (SB, BB), MIN_BET, stacks, self.n, mode=Mode.CASH_GAME
        )

        self.timeline = []
        self.pot = SB + BB  # blinds already posted by automation
        self.prev_board_len = 0
        self.hand_over = False
        self.awaiting_hero = False
        self.result = None
        self.hero_hole = []
        self.all_holes = {}
        self.folded_seats = set()

        sb_phys = self.physical_of_pk[0]
        bb_phys = self.physical_of_pk[1]
        self.timeline.append(
            {"type": "blinds", "sb_seat": sb_phys, "bb_seat": bb_phys, "sb": SB, "bb": BB, "pot": self.pot}
        )

        self._advance()
        return self.view()

    def hero_action(self, action: str, to: Optional[int] = None) -> dict:
        if self.hand_over or not self.awaiting_hero:
            raise RuntimeError("not awaiting a hero action")
        pk = self.state.actor_index
        assert pk is not None and self.players[self.physical_of_pk[pk]].is_hero
        self._apply(pk, self._normalize_hero(action, to))
        self.awaiting_hero = False
        self._advance()
        return self.view()

    # ------------------------------------------------------------ core engine
    def _advance(self):
        st = self.state
        while st.status:
            if st.can_deal_hole():
                st.deal_hole()
                self._capture_holes()
                continue
            if st.can_select_runout_count():
                st.select_runout_count(1)   # all-in: always run it once, then deal the board out
                continue
            if st.can_deal_board():
                st.deal_board()
                self._on_board()
                continue
            pk = st.actor_index
            if pk is None:
                break
            phys = self.physical_of_pk[pk]
            player = self.players[phys]
            if player.is_hero:
                self.awaiting_hero = True
                return
            action = self.bot_policy(player.archetype, self._ctx(pk))
            self._apply(pk, action)
        self.awaiting_hero = False
        self._resolve()

    def _apply(self, pk: int, action: dict):
        # Amounts are read off the PokerKit operation objects, NOT from stack
        # deltas: the hand-ending action synchronously runs bet collection +
        # chips pushing, so a stack delta would absorb refunds and winnings.
        st = self.state
        to_call = st.checking_or_calling_amount or 0
        bet_before = st.bets[pk]
        verb = action["action"]
        if verb == "fold" and to_call == 0:
            verb = "check"  # folding for free is nonsensical; treat as a check
        if verb == "fold":
            st.fold()
            committed = 0
            to_amt = bet_before
        elif verb in ("check", "call"):
            op = st.check_or_call()
            committed = op.amount
            to_amt = bet_before + committed
        elif verb in ("bet", "raise"):
            to = self._clamp_raise(int(action["to"]))
            op = st.complete_bet_or_raise_to(to)
            committed = op.amount - bet_before
            to_amt = op.amount
        else:
            raise ValueError(f"bad action {action!r}")
        self.pot = self._pot_now()

        phys = self.physical_of_pk[pk]
        board_len = self._board_len()
        # canonicalize the recorded verb: a "call" of 0 is really a check, and
        # aggression with nothing outstanding is a bet, not a raise
        rec_verb = verb
        if verb in ("check", "call"):
            rec_verb = "check" if committed == 0 else "call"
        elif verb in ("bet", "raise"):
            rec_verb = "bet" if to_call == 0 else "raise"
        self.timeline.append(
            {
                "type": "action",
                "seat": phys,
                "pos": POSITION_NAMES_9[pk],
                "name": self.players[phys].name,
                "archetype": self.players[phys].archetype,
                "is_hero": self.players[phys].is_hero,
                "street": street_name(board_len),
                "action": rec_verb,
                "committed": committed,
                "to": to_amt,
                "to_call": to_call,
                "pot": self.pot,
            }
        )
        if rec_verb == "fold":
            self.folded_seats.add(phys)

    def _on_board(self):
        board_len = self._board_len()
        flat = self._board_flat()
        new_cards = [card_str(c) for c in flat[self.prev_board_len:]]
        self.prev_board_len = board_len
        self.pot = self._pot_now()
        self.timeline.append(
            {"type": "board", "street": street_name(board_len), "cards": new_cards, "pot": self.pot}
        )

    def _resolve(self):
        st = self.state
        payoffs = list(st.payoffs)
        board_len = self._board_len()
        flop_dealt = board_len >= 3

        # The true final pot is what PokerKit actually pushed to players —
        # net of uncalled-bet refunds. Winners are whoever received chips,
        # which also covers exact chops (payoff == 0 but pot chips received).
        pushed = [0] * self.n
        for op in st.operations:
            if isinstance(op, ChipsPushing):
                for k, amt in enumerate(op.amounts):
                    pushed[k] += amt
        total_pot = sum(pushed)
        self.pot = total_pot

        rake = 0
        if flop_dealt and total_pot > 0:
            rake = min(self.rake_cap, round(self.rake_pct * total_pot))

        winners = [k for k in range(self.n) if pushed[k] > 0]
        rake_share = [0] * self.n
        if rake > 0 and winners:
            assigned = 0
            for k in winners:
                share = min(pushed[k], round(rake * pushed[k] / total_pot))
                rake_share[k] = share
                assigned += share
            big = max(winners, key=lambda k: pushed[k])  # absorb rounding
            rake_share[big] = max(0, min(pushed[big], rake_share[big] + (rake - assigned)))
            rake = sum(rake_share)  # report exactly what was collected

        for k in range(self.n):
            phys = self.physical_of_pk[k]
            self.players[phys].stack = st.stacks[k] - rake_share[k]

        if self.hero_seat is not None:
            hero_pk = self.pk_of_physical[self.hero_seat]
            hero_payoff = payoffs[hero_pk] - rake_share[hero_pk]
            self.hero_session_net += hero_payoff
        else:
            hero_payoff = 0

        reached = [s for s in range(self.n) if s not in self.folded_seats]
        showdown = len(reached) > 1
        reveal = {}
        if showdown:
            for s in reached:
                if s in self.all_holes:
                    reveal[s] = self.all_holes[s]

        winner_seats = [self.physical_of_pk[k] for k in winners]
        self.result = {
            "board": [card_str(c) for c in self._board_flat()],
            "pot": total_pot,
            "rake": rake,
            "winners": winner_seats,
            "winner_names": [self.players[s].name for s in winner_seats],
            "payoffs": {self.physical_of_pk[k]: payoffs[k] - rake_share[k] for k in range(self.n)},
            "hero_payoff": hero_payoff,
            "showdown": showdown,
            "revealed": reveal,
        }
        self.timeline.append({"type": "showdown" if showdown else "win", **self.result})
        self.hand_over = True
        self.hands_completed += 1

    # ------------------------------------------------------------- bot context
    def _ctx(self, pk: int) -> dict:
        """Everything a bot needs to decide, in PokerKit-index terms."""
        st = self.state
        phys = self.physical_of_pk[pk]
        to_call = st.checking_or_calling_amount or 0
        can_raise = bool(st.can_complete_bet_or_raise_to())
        return {
            "pk": pk,
            "seat": phys,
            "pos": POSITION_NAMES_9[pk],
            "hole": [card_str(c) for c in st.hole_cards[pk]],
            "board": [card_str(c) for c in self._board_flat()],
            "street": street_name(self._board_len()),
            "stack": st.stacks[pk],
            "bet": st.bets[pk],
            "to_call": to_call,
            "pot": self.pot,
            "can_check": to_call == 0,
            "can_fold": bool(st.can_fold()),
            "can_raise": can_raise,
            "min_raise_to": st.min_completion_betting_or_raising_to_amount if can_raise else None,
            "max_raise_to": st.max_completion_betting_or_raising_to_amount if can_raise else None,
            "big_blind": BB,
            "num_active": sum(1 for s in st.statuses if s),
            "bets": list(st.bets),
            "timeline": self.timeline,
        }

    # ----------------------------------------------------------------- helpers
    def _normalize_hero(self, action: str, to: Optional[int]) -> dict:
        st = self.state
        action = action.lower()
        if action == "fold":
            if not st.can_fold():
                return {"action": "check"}  # nothing to fold to
            return {"action": "fold"}
        if action in ("check", "call"):
            return {"action": "check" if (st.checking_or_calling_amount or 0) == 0 else "call"}
        if action in ("bet", "raise"):
            if to is None:
                to = st.min_completion_betting_or_raising_to_amount
            return {"action": action, "to": int(to)}
        raise ValueError(f"unknown hero action {action!r}")

    def _clamp_raise(self, to: int) -> int:
        st = self.state
        lo = st.min_completion_betting_or_raising_to_amount
        hi = st.max_completion_betting_or_raising_to_amount
        if lo is None:
            raise RuntimeError("raise not legal here")
        return max(lo, min(hi, to))

    def _pot_now(self) -> int:
        """Live pot including outstanding bets; final pushed pot once the hand ends."""
        st = self.state
        if st.status:
            return st.total_pot_amount
        return sum(op.total_amount for op in st.operations if isinstance(op, ChipsPushing))

    def _board_flat(self):
        return [c for grp in self.state.board_cards for c in grp]

    def _board_len(self) -> int:
        return len(self._board_flat())

    def _capture_holes(self):
        """Snapshot every player's hole cards at deal time (PokerKit mucks losers later)."""
        for pk in range(self.n):
            cards = self.state.hole_cards[pk]
            if cards and len(cards) == 2 and all(c is not None for c in cards):
                self.all_holes[self.physical_of_pk[pk]] = [card_str(c) for c in cards]
        if self.hero_seat is not None and self.hero_seat in self.all_holes:
            self.hero_hole = self.all_holes[self.hero_seat]

    def _rebuys(self):
        for p in self.players:
            if p.stack < self.rebuy_below:
                p.stack = self.max_buyin

    # -------------------------------------------------------------------- view
    def hero_options(self) -> Optional[dict]:
        if not self.awaiting_hero:
            return None
        st = self.state
        to_call = st.checking_or_calling_amount or 0
        can_raise = bool(st.can_complete_bet_or_raise_to())
        return {
            "can_fold": bool(st.can_fold()) and to_call > 0,
            "can_check": to_call == 0,
            "to_call": to_call,
            "can_raise": can_raise,
            "min_raise_to": st.min_completion_betting_or_raising_to_amount if can_raise else None,
            "max_raise_to": st.max_completion_betting_or_raising_to_amount if can_raise else None,
            "pot": self.pot,
        }

    def view(self) -> dict:
        st = self.state
        actor_pk = st.actor_index if (not self.hand_over) else None
        seats = []
        for phys in range(self.n):
            pk = self.pk_of_physical[phys]
            player = self.players[phys]
            cards = None
            if player.is_hero:
                cards = self.hero_hole or None
            elif self.hand_over and self.result and phys in self.result["revealed"]:
                cards = self.result["revealed"][phys]
            seats.append(
                {
                    "seat": phys,
                    "name": player.name,
                    "archetype": player.archetype,
                    "pos": POSITION_NAMES_9[pk],
                    # post-hand, show the durable (post-rake) stack the player
                    # actually starts the next hand with
                    "stack": player.stack if self.hand_over else st.stacks[pk],
                    "bet": st.bets[pk],
                    "active": phys not in self.folded_seats,
                    "is_hero": player.is_hero,
                    "is_button": phys == self.button,
                    "is_actor": (actor_pk == pk),
                    "cards": cards,
                }
            )
        return {
            "hand_number": self.hand_number,
            "button_seat": self.button,
            "hero_seat": self.hero_seat,
            "hero_hole": self.hero_hole,
            "hero_session_net": self.hero_session_net,
            "hands_completed": self.hands_completed,
            "session_seconds": int(time.time() - self.session_start),
            "board": [card_str(c) for c in self._board_flat()],
            "pot": self.pot,
            "street": street_name(self._board_len()),
            "seats": seats,
            "awaiting_hero": self.awaiting_hero,
            "hero_options": self.hero_options(),
            "hand_over": self.hand_over,
            "result": self.result,
            "timeline": self.timeline,
        }


# --------------------------------------------------------------------- smoke test
if __name__ == "__main__":
    import random

    def stub_policy(archetype: str, ctx: dict) -> dict:
        # crude: fold to big bets sometimes, mostly check/call, occasional min-raise
        if ctx["to_call"] > ctx["stack"] * 0.5 and random.random() < 0.6:
            if ctx["can_fold"]:
                return {"action": "fold"}
        if ctx["can_raise"] and random.random() < 0.12:
            return {"action": "raise", "to": ctx["min_raise_to"]}
        return {"action": "call"}

    ps = [
        Player(seat=i, name=f"P{i}", archetype=("hero" if i == 0 else "station"), stack=200, is_hero=(i == 0))
        for i in range(9)
    ]
    table = Table(ps, stub_policy)

    hands = 0
    hero_auto = lambda opts: (
        {"action": "fold"}
        if opts["can_fold"] and opts["to_call"] > 20
        else {"action": "check" if opts["can_check"] else "call"}
    )
    for _ in range(200):
        v = table.start_hand()
        guard = 0
        while v["awaiting_hero"] and guard < 50:
            guard += 1
            a = hero_auto(v["hero_options"])
            v = table.hero_action(a["action"], a.get("to"))
        hands += 1
        total_chips = sum(p.stack for p in table.players)
        assert all(p.stack >= 0 for p in table.players), "negative stack!"
        assert v["hand_over"], "hand did not finish"

    print(f"OK: played {hands} hands, no crashes.")
    print("final stacks:", [p.stack for p in table.players])
    print("hero session net:", table.hero_session_net)
    print("total chips on table:", sum(p.stack for p in table.players))
