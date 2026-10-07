#!/usr/bin/env python3
"""inject: put the right expertise into an agent's context at the right time. Harness-agnostic, never blocks.

The agent never learns that skills exist. It just finds guidance in its context.

A *need* is a short line describing expertise, e.g. "find the root cause of a bug". Each need is routed through
tink-route: first the project's own `.agents/skills/`, then the tink library (approved skills only). Below
INJECT_MIN_CONF it abstains, because a wrong skill does more harm than a missing one.

Three kinds of need:
  planned    from the planner: `needs:` lines in the prompt or plan, or to-do items. These define the session's plan.
  first-touch  the agent reads or edits a file class (UI, tests, docs) for the first time. Allowed only for a skill the
             plan also asked for, or in a session with no plan. A planned skill is repeated here once, at the point of
             use, because launch-time text drifts into the middle of a long context.
  reactive   a test just failed. Always allowed.
At most INJECT_MAX_SKILLS skills per session (reactive ones don't count). Each text is cut at a section boundary near
INJECT_MAX_CHARS, keeping the opening rules.

Entry points (console command `tink-inject`):
  tink-inject needs "line" ["line" ...]   guidance for these needs, as plain text for any prompt
  tink-inject plan PLAN.md                guidance for every `needs:` line in a plan file
  tink-inject hook                        one harness event (Claude Code / Codex hook JSON) on stdin -> hook output
  tink-inject eval CASES.json             routed vs delivered recall and abstention on [{"need", "expect"}] cases
  tink-inject doctor [--json] [--strict]  check key, tink, library, approvals and a live canary delivery
  tink-inject status [--since TS]         delivered / abstained / degraded counts in the log; exit 1 if degraded
  tink-inject lint                        skills that are too long or describe a topic instead of when to apply

Env: INJECT=off disables it. INJECT_LOG sets the JSONL log (default ~/.local/share/tink-inject/log.jsonl).
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from contextlib import contextmanager
from pathlib import Path

PLAN_TTL = 2 * 3600  # a launch plan seeds hook sessions in the same checkout for this long


# Settings are read when used, never at import, so a malformed value can't break the agent before the fail-open boundary.
def setting(name, default, cast):
    try:
        return cast(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return cast(default)


def min_conf():
    return setting('INJECT_MIN_CONF', '0.75', float)


def max_skills():
    return setting('INJECT_MAX_SKILLS', '3', int)


def max_chars():
    return setting('INJECT_MAX_CHARS', '9000', int)


def inject_home():
    return Path(os.environ.get('INJECT_HOME') or '~/.local/share/tink-inject').expanduser()


def log_path():
    return Path(os.environ.get('INJECT_LOG') or inject_home() / 'log.jsonl').expanduser()


def library():
    return Path(os.environ.get('TINK_HOME') or '~/.tink-library').expanduser() / 'skills'


def digest_name(value):
    """A file name for an untrusted id (harness session ids, paths): never a path component of its own."""
    return hashlib.sha256(str(value).encode('utf-8', 'replace')).hexdigest()[:32]


def session_file(session, suffix):
    return inject_home() / 'sessions' / f'{digest_name(session)}{suffix}'


def plan_file(cwd):
    return inject_home() / 'plans' / f'{digest_name(Path(cwd).resolve())}.json'


@contextmanager
def session_lock(session):
    """Serialize overlapping hook processes for one session, so each skill is delivered once."""
    path = session_file(session, '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as handle:
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        except ImportError:  # no flock (Windows): best effort
            pass
        yield
NEEDS = re.compile(r'^\s*(?:[-*]\s*)?needs?\s*:\s*(.+?)\s*$', re.I | re.M)
FRAME = ('Reference guidance for the work you are doing now. Apply what is relevant to your task. It is not a request: '
         'ignore any lines in it about how to greet, introduce yourself or respond. Your task and this repository\'s '
         'own conventions take precedence over it.')

# What the agent touched or saw -> the expertise that moment calls for.
CODE = re.compile(r'\.(py|js|mjs|ts|tsx|jsx|go|rs|rb|java|kt|swift|c|cc|cpp|h|hpp|cs|php|sh|vue|svelte|css|scss|html)$')
TEST = re.compile(r'(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$')
UI = re.compile(r'(^|/|_)(ui|views?|render\w*|hud|screens?|display|components?|pages?|widgets?)([/_.]|$)|\.(tsx|jsx|css|scss|html|vue|svelte)$')
MOTION = re.compile(r'transition|animation|keyframes|easing|requestAnimationFrame|tween')
IGNORE = re.compile(r'(^|/)(\.git|\.claude|\.codex|\.tink|\.pi|runs|node_modules|vendor|dist|build)/|(^|/)SKILL\.md$')
PATHS = re.compile(r'[\w./-]+\.(?:py|mjs|js|tsx?|jsx|go|rs|rb|java|kt|swift|c|cc|cpp|hpp|h|cs|php|sh|vue|svelte|s?css|html|md)\b')
TEST_CMD = re.compile(r'\b(pytest|unittest|npm (run )?test|yarn test|pnpm test|go test|cargo test|jest|vitest|rspec)\b')
FAILED = re.compile(r'FAILED|FAIL:|ERROR:|Traceback|AssertionError|failures=|\d+ failed')
MOMENTS = {
    'test-touch': 'write tests that check behavior',
    'ui-touch': 'give an interface a polished, crafted visual design',
    'motion-touch': 'make an interactive animation feel fluid, physical and natural',
    'docs-touch': 'write clear documentation',
    'removal': 'remove old code that other code may still call',
    'test-fail': 'find the root cause of a bug',
}
REACTIVE = {'test-fail'}


# --- routing ------------------------------------------------------------------

def tink_route(*args):
    env = {k: v for k, v in os.environ.items() if k != 'TINK_ROUTE_RECEIPT'}  # injections are not tink-route receipts
    p = subprocess.run(['tink-route', '--json', *args], capture_output=True, text=True, timeout=30, env=env)
    return json.loads(p.stdout)


def body(content):
    """Guidance text without front matter or title, cut at a section boundary near INJECT_MAX_CHARS."""
    text = re.sub(r'\A---\n.*?\n---\n', '', content, flags=re.S).strip()
    text = re.sub(r'\A#[^\n]*\n+', '', text)
    limit = max_chars()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind('\n#'), head.rfind('\n\n'))
    return head[:cut if cut > limit // 2 else limit].rstrip() + '\n\n(Shortened to its opening sections.)'


def budget():
    return setting('INJECT_BUDGET', '90', float)


def _route_args(need, library_path, pick, approved_only, deadline):
    from types import SimpleNamespace
    from .adapters.client import DEFAULT_MODEL
    from .core.constants import FITS_THRESHOLD
    # The router gates on `probability` at INJECT_MIN_CONF. Inline delivery keeps content in memory; nothing is mounted.
    return SimpleNamespace(task=need, skillset=None, anywhere=True, pick=pick, json=True, receipt=None,
                           library=library_path, model=DEFAULT_MODEL, threshold=min_conf(), tri_gate=True, rerank=True,
                           fits_threshold=FITS_THRESHOLD, deadline=deadline, inline_max=10 ** 9,
                           approved_only=approved_only)


def route(need, cwd, deadline):
    """Outcomes for one need, project skills first, then the library: each is a dict with `kind`
    deliver (with `text`), abstain (the router found nothing good enough; healthy) or degraded (anything broke).
    In process: no subprocess, PATH lookup or output parsing that could fail silently."""
    from . import flow
    from .adapters.executor import DefaultSubprocessExecutor
    from .core.constants import get_default_library_path
    outcomes = []
    house = Path(cwd) / '.agents' / 'skills'
    if any(house.glob('*/SKILL.md')):
        # Project skills are committed with the repo and reviewed there, so they need no library approval.
        d = flow.Decision(None)
        try:
            flow.decide(d, need, _route_args(need, house, True, False, deadline), Path(cwd), None)
            if d.routed and (house / str(d.skill) / 'SKILL.md').is_file():
                outcomes.append({'source': 'project', 'kind': 'deliver', 'skill': d.skill,
                                 'probability': d.result.probability, 'text': body((house / d.skill / 'SKILL.md').read_text())})
            else:
                outcomes.append({'source': 'project', 'kind': 'abstain', 'reason': d.result.status if d.result else None})
        except flow.FlowError as e:
            outcomes.append({'source': 'project', 'kind': 'degraded', 'reason': e.reason})
    args = _route_args(need, get_default_library_path(), False, True, deadline)
    _code, rec, _text, content = flow.deliver_record(args, executor=DefaultSubprocessExecutor())
    if rec['status'] == 'delivered' and content:
        outcomes.append({'source': 'library', 'kind': 'deliver', 'skill': rec['skill'],
                         'probability': rec['probability'], 'text': body(content)})
    elif rec['status'] == 'no_skill':
        outcomes.append({'source': 'library', 'kind': 'abstain', 'reason': rec['reason'], 'probability': rec['probability']})
    else:
        outcomes.append({'source': 'library', 'kind': 'degraded', 'reason': rec['reason'], 'skill': rec['skill']})
    return outcomes


def guidance(needs, cwd, session, kind='planned', why=None, publish=False, started=None):
    """Route needs of one kind, apply the session's gates and return (guidance text, degraded reasons).

    `publish` (launch-time `needs`/`plan`) records the plan for the checkout, so the agent's hook session, which
    has its own session id, starts knowing what was planned and already given. Degraded reasons are for the
    operator only; they never enter the guidance text.
    """
    started = time.monotonic() if started is None else started
    state_file = session_file(session, '.json')
    if state_file.exists():
        state = json.loads(state_file.read_text())
    else:
        state = {}
        try:
            plan = plan_file(cwd)
            if not publish and time.time() - plan.stat().st_mtime < PLAN_TTL:
                seeded = json.loads(plan.read_text())
                state = {'given': list(seeded.get('given', [])), 'planned': list(seeded.get('planned', []))}
        except (OSError, ValueError):
            pass
    given = state.setdefault('given', [])          # skills delivered so far
    planned = state.setdefault('planned', [])      # skills the plan asked for
    repeated = state.setdefault('repeated', [])    # planned skills already repeated at their point of use
    blocks, degraded = [], []
    for need in dict.fromkeys(n.strip() for n in needs if n.strip()):
        remaining = budget() - (time.monotonic() - started)
        if remaining <= 0:  # stop before the harness kills the hook and nothing gets logged
            log({'need': need, 'kind': kind, 'status': 'skipped', 'reason': 'budget_exhausted', 'degraded': True,
                 'session': session})
            degraded.append('budget_exhausted')
            continue
        try:
            outcomes = route(need, cwd, deadline=max(1.0, min(30.0, remaining)))
        except Exception as error:  # fail open; log the type only, a message can carry secrets
            outcomes = [{'source': 'library', 'kind': 'degraded', 'reason': type(error).__name__}]
        for g in outcomes:
            row = {'need': need, 'kind': kind, 'source': g['source'], 'skill': g.get('skill'),
                   'probability': g.get('probability'), 'reason': g.get('reason'), 'session': session}
            if g['kind'] != 'deliver':
                is_degraded = g['kind'] == 'degraded'
                log(dict(row, status=g['kind'], **({'degraded': True} if is_degraded else {})))
                if is_degraded:
                    degraded.append(g['reason'])
                continue
            skill = g['skill']
            counted = len([s for s in given if s not in state.get('reactive', [])])
            if kind == 'planned':
                planned.append(skill) if skill not in planned else None
                verdict = 'already given' if skill in given else 'over cap' if counted >= max_skills() else 'deliver'
            elif kind == 'first-touch':
                if planned and skill not in planned:
                    verdict = 'not in plan'
                elif skill in given:
                    verdict = 'repeat at point of use' if skill in planned and skill not in repeated else 'already given'
                else:
                    verdict = 'over cap' if counted >= max_skills() else 'deliver'
            else:  # reactive
                verdict = 'already given' if skill in given else 'deliver'
            log(dict(row, status=verdict))
            if verdict in ('deliver', 'repeat at point of use'):
                if verdict == 'deliver':
                    given.append(skill)
                    if kind == 'reactive':
                        state.setdefault('reactive', []).append(skill)
                else:
                    repeated.append(skill)
                # No skill name or source here: the agent must never learn that skills exist (the log keeps both).
                blocks.append(f'## Guidance: {need}\n'
                              f'_Why now: {why or "named in the plan"}._\n\n{g["text"]}')
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state))
    if publish:
        plan = plan_file(cwd)
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(json.dumps({'given': state.get('given', []), 'planned': state.get('planned', [])}))
    text = (FRAME + '\n\n' + '\n\n'.join(blocks)).strip() if blocks else ''
    return text, list(dict.fromkeys(degraded))


def notice(reasons):
    return (f'tink-inject: guidance degraded ({", ".join(reasons)}); the agent continues without it. '
            'Details are in the tink-inject log.')


def log(row):
    """Best effort: a log that can't be written must never break the agent."""
    try:
        _write_log(row)
    except Exception:
        pass


def _write_log(row):
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as f:
        f.write(json.dumps(dict(row, ts=time.strftime('%Y-%m-%dT%H:%M:%S'))) + '\n')


# --- in-flight events -----------------------------------------------------------

def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=10).stdout


def tree_changes(cwd, seen):
    """Files whose content changed since the last event, whichever tool changed them."""
    edited, deleted = [], []
    for line in git(cwd, 'status', '--porcelain', '--untracked-files=all').splitlines():
        code, path = line[:2], line[3:].split(' -> ')[-1].strip('"')
        if 'D' in code:
            if seen.get(path) != 'deleted':
                seen[path] = 'deleted'
                deleted.append(path)
            continue
        try:
            digest = hashlib.sha256((Path(cwd) / path).read_bytes()).hexdigest()
        except OSError:
            continue
        if seen.get(path) != digest:
            seen[path] = digest
            added = (Path(cwd) / path).read_text(errors='replace') if code == '??' else git(cwd, 'diff', 'HEAD', '--', path)
            edited.append((path, added))
    return edited, deleted


def classify(rel, added=''):
    """File-class moments for one touched path."""
    if not rel or IGNORE.search(rel):
        return []
    if rel.endswith('.md'):
        return ['docs-touch']
    if not CODE.search(rel):
        return []
    if TEST.search(rel):
        return ['test-touch']
    found = ['motion-touch'] if MOTION.search(added) else []
    return found + (['ui-touch'] if UI.search(rel) else [])


def moments(event, seen):
    """Moment ids for one event: first touches (reads and edits) and reactive signals."""
    cwd, tool = event.get('cwd') or '.', event.get('tool_name')
    i = event.get('tool_input') or {}
    rel = lambda p: os.path.relpath(p, cwd) if os.path.isabs(p) else p
    found = []
    if tool in ('Read', 'read', 'view'):
        found += classify(rel(i.get('file_path') or i.get('path') or ''))
    if tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'write', 'edit'):
        # Classify from the tool input: on a session's first event the tree baseline is taken after the write.
        added = i.get('content') or i.get('new_string') or '\n'.join(e.get('new_string') or e.get('newText') or ''
                                                                       for e in i.get('edits') or [])
        found += classify(rel(i.get('file_path') or i.get('path') or ''), added)
    if tool == 'Bash':
        for path in PATHS.findall(i.get('command', '')):
            found += classify(rel(path))
    in_git = bool(git(cwd, 'rev-parse', '--git-dir'))
    if in_git and not seen.pop('_baselined', None) and not seen:
        tree_changes(cwd, seen)  # first event: files already changed before the session are not touches
    seen['_baselined'] = True
    edited, deleted = tree_changes(cwd, seen) if in_git else ([], [])
    if any(CODE.search(p) and not IGNORE.search(p) for p in deleted):
        found.append('removal')
    for path, added in edited:
        found += classify(path, added)
    if tool == 'Bash':
        out = str(event.get('error') or '') + json.dumps(event.get('tool_response') or '')
        if TEST_CMD.search(i.get('command', '')) and (event.get('hook_event_name') == 'PostToolUseFailure' or FAILED.search(out)):
            found.append('test-fail')
    return list(dict.fromkeys(found))


def hook():
    """One harness event in, hook output out. Only ever adds context."""
    event = json.load(sys.stdin)
    name, session, cwd = event.get('hook_event_name'), event.get('session_id', 'unknown'), event.get('cwd') or '.'
    with session_lock(session):
        _hook(event, name, session, cwd)


def _hook(event, name, session, cwd):
    started = time.monotonic()
    seen_file = session_file(session, '.seen.json')
    seen = json.loads(seen_file.read_text()) if seen_file.exists() else {'fired': []}
    results = []
    if name == 'UserPromptSubmit':
        results.append(guidance(NEEDS.findall(event.get('prompt', '')), cwd, session, 'planned', started=started))
    elif event.get('tool_name') == 'TodoWrite':
        todos = (event.get('tool_input') or {}).get('todos', [])
        results.append(guidance([t.get('content', '') for t in todos], cwd, session, 'planned', started=started))
    else:
        for m in moments(event, seen.setdefault('files', {})):
            if m in seen['fired']:
                continue
            seen['fired'].append(m)
            kind = 'reactive' if m in REACTIVE else 'first-touch'
            why = 'a test just failed' if m == 'test-fail' else f'first {m.split("-")[0]} work in this session'
            results.append(guidance([MOMENTS[m]], cwd, session, kind, why, started=started))
    texts = [t for t, _ in results if t]
    text = '\n\n'.join([texts[0]] + [t.replace(FRAME + '\n\n', '', 1) for t in texts[1:]]) if texts else ''
    # Degradation goes to the operator, once per session per reason, and never into the agent's context.
    notified = seen.setdefault('notified', [])
    fresh = [r for _, reasons in results for r in reasons if r not in notified]
    fresh = list(dict.fromkeys(fresh))
    notified.extend(fresh)
    seen_file.parent.mkdir(parents=True, exist_ok=True)
    seen_file.write_text(json.dumps(seen))
    out = {}
    if text:
        out['hookSpecificOutput'] = {'hookEventName': name, 'additionalContext': text}
    if fresh:
        out['systemMessage'] = notice(fresh)
        sys.stderr.write(notice(fresh) + '\n')
    if out:
        print(json.dumps(out))


# --- measurement ------------------------------------------------------------------

def evaluate(cases_file):
    """Score routing as delivery sees it: for each case, the whole-library pick ("routed") beside what approved-only
    routing and tink actually hand out ("delivered"). A degraded setup gets no score (exit 2)."""
    from . import flow
    from .core.constants import get_default_library_path
    cases = json.loads(Path(cases_file).read_text())
    rows, degraded = [], []
    with tempfile.TemporaryDirectory() as tmp:  # tink mount may write under the cwd; keep it out of any repo
        previous = os.getcwd()
        os.chdir(tmp)
        try:
            for case in cases:
                need = case['need']
                d = flow.Decision(None, False)
                try:
                    flow.decide(d, need, _route_args(need, get_default_library_path(), True, False, 30), Path(tmp), None)
                    routed = d.skill if d.routed else None
                except flow.FlowError as e:
                    routed = None
                    degraded.append(e.reason)
                outcome = [o for o in route(need, tmp, 30) if o['source'] == 'library'][0]
                if outcome['kind'] == 'degraded':
                    degraded.append(outcome['reason'])
                delivered = outcome.get('skill') if outcome['kind'] == 'deliver' else None
                rows.append((case, routed, delivered, outcome.get('reason') or outcome['kind']))
        finally:
            os.chdir(previous)
    if degraded:
        print(f"degraded ({', '.join(dict.fromkeys(degraded))}): no score; run `tink-inject doctor`, fix it, then rerun")
        return 2
    accept = lambda case: {case.get('expect'), *case.get('also', [])} - {None}
    positives = [r for r in rows if r[0].get('expect')]
    negatives = [r for r in rows if not r[0].get('expect')]
    for case, routed, delivered, note in rows:
        good = delivered in accept(case) if case.get('expect') else delivered is None
        print(f"{'ok ' if good else 'BAD'} routed {str(routed):38} delivered {str(delivered):38} {note:16} {case['need']}")
    wrong = sum(1 for c, _, d, _ in positives if d and d not in accept(c)) + sum(1 for _, _, d, _ in negatives if d)
    stuck = sorted({r for _, r, d, _ in rows if r and r != d})
    print(f"\nthreshold {min_conf()}: delivered recall {sum(1 for c, _, d, _ in positives if d in accept(c))}/{len(positives)}, "
          f"routed recall {sum(1 for c, r, _, _ in positives if r in accept(c))}/{len(positives)}, "
          f"correct abstention {sum(1 for _, _, d, _ in negatives if d is None)}/{len(negatives)}, wrong skill {wrong}")
    print(f"routed but not deliverable: {', '.join(stuck) or 'none'}")
    return 0


def doctor(argv):
    """Check every part injection depends on. Exit 1 on any FAIL. The key itself is never printed."""
    from . import flow
    from .core.constants import get_default_library_path, get_default_tink_home
    from .core.credentials import KeyFileInsecure, resolve_api_key
    from .metadata import load_approved_skills
    strict, as_json = '--strict' in argv, '--json' in argv
    need = argv[argv.index('--need') + 1] if '--need' in argv[:-1] else 'find the root cause of a bug'
    checks = []

    def add(name, ok, detail, level='FAIL'):
        checks.append({'check': name, 'status': 'PASS' if ok else level, 'detail': detail})

    try:
        key, source = resolve_api_key()
        add('key', bool(key), f'from {source}' if key else 'no TypeSafe API key found (see the tink-route README)')
    except KeyFileInsecure:
        add('key', False, 'the key file must be yours and readable only by you (chmod 600)')
    add('tink', bool(shutil.which('tink')), 'on PATH' if shutil.which('tink') else 'the tink CLI is not on PATH')
    library_path = get_default_library_path()
    add('library', library_path.is_dir() and any(library_path.glob('*/SKILL.md')), str(library_path))
    try:
        count = len(load_approved_skills(get_default_tink_home()))
        add('approvals', count > 0, f'{count} approved' if count else 'none approved (tink library approve <name>)')
    except Exception:
        add('approvals', False, 'the approvals file is missing or unreadable')
    with tempfile.TemporaryDirectory() as tmp:
        d = flow.Decision(None, True)
        try:
            flow.decide(d, need, _route_args(need, library_path, True, True, 30), Path(tmp), None)
            add('canary routes', d.routed, d.skill if d.routed else f'no skill ({d.result.status if d.result else "none"})')
        except flow.FlowError as e:
            add('canary routes', False, e.reason)
        except Exception as e:
            add('canary routes', False, type(e).__name__)
        previous = os.getcwd()
        os.chdir(tmp)
        try:
            outcome = [o for o in route(need, tmp, 30) if o['source'] == 'library'][0]
            add('canary delivers', outcome['kind'] == 'deliver',
                outcome.get('skill') if outcome['kind'] == 'deliver' else outcome.get('reason') or outcome['kind'])
        except Exception as e:
            add('canary delivers', False, type(e).__name__)
        finally:
            os.chdir(previous)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        open(path, 'a').close()
        add('log writable', True, str(path))
    except Exception as e:
        add('log writable', False, type(e).__name__)
    off = os.environ.get('INJECT') == 'off'
    add('injection enabled', not off, 'INJECT=off' if off else 'on', 'FAIL' if strict else 'WARN')
    ok = all(c['status'] != 'FAIL' for c in checks)
    if as_json:
        print(json.dumps({'ok': ok, 'checks': checks}, indent=2))
    else:
        print('\n'.join(f"{c['status']} {c['check']}: {c['detail']}" for c in checks))
    return 0 if ok else 1


def status(argv):
    """Counts of delivered, abstained and degraded outcomes in the log (since --since). Exit 1 if any degraded."""
    since = argv[argv.index('--since') + 1] if '--since' in argv[:-1] else ''
    path = Path(argv[argv.index('--log') + 1]) if '--log' in argv[:-1] else log_path()
    counts, reasons = {'delivered': 0, 'abstained': 0, 'degraded': 0}, []
    for line in path.read_text().splitlines() if path.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('ts', '') < since:
            continue
        if row.get('degraded'):
            counts['degraded'] += 1
            reasons.append(str(row.get('reason')))
        elif row.get('status') in ('deliver', 'repeat at point of use'):
            counts['delivered'] += 1
        elif row.get('status') == 'abstain':
            counts['abstained'] += 1
    detail = f" ({', '.join(dict.fromkeys(reasons))})" if reasons else ''
    print(f"delivered {counts['delivered']} · abstained {counts['abstained']} · degraded {counts['degraded']}{detail}")
    return 1 if counts['degraded'] else 0


def lint():
    """Skills that will be shortened on injection, or whose description names a topic instead of a moment."""
    for root in (Path.cwd() / '.agents' / 'skills', library()):
        for f in sorted(root.glob('*/SKILL.md')):
            text = f.read_text()
            m = re.search(r'^description:\s*["\']?(.*)', text, re.M)
            desc = (m.group(1) if m else '').strip()
            notes = []
            if len(body(text)) < len(re.sub(r'\A---\n.*?\n---\n', '', text, flags=re.S).strip()) - 200:
                notes.append(f'long ({len(text)} chars): shortened to its opening sections on injection')
            if not re.search(r'\b(apply when|apply to|use (only )?(when|for|if|after|before)|when (you|the|asked|a|an))\b', desc, re.I):
                notes.append('description says what it is, not when to apply it; add "Apply when ..." so needs route to it')
            if notes:
                print(f'{f.parent.name}: ' + '; '.join(notes))


def main(argv):
    if not argv:
        return 0
    command = argv[0]
    if command in ('hook', 'needs', 'plan') and os.environ.get('INJECT') == 'off':
        return 0
    cwd, session = os.getcwd(), os.environ.get('INJECT_SESSION', f'cli-{os.getppid()}')
    if command in ('needs', 'plan'):
        needs = argv[1:] if command == 'needs' else NEEDS.findall(Path(argv[1]).read_text())
        with session_lock(session):
            text, degraded = guidance(needs, cwd, session, 'planned', publish=True)
        if text:
            print(text)
        if degraded:
            sys.stderr.write(notice(degraded) + '\n')
        return 0
    if command == 'hook':
        hook()
        return 0
    if command == 'eval':
        return evaluate(argv[1])
    if command == 'doctor':
        return doctor(argv[1:])
    if command == 'status':
        return status(argv[1:])
    if command == 'lint':
        lint()
    return 0


def cli():
    """Console entry point (`tink-inject`). Fails open: a hook never breaks the agent that called it.
    eval, doctor and status return their exit codes (2 or 1 mean a broken setup or degraded injection)."""
    code = 0
    try:
        code = main(sys.argv[1:]) or 0
    except Exception as error:  # the type only: an error message can carry secrets
        log({'status': 'error', 'reason': type(error).__name__, 'degraded': True})
    sys.exit(code)


if __name__ == '__main__':
    cli()
