# AGENTS.md

This repository follows the [AI-Native SDLC Playbook](/Users/jondev/dev/ai-native-sdlc).

## Development Guidelines

- Python 3.11+ zero-runtime dependency core.
- Tests must use Python's built-in `unittest` framework.
- Always run `PYTHONPATH=src python3 -m unittest discover -s tests -v` before committing.
- Respect Tink invariants: inspection authority is strictly separated from mutation authority.
- The default execution must remain read-only; `--install` is required for project mutations.
