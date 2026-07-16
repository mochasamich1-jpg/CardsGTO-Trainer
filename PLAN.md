# CardsGTO Trainer — Build Plan & Status

A free, open-source poker trainer: you sit at a 9-handed live-style $1/$2 NLHE table,
play hands fast against realistic recreational bots, and get an instant **population-exploit**
review after every hand. Built for the **cardsgto** YouTube channel.

## Locked scope (the game we train)
- $1/$2 No-Limit Hold'em, **live, 9-handed full ring**
- $100 min / $200 max buy-in, **no stack cap** (stacks can grow past $200 after winning)
- No ante, no straddle
- Rake: **10% capped at $6, no flop = no drop**, no extra jackpot drop

## Key decisions (from recon, 2026-07-15)
- **Build fresh — do NOT fork LibreGTO.** LibreGTO (MIT) is a thin 6-max *preflop-only drill*
  app with no engine, no bots, no postflop, no exploit modeling. Every hard part is absent.
- **Engine: PokerKit** (github.com/uoftcprg/pokerkit, MIT, pure-Python). Handles dealing,
  legal-action enumeration, betting, showdown, side pots. Gotcha: it models **one hand per
  State object** → we wrap it in a persistent `Table` (carries stacks, rotates button, applies
  our own rake so we control the $6 cap + no-flop-no-drop rule).
- **App license: AGPL-3.0** (free public repo). Lets us bundle TexasSolver later with no conflict.
- **Solver: TexasSolver `console_solver`** (AGPL) — *Phase 2*. Used offline to pre-solve a
  postflop-solution DB (live solving is too slow for a fast trainer). Not needed for the MVP.
- **Review brain = population-EXPLOIT rules, not GTO.** At 1/2 the "correct" play is the
  exploit (overfold vs big rivers, don't bluff stations, thin+big value, isolate limpers,
  avoid rake-bloated pots…). GTO is only a theory anchor (Phase 2).

## Architecture
- **Backend:** Python 3.14 + FastAPI (REST) serving the static frontend + game API.
  - `server/engine.py` — `Table` wrapper over PokerKit: persistent stacks, button rotation,
    rake, structured **action log** (PokerKit has no built-in history).
  - `server/ranges.py` — parse standard range notation ("22+, ATs+, AJo+") → hand sets; lookup by seat.
  - `server/bots.py` — 5 population archetypes (station / nit / TAG / maniac / rec limper) as
    heuristic policies driven by data in `data/`.
  - `server/review.py` — ordered exploit-rule engine (R1–R20); fires matching rules on the
    parsed hand, ranks by confidence × pot size, returns top coaching notes.
  - `server/app.py` — FastAPI endpoints + static serving.
- **Frontend:** vanilla HTML/CSS/JS in `web/` — 9-max oval table, hero cards, action controls
  (fold/check/call/bet+raise slider), bot-action animation, post-hand review panel, "next hand".
- **Data:** `data/preflop_ranges.json`, `data/bot_archetypes.json` (tunable without code).

## Data flow
`New hand` → Table deals, bots act until it's hero's turn → return state + legal actions +
action log. `Hero acts` → apply, bots act until hero's turn or hand end → return new state
(+ review when hand ends). Frontend replays the action log with small delays for a live feel.

## Phased plan / status
- [x] Phase 0 — Recon (LibreGTO/PokerKit/TexasSolver/charts/bots+rules). **Done.**
- [x] Toolchain + venv de-risked (PokerKit + FastAPI import clean on Py 3.14).
- [x] **Phase 1 — MVP: DONE & verified end-to-end in browser (2026-07-15).**
  - [x] Engine `Table` wrapper (deal, act, showdown, side pots, rake, button rotation, timeline) — 200-hand smoke passes
  - [x] Range parser + rake-adjusted preflop answer key — self-test passes
  - [x] Population bots (5 archetypes) — VPIP/PFR differentiated correctly
  - [x] Postflop hand-strength evaluator — unit tests pass
  - [x] Exploit review engine (R1, R3, R4, R7, R10, R11 + preflop RFI/iso; scaffolded for the rest) — unit tests pass
  - [x] FastAPI server + endpoints (/api/new-hand, /api/action, /api/reset, /api/state)
  - [x] Web table UI + animated bot actions + betting controls + review panel
  - [x] Verified live: deal / fold / call / raise-with-presets / resolve / review all work
- [ ] Phase 2 — TexasSolver pre-solved postflop DB as GTO theory anchor; bot calibration
      harness (VPIP/PFR/AF checksum over 10k sim hands); richer review (GTO vs exploit deltas).
- [ ] Phase 3 — PokerStars-grade polish, session stats / leak tracking, hand-history import,
      recalibrate rules from user's own histories.

## How to run (once MVP lands)
```
cd CardsGTO-Trainer
.venv/Scripts/python -m uvicorn server.app:app --port 8000
# open http://localhost:8000
```

## Sources
Recon findings + citations captured in this session (Upswing/PokerCoaching/Ed Miller The
Course/Run It Once/GTO Wizard for strategy; PokerKit docs; TexasSolver console branch).
