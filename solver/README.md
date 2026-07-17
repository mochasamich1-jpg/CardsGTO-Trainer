# Solver (Phase 2 GTO anchor)

CardsGTO uses [TexasSolver](https://github.com/bupticybee/TexasSolver) (AGPL-3.0, same
license as this project) as its GTO anchor: spots are pre-solved offline and the review
engine compares the live-pool **exploit** line against the **GTO** baseline.

The solver binary is not committed. Install it with:

```powershell
powershell -ExecutionPolicy Bypass -File solver/fetch_solver.ps1
```

That downloads the official v0.2.0 Windows release (39 MB) and installs the two files
the console solver needs (~56 MB on disk):

```
solver/texassolver/console_solver.exe
solver/texassolver/resources/compairer/card5_dic_sorted.txt
```

The Python wrapper is `server/solver.py` (`python -m server.solver` self-tests it).
Solve dumps land in `solver/outputs/` (gitignored).
