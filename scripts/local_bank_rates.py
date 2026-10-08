"""Refresh the banks that block cloud servers -> data/bank-rates.json on GitHub

Bank of Ceylon and Commercial Bank sit behind a CloudFront firewall that
answers GitHub Actions (and other cloud services) with HTTP 403, so the daily
workflow cannot read them. This script runs on a home PC (Windows Task
Scheduler, 1st and 15th of each month): it reads just those banks with the
same readers as build_bank_rates.py, merges them into the CURRENT bank-rates.json
on GitHub, and uploads the file with the GitHub API (`gh`), because plain git
pushes are blocked on that PC. Everything else in the file is left as is.

Log: %LOCALAPPDATA%\\CeylonPulse\\bank-rates.log
Run by hand: python scripts/local_bank_rates.py
"""
import base64, datetime as dt, json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_bank_rates as b   # noqa: E402

REPO = 'repos/investwithdon7-rgb/ceylonpulse-data'
PATH = 'data/bank-rates.json'
LOG = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'CeylonPulse', 'bank-rates.log')


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


def read_local_banks(today):
    got = {}
    for bid, name, short, owner, url, reader in b.BANKS:
        if bid not in b.LOCAL_ONLY:
            continue
        page = b.curl(url)
        try:
            r = reader(page) if page else None
        except Exception as e:
            r = None
            log(f'{short}: reader error {e}')
        if r and len(r['fd']) >= 3:
            got[bid] = {'id': bid, 'name': name, 'short': short, 'owner': owner, 'url': url, 'checked': today,
                        'effective': r['effective'], 'fd': {str(m): {'rate': v, 'aer': b.aer(v, m)} for m, v in sorted(r['fd'].items())},
                        'savings': r['savings'], 'savingsLabel': r['savingsLabel'], 'via': 'local'}
            log(f'{short}: {len(r["fd"])} terms, effective {r["effective"]}')
        else:
            log(f'{short}: could not read ({b.probe(url)[:200]})')
    return got


def main():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    fresh = read_local_banks(today)
    if not fresh:
        log('nothing read; GitHub file left unchanged')
        return 1
    for attempt in (1, 2):   # the hourly/daily bots may commit in between: re-read and retry once
        cur = gh(f'{REPO}/contents/{PATH}')
        data = json.loads(base64.b64decode(cur['content']).decode('utf-8'))
        order = [x[0] for x in b.BANKS]
        banks = {x['id']: x for x in data.get('banks', [])}
        banks.update(fresh)
        data['banks'] = sorted(banks.values(), key=lambda x: order.index(x['id']) if x['id'] in order else 99)
        data['generated'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')
        payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        try:
            res = gh(f'{REPO}/contents/{PATH}', '-X', 'PUT', body={
                'message': f'Bank rates from home PC ({", ".join(x["short"] for x in fresh.values())}) {today}',
                'content': base64.b64encode(payload).decode(), 'sha': cur['sha']})
            log(f'uploaded {PATH} as commit {res["commit"]["sha"][:7]}')
            break
        except RuntimeError as e:
            if attempt == 2:
                log(f'upload failed: {e}')
                return 1
            log(f'upload conflict, retrying: {str(e)[:120]}')
    subprocess.run(['curl', '-s', '-o', os.devnull, f'https://purge.jsdelivr.net/gh/investwithdon7-rgb/ceylonpulse-data@main/{PATH}'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
