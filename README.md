# tink-route

Routes a task to one specialist skill from your [Tink](https://github.com/jon-devlapaz/tink) library, verifies it with `tink mount`, and prints it, using [TypeSafe Jev](https://docs.typesafe.ai/) for the decision.

## Install

```bash
pipx install git+https://github.com/jon-devlapaz/tink-route.git   # not on PyPI
export TYPESAFE_API_KEY=...  # the routing model
```

Requires Python 3.11+ and the `tink` CLI on `PATH`.

## For agents

Add one line to `AGENTS.md`:

```
When a task needs a specialised procedure you do not already know, run: tink-route "<what you need>" and follow the output; if it exits non-zero, continue without it.
```

## Usage

```
tink-route [--skillset NAME | --anywhere] [--receipt PATH] [--inline-max N] [--json] [--pick] "<task>"
```

| Command | What it does |
| :--- | :--- |
| `tink-route "<task>"` | Route, verify the winner, print the skill (or its path). |
| `tink-route --pick "<task>"` | Decide only. Prints `Skill: <name> (confidence 0.xx)` or `No specialist skill applies to this task.` Mounts nothing, writes nothing, no receipt. |
| `tink-route --json ...` | Machine-readable output for either command. |

Exit codes: `0` delivered (`--pick`: routed), `1` no skill applies, `2` could not deliver, or a usage error. A usage error prints exactly two stderr lines.

`--pick --json` prints `{contract_version, status, task, skillset, scope, specialist_noul, winner, probability, confidence, threshold, runner_up, runner_up_probability, margin, elapsed_ms, fits, shortlist}`. `status` is `routed` or the reason nothing was chosen.

Delivery `--json` prints `{contract_version, status: delivered|no_skill|error, skill, tree_digest, chars, delivery: inline|path|none, path, confidence, content, reason, scope, hint}`. `--pick --json` adds `hint` too when nothing was chosen.

Tuning flags (`--library`, `--model`, `--threshold`, `--tri-gate/--no-tri-gate`, `--rerank/--no-rerank`, `--fits-threshold`, `--deadline`, `--inline-max`) are listed under Advanced in `tink-route --help`.

## Delivery

- Skills up to `--inline-max` characters (default 12000) print in full under a one-line header: `# tink skill: <name>  (digest, chars, confidence)`.
- Larger skills are mounted with `tink mount <name>` and the output points at `.tink/.active/<name>/SKILL.md`. Content is never truncated.
- Delivery fails open. Any problem prints one plain sentence naming the fix and exits non-zero; no skill content is printed after a refusal. The stable reason slug (`no_api_key`, `unapproved`, `digest_mismatch`, ...) is in `--json` `reason` and in receipts.

## Receipts

`--receipt PATH` (or `TINK_ROUTE_RECEIPT`) appends one JSON line per delivery, including no-skill and error outcomes: `ts, task, skillset, status, skill, tree_digest, chars, delivery, confidence, reason, scope, hint_skill`. Writes use `O_APPEND` under `flock` on the receipt file itself. Symlinked receipt paths are refused. A receipt failure prints a stderr warning and never changes stdout or the exit code.

## Scoping: the shelf

The phase decides the shelf, so the agent never says which stage it is in. `tink use <skillset>` writes a block into `./AGENTS.md`:

```
<!-- tink:rules begin skillset=<name> digest=<hex> -->  ...  <!-- tink:rules end -->
```

- With no flag, the shelf is the skillset named in that block (only the root `AGENTS.md` in the current directory, exactly one well-formed block; text outside it is ignored). Candidates are its members minus the pin's `required` list (already compiled into `AGENTS.md`).
- `--skillset NAME` overrides the block. `--anywhere` searches the whole library and ignores it; the two together are a usage error. No block and no flag also means the whole library.
- Pins are read from `.tink/skillsets/<name>[-skillset].json` in the project first, then `$TINK_HOME/skillsets`. A malformed pin, an unresolvable skillset, or a malformed/duplicate/unbalanced block exits 2 with a plain sentence; it never falls back to the whole library.
- The shelf is strict. If it yields nothing: `No specialist skill on the <name> shelf applies to this task; proceed without one.` (exit 1).
- **Hint.** One more routing call over the rest of the library. If a skill there fits: `Hint: <skill> fits but is on another shelf (<a>, <b>); it was not delivered.` (or `is not on any stage shelf`). It is never mounted or printed, and exit stays 1. A failed hint call is silently omitted. No hint without a shelf.
- `scope` (`skillset` or `library`) in `--json` and receipts records whether a shelf applied.

Removed flags: `--strict` (the shelf is always strict), `--use`, `--stage`, `--stage-only`, `--multi`, `--top-k`, `-i`, `--prune`, `--check`.

## How routing works

1. **Gate.** One question: is this a specialised workflow rather than an ordinary reply? A low score ends routing with no skill.
2. **Rank.** Jev picks the most load-bearing candidate (batched, tournament-reduced past 24 skills) or abstains.
3. **Rerank.** The top three are re-read with `SKILL.md` excerpts and per-skill fit scores; if none fits, no skill is chosen.

## Trust model

Verification is delegated to `tink mount --json --payload`: the skill must be approved (`tink library approve`), unchanged since approval (tree digest), and free of symlinks. Only verified content is printed.

## Environment

| Variable | Purpose |
| :--- | :--- |
| `TYPESAFE_API_KEY` | Routing model credential. Required. |
| `TINK_HOME` | Library root (default `~/.tink-library`); skills live in `$TINK_HOME/skills`. |
| `TINK_ROUTE_RECEIPT` | Default receipt path. |

## License

MIT
