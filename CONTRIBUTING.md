# contributing

## share your data

the most valuable contribution is running ccmeter and sharing your report:

```bash
pip install ccmeter
ccmeter install
# wait a few days
ccmeter report
```

post your `ccmeter report` output in a [GitHub discussion](https://github.com/iteebz/ccmeter/discussions). include your tier — the numbers mean different things on pro vs max 5x vs max 20x vs team.

cleanest data comes from sessions where you're only using claude code (not claude.ai simultaneously).

## contribute code

```bash
git clone https://github.com/iteebz/ccmeter
cd ccmeter
just install
```

`just ci` runs lint + typecheck + tests. `just format` before committing.

### what we need

- **more tiers** — ccmeter has only been tested on max 20x. pro, max 5x, and team users would immediately tell us how limits scale across plans.
- **windows support** — auth.py needs a Windows Credential Manager backend.
- **better calibration** — confidence intervals, outlier detection, weighted averages for mixed-model windows.
- **visualization** — budget over time per bucket. if the number drops, the cap shrank.

### adding a migration

schema changes go in `ccmeter/migrations/`. create `NNN_description.py` with an `up(conn)` function. never modify a shipped migration.

### style

- fncli for CLI, not click/argparse
- pyright strict
- stdlib over external deps
- print() for output
- all display through `display.py`
