# CardsGTO Trainer

A free, open-source poker trainer for **live $1/$2 9-max No-Limit Hold'em**. You sit at a
PokerStars-style table, play hands fast against realistic recreational bots, and get an
instant **population-exploit review** after every hand.

> **Why "exploit" and not GTO?** At $1/$2 live, the money doesn't come from out-solving
> anyone — it comes from the pool being bad and you not being. So the "correct" play this
> trainer teaches is the *exploit* (overfold vs big rivers, don't bluff stations, value bet
> thin and big, isolate limpers, avoid rake-bloated pots), with GTO as a theory anchor.
> A real solver (TexasSolver) plugs in later as that anchor — see the roadmap.

Built for the **cardsgto** YouTube channel.

## Features

- **Full 9-max table** — deal, bet, showdown, side pots, button rotation, live $6-cap rake
  (10%, no-flop-no-drop), carried stacks + auto-rebuys.
- **5 realistic population bots** — calling station, rec limper, nit, TAG reg, and spewy
  maniac, each with distinct VPIP/PFR/aggression and postflop tendencies. A typical lineup
  mixes them the way a real $1/$2 table does.
- **Instant post-hand review** — replays your decisions and fires ~exploit rules
  (R1–R20 from the strategy base): overfold vs third barrels, don't bluff stations, thin+big
  value, isolate limpers, respect passive raises, stack off vs maniacs, and more.
- **Rake-adjusted preflop answer key** — full-ring 9-max RFI / 3-bet / limper-iso ranges,
  tightened for heavy rake and a recreational pool.
- **Training-room UI** — dark session dashboard: live stats bar (hands, hands/hr, clean-hand
  rate, net, bb/100), a running hand feed, in-panel post-hand review, a dynamic FOCUS card
  that tracks your most-flagged leak, and keyboard shortcuts (F fold / C check-call /
  R raise / Enter confirm-deal). Refreshing mid-hand resumes exactly where you were.
- **Fast** — click through many hands per hour to accelerate reps.

## Quickstart

Requires Python 3.11+ (developed on 3.14).

```bash
# from the project root
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

.venv/Scripts/python -m uvicorn server.app:app --port 8000
```

Then open **http://localhost:8000** in your browser. Click **Deal next hand** and play.

## How it works

```
web/                 vanilla-JS table UI (no build step)
server/
  engine.py          PokerKit wrapper: persistent 9-max table, rake, action timeline
  bots.py            5 population archetypes (heuristic policies)
  ranges.py          range-notation parser + preflop answer-key lookup
  evaluator.py       postflop hand-strength + board-texture buckets
  review.py          exploit-rule engine (the coaching brain)
  app.py             FastAPI server + game API
data/
  preflop_ranges.json   rake-adjusted 9-max ranges
```

The poker rules engine is [PokerKit](https://github.com/uoftcprg/pokerkit) (MIT). Everything
else — the table, bots, review brain, and strategy data — is original.

Each backend module is self-testing: `python -m server.engine` (200-hand smoke),
`python -m server.ranges`, `python -m server.evaluator`, `python -m server.bots`
(VPIP/PFR calibration), `python -m server.review` (rule unit tests).

## Roadmap

- **Phase 2** — bundle [TexasSolver](https://github.com/bupticybee/TexasSolver) (AGPL) to
  pre-solve a postflop-solution database offline, so the review can show *GTO vs exploit
  deltas* ("population says fold, GTO defends 30% — here's why you fold at 1/2"). Bot
  VPIP/PFR calibration harness. Facing-raise preflop grading.
- **Phase 3** — polish to full PokerStars-table feel, session leak-tracking over time,
  hand-history import, recalibrate rules from your own tracked hands.

## License & credits

**AGPL-3.0** (see `LICENSE`). This keeps the trainer free and open — anyone can use and fork
it, but derivatives stay open too. AGPL is also what lets us bundle TexasSolver in Phase 2.

- Rules engine: [PokerKit](https://github.com/uoftcprg/pokerkit) (MIT)
- Phase-2 solver: [TexasSolver](https://github.com/bupticybee/TexasSolver) (AGPL-3.0)
- Strategy basis: Ed Miller *The Course*, Upswing Poker, PokerCoaching (Jonathan Little),
  Run It Once population tendencies, GTO Wizard.

Educational tool. Not affiliated with PokerStars or any operator. Play within your local laws.
