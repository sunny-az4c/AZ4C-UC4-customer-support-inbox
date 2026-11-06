# Start here

This folder is your repository. Push it to your own GitHub repository as it is, and build your system inside `code/`.

## Rules

- Change files only inside `code/`. Everything outside it is read-only.
- Run everything from `code/` with `python -m app`. A stub is in `code/app/__main__.py`.
- Read model keys and addresses only from the four variables in `code/.env.example`. Don't hard-code a key or a provider address.
- Pin every package in `code/requirements.txt` to an exact version (`name==version`). Use Python 3.12.
- Put your own test cases in `code/tests/cases/` and their expected answers in `code/tests/expected/`, in the same format as `samples/` and `samples/expected/`. `python -m app eval --cases tests/cases` writes your score to `code/tests/score.json`.
- Never commit `.env` or any key.

## Getting started

```
python .az4c/check_setup.py   # first: checks your setup and says what to fix (Windows: py .az4c/check_setup.py)
cd code
cp .env.example .env          # then add your keys to .env
python -m app run --case ../samples/UC4-S01.json --out out/UC4-S01.json
python -m app eval --cases ../samples
```

## Your repository at the end

```
pack.json  README.md  USE_CASE.md  COMMON.md  data/  docs/  samples/  schema/   (unchanged)
code/
  README.md         how to set up and run it, your design, models used and why
  AI_USAGE.md       how you used AI coding tools
  .env.example      the settings needed, without values
  requirements.txt  every package pinned
  app/              your code, run with python -m app
  tests/            your test cases, expected answers and score.json
  docs/             your diagram, and the write-up if your role needs one
```
