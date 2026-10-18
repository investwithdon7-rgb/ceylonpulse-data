"""Feed health: a run goes red only when a source has been down too long.

Sources (government sites, NASA FIRMS) often drop out for a few hours. The
build scripts then keep the previous file, so the site stays up, and that is
reported as a GitHub warning. The run fails only once a feed has been failing
for longer than its `max_hours`.

State lives in data/feeds-health.json:
  {feed: {"failingSince": ISO time, "lastError": str, "maxHours": n, "lastChecked": ISO time}}
Only failing feeds are listed, and the file is rewritten only when a feed
starts or stops failing (or once a day while it keeps failing), so it adds no
hourly commits.

Use from Python (build_feeds.py) with ok()/fail()/finish(), or wrap any script:
  python scripts/health.py run FEED MAX_HOURS [--local FILE:HOURS] scripts/build_x.py [args]
A wrapped script counts as failing when it exits non-zero or prints one of the
"kept previous" messages the build scripts use when a source fails.
--local: the source refuses GitHub, and scripts/local_refresh.py uploads data/FILE
from a home PC with a `checked` time; a copy checked within HOURS counts as OK.
"""
import datetime as dt, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'data', 'feeds-health.json')
SOFT_FAIL = re.compile(r'(?i)keeping previous|kept previous')

_state = json.load(open(PATH, encoding='utf-8')) if os.path.exists(PATH) else {}
_orig = json.dumps(_state, sort_keys=True)
_seen = {}


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def ok(feed):
    _seen[feed] = True
    _state.pop(feed, None)


def fail(feed, error, max_hours):
    _seen[feed] = False
    now = _now().isoformat()
    e = _state.setdefault(feed, {'failingSince': now})
    e['maxHours'] = max_hours
    if e.get('lastError') != error[:300] or e.get('lastChecked', '')[:10] != now[:10]:
        e['lastError'], e['lastChecked'] = error[:300], now


def finish():
    """Save state, report this run's failing feeds, and exit 1 only if one is stale."""
    if json.dumps(_state, sort_keys=True) != _orig:
        if _state:
            json.dump(_state, open(PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
        elif os.path.exists(PATH):
            os.remove(PATH)
    stale = []
    for feed, good in _seen.items():
        if good:
            continue
        e = _state[feed]
        hours = (_now() - dt.datetime.fromisoformat(e['failingSince'])).total_seconds() / 3600
        msg = f"{feed}: failing for {hours:.1f} h (limit {e['maxHours']} h), previous data kept - {e['lastError']}"
        if hours >= e['maxHours']:
            stale.append(feed)
            print(f'::error title={feed} is stale::{msg}')
        else:
            print(f'::warning title={feed} temporarily unavailable::{msg}')
    sys.exit(1 if stale else 0)


def local_age(name):
    """Hours since scripts/local_refresh.py last confirmed data/<name>, or None."""
    try:
        checked = json.load(open(os.path.join(ROOT, 'data', name), encoding='utf-8'))['checked']
        return (_now() - dt.datetime.fromisoformat(checked)).total_seconds() / 3600
    except (OSError, KeyError, ValueError):
        return None


def run(feed, max_hours, cmd, local=None):
    r = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = (r.stdout or '') + (r.stderr or '')
    print(out, end='' if out.endswith('\n') or not out else '\n')
    soft = [l.strip() for l in out.splitlines() if SOFT_FAIL.search(l)]
    if (r.returncode != 0 or soft) and local:
        name, hours = local.split(':')
        age = local_age(name)
        if age is not None and age < float(hours):
            print(f'{feed}: refused from GitHub; home PC copy of {name} checked {age:.1f} h ago')
            soft, r.returncode = [], 0
    if r.returncode != 0:
        tail = [l.strip() for l in out.splitlines() if l.strip()][-1:] or ['']
        fail(feed, f'exit {r.returncode}: {tail[0]}', max_hours)
    elif soft:
        fail(feed, '; '.join(soft)[:300], max_hours)
    else:
        ok(feed)
    finish()


if __name__ == '__main__':
    if len(sys.argv) < 5 or sys.argv[1] != 'run':
        sys.exit('usage: health.py run FEED MAX_HOURS script.py [args]')
    hours = float(sys.argv[3])
    args, local = sys.argv[4:], None
    if args[0] == '--local':
        local, args = args[1], args[2:]
    run(sys.argv[2], int(hours) if hours.is_integer() else hours, args, local)
