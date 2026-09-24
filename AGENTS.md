# AGENTS.md

This repository follows the [AI-Native SDLC Playbook](/Users/jondev/dev/ai-native-sdlc).

## Development Guidelines

- Python 3.11+ zero-runtime dependency core.
- Tests must use Python's built-in `unittest` framework.
- Always run `PYTHONPATH=src python3 -m unittest discover -s tests -v` before committing.
- Never write unit tests after you write code.
- Highly prefer E2E tests as the sole testing mechanism. Use them to verify complex features work. At the end of E2E tests, produce a verifiable and repeatable artifact.
- If you must test a system in isolation, first write down all the ways it could fail, then write the code.
- Respect Tink invariants: inspection authority is strictly separated from mutation authority.
- The default execution must remain read-only; `--install` is required for project mutations.
