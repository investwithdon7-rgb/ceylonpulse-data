"""Refresh fuel prices from a home PC -> data/fuel.json, data/fuel-history.json on GitHub

Since Oct 2026 Ceypetco's price API (api.ceypetco.gov.lk) only answers
Sri Lankan connections: GitHub Actions and foreign proxies time out. This
script runs on a home PC (Windows Task Scheduler, every 3 hours) with the same
readers as build_feeds.py and uploads the files with the GitHub API (`gh`),
because plain git pushes are blocked on that PC.

fuel.json gets a `checked` time. The hourly workflow still tries the API
itself; when it cannot connect it accepts this copy while `checked` is recent
(build_feeds.LOCAL_OK_HOURS), so the run stays green while the PC keeps it fresh.
An unchanged file is re-uploaded only twice a day, just to renew `checked`.

Log: %LOCALAPPDATA%\\CeylonPulse\\fuel.log
Run by hand: python scripts/local_fuel.py
"""
import base64, datetime as dt, json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_feeds as b   # noqa: E402

REPO = 'repos/investwithdon7-rgb/ceylonpulse-data'
LOG = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'CeylonPulse', 'fuel.log')
RENEW_HOURS = 12


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
    cur = gh(f'{REPO}/contents/data/{name}')
    return json.loads(base64.b64decode(cur['content']).decode('utf-8')), cur['sha']


def strip(d, *keys):
    return {k: v for k, v in d.items() if k not in keys}


def upload(name, payload, now, renew):
    """Upload data/<name> if its data changed (or `checked` is due a renewal)."""
    for attempt in (1, 2):   # the hourly bot may commit in between: re-read and retry once
        old, sha = remote(name)
        if strip(old, 'generated', 'checked') == payload:
            due = renew and (now - dt.datetime.fromisoformat(old.get('checked', '2000-01-01T00:00:00+00:00'))).total_seconds() > RENEW_HOURS * 3600
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
                'message': f'Fuel prices from home PC {now:%Y-%m-%dT%H:%MZ}',
                'content': base64.b64encode(body).decode(), 'sha': sha})
            log(f'{name}: uploaded as commit {res["commit"]["sha"][:7]}')
            subprocess.run(['curl', '-s', '-o', os.devnull, f'https://purge.jsdelivr.net/gh/investwithdon7-rgb/ceylonpulse-data@main/data/{name}'])
            return True
        except RuntimeError as e:
            if attempt == 2:
                log(f'{name}: upload failed: {e}')
                raise
            log(f'{name}: upload conflict, retrying: {str(e)[:120]}')


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    current = b.fuel()
    if not current:
        log('fuel: could not read the Ceypetco API; GitHub files left unchanged')
        return 1
    try:
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
    except RuntimeError:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
