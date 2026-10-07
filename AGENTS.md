# AGENTS.md

This repository follows the [AI-Native SDLC Playbook](https://github.com/jon-devlapaz/tink-sdlc).

## Development Guidelines

- Python 3.11+ zero-runtime dependency core.
- Tests must use Python's built-in `unittest` framework.
- Always run `PYTHONPATH=src python3 -m unittest discover -s tests -v` before committing.
- Never write unit tests after you write code.
- Highly prefer E2E tests as the sole testing mechanism. Use them to verify complex features work. At the end of E2E tests, produce a verifiable and repeatable artifact.
- If you must test a system in isolation, first write down all the ways it could fail, then write the code.
- Respect Tink invariants: inspection authority is strictly separated from mutation authority.
- Delivery writes only under `.tink/.active/` (via `tink mount`) and the optional receipt; `--pick` writes nothing.
- `tink-inject` (the context injector) writes only its log and per-session state under `INJECT_HOME`
  (default `~/.local/share/tink-inject`; log path `INJECT_LOG`), plus whatever `tink mount` writes during delivery.
  Hooks must exit 0 and stay silent on any failure: the agent is never broken or told that skills exist.

## git-golden

A repository is `git-golden` when all of the following are true:

- It is checked out on `main` with a clean working tree.
- Local `main` is even with `origin/main`.
- Open issues and pull requests are tracked separately; they do not make the checkout unclean.
- The latest `CI` run on `main` succeeded.
