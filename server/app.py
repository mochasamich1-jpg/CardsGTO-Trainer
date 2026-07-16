"""
FastAPI server: serves the table UI and the game API.

Single local session (it's a personal trainer). One global Table; the frontend
drives it hand by hand. Run:  python -m uvicorn server.app:app --port 8000
"""
from __future__ import annotations

import os
import warnings

warnings.filterwarnings("ignore")  # silence PokerKit's benign "no reason to fold" UserWarnings

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bots import bot_policy
from .engine import Player, Table
from .review import review

BASE = os.path.dirname(os.path.dirname(__file__))
WEB = os.path.join(BASE, "web")

# A realistic live 1/2 lineup: mostly recreational/passive with a couple of regs.
LINEUP = [
    ("station", "Big Mike"),
    ("rec", "Sunglasses"),
    ("nit", "Grandpa Joe"),
    ("station", "Hoodie"),
    ("tag", "The Reg"),
    ("maniac", "Spewy Sam"),
    ("rec", "Vacation Vic"),
    ("nit", "Rock Randy"),
]

app = FastAPI(title="CardsGTO Trainer")


@app.middleware("http")
async def _no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"   # always serve fresh assets during dev
    return response


def new_table() -> Table:
    players = [Player(seat=0, name="You", archetype="hero", stack=200, is_hero=True)]
    for i, (arch, name) in enumerate(LINEUP, start=1):
        players.append(Player(seat=i, name=name, archetype=arch, stack=200))
    return Table(players, bot_policy)


def new_stats() -> dict:
    return {"reviewed": 0, "clean": 0, "leaks": 0, "leak_counts": {}}


TABLE = new_table()
STATS = new_stats()
_LAST_REVIEW: dict = {"hand": 0, "review": None}


class Act(BaseModel):
    action: str
    to: int | None = None


def _augment(v: dict) -> dict:
    """Attach the post-hand review (computed once per hand) and session stats."""
    if v.get("hand_over") and TABLE.hand_number and TABLE.hero_hole:
        if _LAST_REVIEW["hand"] != TABLE.hand_number:
            rv = review(v, TABLE.hero_hole)
            _LAST_REVIEW["hand"] = TABLE.hand_number
            _LAST_REVIEW["review"] = rv
            leaks = [f for f in rv["findings"] if f["kind"] == "leak"]
            STATS["reviewed"] += 1
            STATS["leaks"] += len(leaks)
            if not leaks:
                STATS["clean"] += 1
            for f in leaks:
                STATS["leak_counts"][f["rule"]] = STATS["leak_counts"].get(f["rule"], 0) + 1
        v["review"] = _LAST_REVIEW["review"]
    v["stats"] = STATS
    return v


def _empty_view() -> dict:
    return {"hand_over": True, "seats": [], "stats": STATS}


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.post("/api/new-hand")
def new_hand():
    if TABLE.state is not None and not TABLE.hand_over:
        return _augment(TABLE.view())   # mid-hand deal click: no-op, resync the client
    return _augment(TABLE.start_hand())


@app.post("/api/action")
def action(a: Act):
    if TABLE.state is None:
        return _empty_view()
    try:
        return _augment(TABLE.hero_action(a.action, a.to))
    except (RuntimeError, ValueError, AssertionError, TypeError):
        # out-of-sync or malformed client request: never 500, just resync
        return _augment(TABLE.view())


@app.get("/api/state")
def state():
    if TABLE.state is None:
        return _empty_view()
    return _augment(TABLE.view())


@app.post("/api/reset")
def reset():
    global TABLE, STATS
    TABLE = new_table()
    STATS = new_stats()
    _LAST_REVIEW["hand"] = 0
    _LAST_REVIEW["review"] = None
    return {"ok": True}


app.mount("/static", StaticFiles(directory=WEB), name="static")
