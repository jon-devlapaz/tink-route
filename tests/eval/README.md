# Skill routing eval prompts

`prompts.jsonl`: 60 labelled prompts, one JSON object per line.

Schema: `id`, `prompt`, `expected` (library skill dir name or null), `category`
(clear_positive 20, paraphrase_positive 12, ambiguous 8, negative_generic_coding 10,
negative_chitchat 5, near_miss 5), `acceptable` (other defensible skills), `notes`.

## How labels were made
Written by hand from the frontmatter name and description of each skill in
`~/.tink-library/skills` only. The router was never run, so the set is independent of it.
`write-skillset-router` is a symlink and is excluded: never expected or acceptable.
29 distinct skills appear as `expected`.

## Scoring notes
- Positive/ambiguous: a hit is `expected`; `acceptable` counts as a soft hit.
- Negatives and near_miss: the correct outcome is no activation (`expected` null).

## Known ambiguities
- Overlap cluster: `teach` / `how` / `why` / `eli5`; `create-skill` / `write-skills` / `skill-cleaner`.
- `deslop` (code) vs `unslop` (prose) vs `technical-writing`.
- `arena` / `swarm` / `principle-exhaust-the-design-space` for "try several approaches".
- Always-apply style skills (`unslop` says "must always apply", `principle-*`) could
  legitimately fire on generic prompts; negatives assume they should not be routed per-prompt.
- The stack-trace prompt (p044) could plausibly match `eli5`/`bro`; it asks for a plain explanation, labelled null by design.

## Stage-open eval (`run_stage_open_eval.py`)

What a launcher does at stage open: hand the whole prior document to `tink-route --pick --anywhere`
and accept its pick or its abstention. 63 cases in `stage_open_cases.jsonl`:
32 `plain` and 21 `paraphrase` (need described without cue words), 4 `multi_need` (any listed skill
counts), 6 `none` (no specialist wanted). `--needle` buries 8 plain documents in 20k/100k characters
of unrelated text (`stage_open_filler.json`) at the start, middle and end.

    python3 tests/eval/run_stage_open_eval.py --check          # no network: schema, gold exists, no cue leaks
    TYPESAFE_API_KEY=... python3 tests/eval/run_stage_open_eval.py --needle

Artifact (overwritten): `target/eval/stage-open-eval.json` and `.md`. Headline metrics are precision
(correct among routed; abstaining is the safe outcome) and recall.

Baseline (default config, 2026-09-30): document only 55/55 correct of 55 routed, 2 abstained, 1 of 6 `none` cases
falsely routed; with a `Stage: X.` prefix 56/56, 1 abstained, the same false route at higher confidence; needle
variants 46 of 48 routed and correct, plus one wrong pick (`architect` for a buried TypeScript need at 20k, start).

Caveats: the author wrote the documents and the labels, all from skill descriptions, so numbers are optimistic
until real stage documents are added. Cue leaks are checked only for hyphenated skill ids. The library is the
reader's own: results change when it does.
