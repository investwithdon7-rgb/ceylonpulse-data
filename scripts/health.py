"""Feed health: a run goes red only for a fault in our own scripts.

Sources (government sites, NASA FIRMS) drop out, sometimes for weeks, and nothing
here can fix that. The build scripts then keep the previous file, so the site
stays up, and the run reports a GitHub warning; past the feed's `max_hours` the
warning says STALE and the feed is listed in the run summary, but the run stays
green. A run fails only when a script crashes (a Python traceback that is not a
network error) for longer than `max_hours`.
Which feeds are failing right now: the files in data/health/.

State lives in data/health/<feed>.json, one file per failing feed:
  {"feed": name, "failingSince": ISO time, "lastError": str, "maxHours": n, "lastChecked": ISO time}
A file exists only while its feed is failing and is rewritten only when the
error changes (or once a day), so it adds no hourly commits. One file per feed
means two workflows committing at the same time never overwrite each
other's state.

Use from Python (build_feeds.py) with ok()/fail()/finish(), or wrap any script:
  python scripts/health.py run FEED MAX_HOURS [--local FILE:HOURS] scripts/build_x.py [args]
A wrapped script counts as failing when it exits non-zero or prints one of the
"kept previous" messages the build scripts use when a source fails.
--local: the source refuses GitHub, and scripts/local_refresh.py uploads data/FILE
from a home PC with a `checked` time; while that copy is less than HOURS old the
script is not run at all (it counts as OK), after that GitHub tries the source itself.
"""
import datetime as dt, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'data', 'health')
LEGACY = os.path.join(ROOT, 'data', 'feeds-health.json')   # single-file state used until 18 Oct 2026
SOFT_FAIL = re.compile(r'(?i)keeping previous|kept previous')
NETWORK = re.compile(r'(?i)URLError|HTTPError|timed? ?out|ConnectionError|RemoteDisconnected|IncompleteRead|SSLError|socket\.')

_seen = {}


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _path(feed):
    return os.path.join(DIR, re.sub(r'\.json$', '', feed) + '.json')


def _load(feed):
    if os.path.exists(LEGACY):   # move the old single file into per-feed files once
        os.makedirs(DIR, exist_ok=True)
        for f, e in json.load(open(LEGACY, encoding='utf-8')).items():
            if not os.path.exists(_path(f)):
                json.dump({'feed': f, **e}, open(_path(f), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
        os.remove(LEGACY)
    p = _path(feed)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def ok(feed):
    _seen[feed] = None
    if _load(feed) is not None:
        os.remove(_path(feed))


def fail(feed, error, max_hours, crash=False):
    now = _now().isoformat()
    old = _load(feed)
    e = dict(old or {'feed': feed, 'failingSince': now})
    e['maxHours'] = max_hours
    e.pop('crash', None)
    if crash:
        e['crash'] = True
    if e.get('lastError') != error[:300] or e.get('lastChecked', '')[:10] != now[:10]:
        e['lastError'], e['lastChecked'] = error[:300], now
    if e != old:
        os.makedirs(DIR, exist_ok=True)
        json.dump(e, open(_path(feed), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    _seen[feed] = e


def finish():
    """Report this run's failing feeds; exit 1 only for a script crash past its limit."""
    red, rows = [], []
    for feed, e in _seen.items():
        if e is None:
            continue
        hours = (_now() - dt.datetime.fromisoformat(e['failingSince'])).total_seconds() / 3600
        over = hours >= e['maxHours']
        msg = f"{feed}: failing for {hours:.1f} h (limit {e['maxHours']} h), previous data kept - {e['lastError']}"
        if over and e.get('crash'):
            red.append(feed)
            print(f'::error title={feed} script crash::{msg}')
        elif over:
            print(f'::warning title={feed} is STALE::{msg}')
        else:
            print(f'::warning title={feed} temporarily unavailable::{msg}')
        if over:
            rows.append(f"- **{feed}** stale for {hours / 24:.1f} days: {e['lastError'][:200]}\n")
    if rows and os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.writelines(rows)
    sys.exit(1 if red else 0)


def local_age(name):
    """Hours since scripts/local_refresh.py last confirmed data/<name>, or None."""
    try:
        checked = json.load(open(os.path.join(ROOT, 'data', name), encoding='utf-8'))['checked']
        return (_now() - dt.datetime.fromisoformat(checked)).total_seconds() / 3600
    except (OSError, KeyError, ValueError):
        return None


def run(feed, max_hours, cmd, local=None):
    if local:
        name, hours = local.split(':')
        age = local_age(name)
        if age is not None and age < float(hours):
            # The source refuses GitHub: don't spend the run on it while the home PC copy is fresh
            print(f'{feed}: skipped; home PC copy of {name} checked {age:.1f} h ago')
            ok(feed)
            finish()
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
        crash = 'Traceback (most recent call last)' in out and not NETWORK.search(tail[0])
        fail(feed, f'exit {r.returncode}: {tail[0]}', max_hours, crash)
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
