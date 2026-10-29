# Contributing

Thanks for taking a look. Issues and pull requests are welcome.

## Development setup

```powershell
git clone <this repo>
cd MultisimForge
python -m pip install -e .[capture,dev]
MultisimForge parts init
```

`parts init` needs a licensed Multisim install; everything else works without it.

## What runs without Multisim

The offline half of the codebase is testable on any machine:

```powershell
python -m pytest -q
```

That covers the container codec, XML round-trips, the netlist parser, and
generation plus connectivity checks for the three example specs. If you add a
feature to `builder.py`, `router.py`, `check.py`, `netlist.py` or the codec,
add an offline test for it.

The four modules that need the real machine are `sim.py`, `capture.py`,
`com.py` and `parts.py` (the latter only for `fetch` / `init`). Changes there
should be described with the Multisim version you tested against and, for
screenshots, attach the image you got.

## Style

- Python 3.10+, standard library first. `pywin32` / `Pillow` / `numpy` /
  `scipy` are optional extras, so import them lazily inside the function that
  needs them.
- Comments and docstrings in English.
- Every CLI command prints JSON to stdout; nothing else goes to stdout.
- Keep the offline/real-machine split: if something can be verified from the
  file alone, it belongs in `check.py`, not `sim.py`.
- No fabricated data. Screenshots come from a real window, numbers come from a
  real simulation.

## Submitting

1. Open an issue first for anything larger than a bug fix.
2. One topic per pull request; keep the diff focused.
3. Run `python -m pytest -q` before pushing.
4. Describe how you verified the change (offline tests, and for real-machine
   code: Multisim version, design used, what you observed).
