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
