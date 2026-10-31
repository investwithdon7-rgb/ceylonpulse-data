"""Refresh the sources that refuse cloud servers, from a home PC in Sri Lanka

  * Fuel (every run): Ceypetco's price API (api.ceypetco.gov.lk) only answers
    Sri Lankan connections since Oct 2026 -> data/fuel.json, fuel-history.json
  * Economy: cbsl.gov.lk answers GitHub Actions with HTTP 403 since Oct 2026
    -> data/economy.json (same build_economy.py readers)
  * CBSL Daily Price Report (same block) -> data/prices.json, archive/prices/YYYY.json

Task Scheduler ("CeylonPulse home refresh") starts this hourly and at logon, but
each source is fetched only once a day: a job runs when its `checked` time is
older than the latest 18:15 Sri Lanka time (an hour after CBSL usually posts
the price report), and retries on later starts until it succeeds. One attempt a
day was not enough: the PC is often off, just waking with no network, or a
source is briefly down. Uploads use the GitHub API (`gh`), because plain git
pushes are blocked on that PC.

fuel.json, economy.json and prices.json get a `checked` time: the GitHub
workflows skip those sources while a copy is recent (build_feeds.LOCAL_OK_HOURS,
health.py --local) and otherwise try the source themselves.

Log: ~/.ceylonpulse/home-refresh.log (not AppData: apps packaged as MSIX, such as
Claude desktop, see a private copy of AppData and could not read the task's log)
Run by hand: python scripts/local_refresh.py
"""
import base64, contextlib, datetime as dt, io, json, os, re, shutil, subprocess, sys, tempfile

if os.name == 'nt':   # run by pythonw from Task Scheduler: keep curl/gh from opening console windows
    _run = subprocess.run
    subprocess.run = lambda *a, **k: _run(*a, **{'creationflags': subprocess.CREATE_NO_WINDOW, **k})

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_feeds as b   # noqa: E402
import build_economy as econ   # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))

REPO = 'repos/investwithdon7-rgb/ceylonpulse-data'
LOG = os.path.join(os.path.expanduser('~'), '.ceylonpulse', 'home-refresh.log')
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAILY_AT = (18, 15)   # Sri Lanka time


def log(msg):
    line = f'{dt.datetime.now():%Y-%m-%d %H:%M} {msg}'
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def gh(*args, body=None):
    cmd = ['gh', 'api', *args]
    tmp = None
    if body is not None:
        tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(body, tmp)
        tmp.close()
        cmd += ['--input', tmp.name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    finally:
        if tmp:
            os.unlink(tmp.name)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return json.loads(r.stdout) if r.stdout.strip() else None


def remote(name):
    """GitHub's current copy of data/<name> (or of a repo path containing '/')."""
    cur = gh(f'{REPO}/contents/{name if "/" in name else "data/" + name}')
    if not cur.get('content'):   # files over 1 MB come without content: fetch them raw
        raw = subprocess.run(['gh', 'api', cur['url'], '-H', 'Accept: application/vnd.github.raw'],
                             capture_output=True, check=True).stdout
        return json.loads(raw.decode('utf-8')), cur['sha']
    return json.loads(base64.b64decode(cur['content']).decode('utf-8')), cur['sha']


def strip(d, *keys):
    return {k: v for k, v in d.items() if k not in keys}


def put(rel, data, message):
    """Upload repo path `rel` as compact JSON (archive files: no `checked` stamp)."""
    for attempt in (1, 2):
        try:
            sha = gh(f'{REPO}/contents/{rel}').get('sha')
        except RuntimeError:
            sha = None   # a new year's file
        body = {'message': message, 'content': base64.b64encode(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).decode()}
        if sha:
            body['sha'] = sha
        try:
            res = gh(f'{REPO}/contents/{rel}', '-X', 'PUT', body=body)
            log(f'{rel}: uploaded as commit {res["commit"]["sha"][:7]}')
            return
        except RuntimeError as e:
            if attempt == 2:
                log(f'{rel}: upload failed: {e}')
                raise


def latest_slot(now):
    """The most recent 18:15 Sri Lanka time at or before `now`."""
    local = now.astimezone(COLOMBO)
    slot = local.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
    return slot if slot <= local else slot - dt.timedelta(days=1)


def is_due(name, now):
    """True when data/<name> has not been confirmed since the latest daily slot."""
    checked = remote(name)[0].get('checked')
    return not checked or dt.datetime.fromisoformat(checked) < latest_slot(now)


def upload(name, payload, now, renew):
    """Upload data/<name> if its data changed, or to renew `checked` after a good read."""
    for attempt in (1, 2):   # the hourly bot may commit in between: re-read and retry once
        old, sha = remote(name)
        if strip(old, 'generated', 'checked') == payload:
            due = renew
            if not due:
                log(f'{name}: unchanged')
                return False
            out = {**old}
        else:
            out = {'generated': now.isoformat(timespec='seconds'), **payload}
        if renew:
            out['checked'] = now.isoformat(timespec='seconds')
        body = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        try:
            res = gh(f'{REPO}/contents/data/{name}', '-X', 'PUT', body={
                'message': f'{name} from home PC {now:%Y-%m-%dT%H:%MZ}',
                'content': base64.b64encode(body).decode(), 'sha': sha})
            log(f'{name}: uploaded as commit {res["commit"]["sha"][:7]}')
            subprocess.run(['curl', '-s', '-o', os.devnull, f'https://purge.jsdelivr.net/gh/investwithdon7-rgb/ceylonpulse-data@main/data/{name}'])
            return True
        except RuntimeError as e:
            if attempt == 2:
                log(f'{name}: upload failed: {e}')
                raise
            log(f'{name}: upload conflict, retrying: {str(e)[:120]}')


def refresh_fuel(now):
    current = b.fuel()
    if not current:
        log('fuel: could not read the Ceypetco API; GitHub files left unchanged')
        return False
    upload('fuel.json', current, now, renew=True)
    # fuel_history() merges with the published file, so give it GitHub's current copy
    with tempfile.TemporaryDirectory() as tmp:
        hist, _ = remote('fuel-history.json')
        json.dump(hist, open(os.path.join(tmp, 'fuel-history.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        b.DATA = tmp
        history = b.fuel_history(current)
    if history:
        upload('fuel-history.json', history, now, renew=False)
    else:
        log('fuel-history: could not read the Ceypetco API; left unchanged')
    return True


def refresh_economy(now):
    old, _ = remote('economy.json')
    with tempfile.TemporaryDirectory() as tmp:
        econ.OUT = os.path.join(tmp, 'economy.json')
        json.dump(old, open(econ.OUT, 'w', encoding='utf-8'), ensure_ascii=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            econ.main()
        new = json.load(open(econ.OUT, encoding='utf-8'))
    failed = [l.strip() for l in buf.getvalue().splitlines() if 'kept previous' in l]
    log('economy: ' + ('; '.join(failed) if failed else 'all series read'))
    # Renew `checked` only when every series was read, so a broken sheet still shows up on GitHub
    upload('economy.json', {k: v for k, v in new.items() if k not in ('generated', 'checked')}, now, renew=not failed)
    return not failed


def refresh_prices(now):
    """Run build_prices.py in a scratch copy of the repo holding GitHub's current files."""
    years = [f['name'] for f in gh(f'{REPO}/contents/archive/prices') if re.match(r'\d{4}\.json$', f['name'])]
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'data'))
        os.makedirs(os.path.join(tmp, 'archive', 'prices'))
        shutil.copytree(SCRIPTS, os.path.join(tmp, 'scripts'), ignore=shutil.ignore_patterns('__pycache__'))
        before = {}
        for rel in ['data/prices.json'] + [f'archive/prices/{y}' for y in years]:
            before[rel], _ = remote(rel)
            json.dump(before[rel], open(os.path.join(tmp, rel), 'w', encoding='utf-8'), ensure_ascii=False)
        r = subprocess.run([sys.executable, os.path.join(tmp, 'scripts', 'build_prices.py')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        out = (r.stdout or '') + (r.stderr or '')
        failed = r.returncode != 0 or 'keeping previous' in out
        log('prices: ' + ' | '.join(l.strip() for l in out.splitlines() if l.strip())[:300])
        for y in sorted(os.listdir(os.path.join(tmp, 'archive', 'prices'))):
            rel = f'archive/prices/{y}'
            new = json.load(open(os.path.join(tmp, rel), encoding='utf-8'))
            if new != before.get(rel):
                put(rel, new, f'Price report archive from home PC {now:%Y-%m-%d}')
        new = json.load(open(os.path.join(tmp, 'data', 'prices.json'), encoding='utf-8'))
    upload('prices.json', {k: v for k, v in new.items() if k not in ('generated', 'checked')}, now, renew=not failed)
    return not failed


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    ok = True
    for job, name in ((refresh_fuel, 'fuel.json'), (refresh_economy, 'economy.json'), (refresh_prices, 'prices.json')):
        try:
            if is_due(name, now):
                ok = job(now) and ok
        except Exception as e:
            log(f'{job.__name__}: {e}')
            ok = False
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
