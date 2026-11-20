# Contributing to Pycrograph

Pycrograph is maintained by Etienne Boulter and Chloé C. Féral (IRCAN). Bug reports, feature
suggestions and pull requests are welcome; for anything large, please open an
issue first.

## Development setup

Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/) ≥ 0.13.5 (CI pins 0.13.5):

```bash
git clone https://github.com/TheBrickalista/Pycrograph.git
cd Pycrograph
uv sync --extra dev
uv run python -m pycrograph
```

If you change dependencies, regenerate `uv.lock` and the hashed lock in `ci/`
(command in the header of `ci/requirements-min.in`). Floors in
`pyproject.toml` must equal the `==` pins in `ci/requirements-min.in`. Then
regenerate the third-party notices from the locked environment
(`uv sync --locked`, then `python scripts/make_third_party_notices.py`). If you
edit `LICENSE` or `CITATION.cff`, copy them to `pycrograph/resources/LICENSE.txt`
and `pycrograph/resources/CITATION.cff`; tests check that the copies are
identical.

## Tests

```bash
uv run pytest
uv run pyflakes pycrograph tests launcher.py
```

Qt tests run offscreen (`QT_QPA_PLATFORM=offscreen` is set by `tests/conftest.py`).
Test images are generated on the fly (`tests/synth.py`, including a byte-level
MetaMorph STK writer); no large binaries in the repository.

## Rules for changes

- Never modify raw pixel data; everything visible is a display setting.
- Every model change goes through the operation log (one entry per gesture).
- Code copied from PysternBlot keeps its `SHARED-CORE candidate` header.
- Branches: work on `dev`, pull request into `main`.
