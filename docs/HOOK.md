# tink-hook

`tink-hook claude-code` is a Claude Code hook. On each prompt it asks `tink-route` whether one skill
applies. If one does, `tink mount --json --payload` checks the skill and returns its full text, and the
hook sends that text to Claude with the prompt. The agent never sees a skill catalog. A skill is
delivered whole or not at all: the hook never truncates a skill and never falls back to a summary.

## Setup

```sh
tink library approve --all            # review first; only approved tree digests are ever injected
cd /path/to/repo && tink-hook enable  # per-project opt-in, stored in $TINK_HOME/hook.json
tink-hook print-settings              # prints the snippet; merge it into ~/.claude/settings.json yourself
tink-hook status                      # opt-in and kill-switch state for the current project
```

`print-settings` only prints. It never writes Claude settings.

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "tink-hook claude-code", "timeout": 10}]}],
    "SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": "tink-hook claude-code", "timeout": 10}]}]
  }
}
```

## Flow

1. Stdin is parsed. If it is malformed, or `session_id`, `cwd` (an existing absolute directory) or
   `hook_event_name` is missing, the hook prints nothing.
2. `SessionStart` with `source` `compact` or `clear` clears this session's dedupe state and prints
   nothing. Any other event prints nothing.
3. The hook turns itself off, and never starts the router, if any of these is true:
   - `TINK_HOOK` is `off`, `0`, `false`, `no`, `disable` or `disabled`.
   - `<project>/.tink/hook.off` exists.
   - `$TINK_HOME/hook.json` is missing, invalid or does not list the project.

   Here, `<project>` is the git top-level of `cwd` (or `cwd` itself), fully resolved. Files in the
   repo can only turn the hook off. They can never turn it on, and they can never set `router_cmd`.
4. Prompts that are empty, start with `/` or are shorter than 12 characters are skipped.
5. Secrets are scrubbed from the prompt (see Privacy).
6. The router runs as a child process in its own process group. At `TINK_HOOK_DEADLINE` (default 2.0s)
   the whole group is killed. The default command is
   `python -m tink_route.cli --json --deadline <D> -- <task>`. `--deadline` sets the per-API-call
   timeout and turns off retries. The user-scope `router_cmd` (an argv list; the task is appended)
   can replace this command and exists for tests. The hook prints nothing if any of these is true:
   - The router exits non-zero.
   - Its output is not JSON.
   - `contract_version` is not 1.
   - `status` is not `routed` or `multi_routed`.
7. Only the top-ranked skill (`winner`) is used. The hook runs `tink mount <winner> --json --payload`
   with its own deadline, `TINK_HOOK_MOUNT_DEADLINE` (default 1.5s), and checks the result:
   - A refusal that includes a `code` produces only the notice `tink: skill X not applied (<code>)`.
   - A timeout, unparseable output, a skill-name mismatch, `payload.chars != len(content)`, a
     `truncated` payload or a malformed `tree_digest` produces no output.
8. Dedupe: state lives in `<project>/.tink/.active/.sessions/<id>`. The file is written atomically,
   and the directory has its own `.gitignore` containing `*`. If `<id>` is not
   `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`, the file is named `h-<sha256 prefix>` instead. A
   `(skill, tree_digest)` pair already injected in this session is not injected again, and a
   repeated refusal notice is not shown again. Approving a changed skill (new digest) injects it
   again.
9. Output is written only if the whole `additionalContext` fits within `TINK_HOOK_MAX_CHARS` (default
   200000):

   ```json
   {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<framing line>\n<tink-skill name=\"X\" digest=\"sha256:...\">\n<payload.content>\n</tink-skill>"},
    "systemMessage": "tink: applied skill X (disable: TINK_HOOK=off)"}
   ```

   If it does not fit, the skill is not injected. Only a notice naming the size and the limit is
   shown.

The process exits 0 on every path, including exceptions, no subcommand and unknown subcommands.

## Router contract (additive)

`tink-route --json` now includes `"contract_version": 1` and `"action"`. For routed results `action` is
`{"type": "inject" | "mount_and_inject", "mount_command": "tink mount <winner> --json --payload"}`.
The type is `mount_and_inject` when the winner's directory has `scripts/`. For every other result it is
`{"type": "none", "mount_command": null}`. Router output never contains skill content. Without `-i`,
the router writes no files. The e2e compares the project tree, including `.tink/`, and `$TINK_HOME`
before and after a live route.

## Privacy

The prompt is sent to the router's API only for opted-in projects. Before sending, a best-effort
heuristic redacts the following. This is not a guarantee.
- PEM blocks
- `sk-…` tokens
- `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` tokens
- `AKIA`/`ASIA` keys
- `xox?-` Slack tokens
- `Bearer …`
- `password=`/`token:`/`api_key=`-style assignments (the value is redacted)
- Hex runs of 32 or more characters
- Base64-like blobs of 40 or more characters that mix digits, upper case and lower case

`TINK_HOME` is read from the hook's environment, which Claude Code inherits from the user's shell.

## Claude Code hook schema: verified vs assumed

Verified against code.claude.com/docs/en/hooks on 2026-09-28:
- UserPromptSubmit stdin has `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode`,
  `hook_event_name` and `prompt`. The docs say: "The `prompt` field contains the text that the user
  submitted."
- UserPromptSubmit output is `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}`.
  The hook cannot replace the prompt. It can only add `additionalContext` next to it.
- On exit 0, plain stdout is also added as context. The hook prints JSON only, or nothing.
- Top-level `systemMessage` is a "Warning message shown to the user". It can be sent together with
  `hookSpecificOutput`.
- On UserPromptSubmit, exit 2 "Blocks prompt processing and erases the prompt". Any other non-zero
  code is a non-blocking error. `tink-hook` always exits 0.
- SessionStart stdin has `source`: `startup`, `resume`, `clear`, `compact` or `fork`. The `compact`
  matcher means "Auto or manual compaction". UserPromptSubmit has no matcher support and always fires.
- `timeout` is in seconds. For `command` hooks the default is 600, lowered to 30 on UserPromptSubmit.
  The snippet sets 10.
- The docs say: "A hook's `additionalContext`, `systemMessage`, and `initialUserMessage` strings, and
  its plain stdout, are capped at 10,000 characters". Over that limit, Claude Code saves the output
  to a file and passes the file path plus a preview of up to 2,000 characters.

Assumed or not yet measured:
- The documented 10,000-character cap conflicts with the default guard of 200000. A skill whose
  context is between 10,000 and 200,000 characters would reach the model only as a file path plus a
  2,000-character preview, which is effectively progressive disclosure. Until the harness limit is
  measured (P2), set `TINK_HOOK_MAX_CHARS` to about 10000 to keep "whole or nothing" true on Claude
  Code.
- Whether additionalContext survives compaction is not documented. The hook assumes it does not:
  compaction clears the dedupe state so the skill can be injected again.
- How `systemMessage` is rendered, for example its styling or whether it is shown in non-interactive
  modes, has not been checked in a live Claude Code session.
