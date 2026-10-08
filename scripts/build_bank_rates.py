"""Bank deposit and lending rates -> data/bank-rates.json

1. CBSL weekly averages for all licensed commercial banks (Data Library report
   "Commercial Bank Lending and Deposit Rates", cbsl.lk/eResearch ReportId=6277):
   AWLR, AWPR (weekly, monthly), AWDR, AWFDR, AWSR. The page is an ASP.NET form:
   open the report link first (sets the session), then post a From/To date range.
   Two sub-columns labelled only "6 months" are not explained on the page and are
   left out.
2. Each bank's own rupee fixed deposit rates (interest paid at maturity, regular
   deposits, not senior-citizen or special schemes) and its ordinary savings rate,
   read from the bank's rate page. Banks that render their rates in the browser
   (HNB, Sampath, NSB, DFCC, NDB, Nations Trust) are not covered yet.

All banks' annual effective rates (AER) are computed the same way from the
nominal rate, so multi-year "at maturity" rates can be compared:
  AER = (1 + r * years) ** (1 / years) - 1
A bank whose page fails to parse keeps its previous entry (with its old
"checked" date); entries are only shown by the site while they are recent.
Standard library only.
"""
import datetime as dt, html, json, os, re, subprocess, tempfile, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'bank-rates.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TENURES = [1, 3, 6, 12, 24, 36, 60]   # months
CBSL_LINK = 'https://www.cbsl.lk/eResearch/MoneyMarketRatesDefault.aspx?ReportId=6277'
CBSL_FORM = 'https://www.cbsl.lk/eResearch/Modules/RD/SearchPages/CMB_LendingAndDeposit.aspx'
CBSL_FROM = '2024-01-01'
CBSL_COLS = ['awlr', 'awprWeekly', 'awprMonthly', None, 'awdr', None, 'awfdr', 'awsr']   # cells after the date


def curl(url, jar=None, data=None, referer=None):
    cmd = ['curl', '-sSL', '--compressed', '-m', '90', '-A', UA]
    if jar:
        cmd += ['-c', jar, '-b', jar]
    if referer:
        cmd += ['-e', referer]
    if data is not None:
        cmd += ['--data', data]
    r = subprocess.run(cmd + [url], capture_output=True)
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def clean(x):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', x))).strip()


def tables(page):
    """[(text just before the table, [[cell, …], …]), …]"""
    out = []
    for m in re.finditer(r'<table.*?</table>', page, re.S | re.I):
        ctx = clean(re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page[max(0, m.start() - 1500):m.start()], flags=re.S))[-300:]
        rows = [[clean(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S | re.I)]
                for r in re.findall(r'<tr.*?</tr>', m.group(0), re.S | re.I)]
        out.append((ctx, [r for r in rows if any(r)]))
    return out


def find(tbls, ctx_re, body_re=None):
    for ctx, rows in tbls:
        if re.search(ctx_re, ctx, re.I) and (body_re is None or re.search(body_re, ' '.join(' '.join(r) for r in rows), re.I)):
            return ctx, rows
    return None, None


def pct(s):
    m = re.search(r'(\d{1,2}(?:\.\d{1,2})?)\s*%?', s or '')
    v = float(m.group(1)) if m else None
    return v if v is not None and 0.5 <= v <= 25 else None


def months(label):
    m = re.search(r'(\d+)\s*(month|year)', label, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n * 12 if m.group(2).lower().startswith('y') else n


def aer(rate, m):
    years = m / 12
    return round(((1 + rate / 100 * years) ** (1 / years) - 1) * 100, 2)


def date_from(text, patterns):
    for p in patterns:
        m = re.search(p, text or '', re.I)
        if m:
            s = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', m.group(1)).strip()
            for fmt in ('%d.%m.%Y', '%d %B %Y', '%d/%m/%Y'):
                try:
                    return dt.datetime.strptime(s, fmt).date().isoformat()
                except ValueError:
                    pass
    return None


def rows_by_label(rows, keep=lambda label: True, col=1):
    fd = {}
    for r in rows:
        if len(r) <= col:
            continue
        m = months(r[0])
        v = pct(r[col])
        if m in TENURES and v and keep(r[0]) and m not in fd:
            fd[m] = v
    return fd


# ── Per-bank readers: return {'fd': {months: rate}, 'effective': iso|None, 'savings': rate|None, 'savingsLabel': str} ──

def boc(page):
    t = tables(page)
    ctx, rows = find(t, r'FIXED DEPOSITS.*Effective from')
    if not rows:
        return None
    fd = rows_by_label(rows, lambda l: 'senior' not in l.lower() and ('maturity' in l.lower() or 'year' not in l.lower()))
    _, sv = find(t, r'.', r'Ordinary Savings')
    sav = next((pct(r[1]) for r in (sv or []) if 'Ordinary Savings' in r[0] and len(r) > 1), None)
    return {'fd': fd, 'effective': date_from(ctx, [r'Effective from (\d\d\.\d\d\.\d{4})(?!.*Effective from)']),
            'savings': sav, 'savingsLabel': 'Ordinary savings'}


def peoples(page):
    t = tables(page)
    ctx, rows = find(t, r'Fixed deposits \(Minimum deposit Rs\. ?5,000')
    if not rows:
        return None
    return {'fd': rows_by_label(rows), 'effective': date_from(ctx, [r'w\.e\.f\.? (\d+\w* \w+ \d{4})\)?\s*$']),
            'savings': None, 'savingsLabel': None}


def commercial(page):
    t = tables(page)
    ctx, rows = find(t, r'Fixed Deposits\s*$', r'\(LKR\)')
    if not rows:
        return None
    fd = rows_by_label(rows, lambda l: '(LKR)' in l and ('maturity' in l.lower() or not re.search(r'month(ly|s -)', l, re.I) or months(l) < 12))
    fd = {m: v for m, v in fd.items()}
    # 12+ month rows must be the "Interest at maturity" ones
    for r in rows:
        m = months(r[0])
        if m in TENURES and m >= 12 and 'maturity' in r[0].lower():
            fd[m] = pct(r[1])
    _, sv = find(t, r'Regular Savings Account\s*$')
    sav = next((pct(r[1]) for r in (sv or []) if 'monthly' in r[0].lower() and len(r) > 1), None)
    return {'fd': fd, 'effective': None, 'savings': sav, 'savingsLabel': 'Regular savings'}


def seylan(page):
    t = tables(page)
    ctx, rows = find(t, r'Fixed Deposits - Interest Paid At Maturity')
    if not rows:
        return None
    fd = rows_by_label(rows, lambda l: '*' not in l)
    _, sv = find(t, r'Normal Savings Account')
    sav = next((pct(r[1]) for r in (sv or []) if '10,000' in r[0] and len(r) > 1), None)
    return {'fd': fd, 'effective': date_from(ctx, [r'w\.e\.f\.? (\d\d\.\d\d\.\d{4})\s*$']),
            'savings': sav, 'savingsLabel': 'Normal savings, Rs 10,000+'}


def sdb(page):
    t = tables(page)
    ctx, rows = find(t, r'Normal / Corporate FD')
    if not rows or len(rows) < 2:
        return None
    head, mat = rows[0], next((r for r in rows if r and r[0].lower() == 'maturity'), None)
    if not mat:
        return None
    fd = {}
    for label, val in zip(head[1:], mat[1:]):
        m, v = months(label), pct(val)
        if m in TENURES and v:
            fd[m] = v
    eff = date_from(' '.join(' '.join(r) for r in rows), [r'With effect from (\d+\w* \w+ \d{4})'])
    return {'fd': fd, 'effective': eff, 'savings': None, 'savingsLabel': None}


BANKS = [
    ('boc', 'Bank of Ceylon', 'BOC', 'state', 'https://www.boc.lk/rates-tariff', boc),
    ('peoples', "People's Bank", "People's", 'state', 'https://www.peoplesbank.lk/interest-rates/', peoples),
    ('commercial', 'Commercial Bank', 'ComBank', 'private', 'https://www.combank.lk/rates-tariff', commercial),
    ('seylan', 'Seylan Bank', 'Seylan', 'private', 'https://www.seylan.lk/interest-rates', seylan),
    ('sdb', 'SDB bank', 'SDB', 'private', 'https://www.sdb.lk/en/rates?tableid=5', sdb),
]
NOT_COVERED = ['HNB', 'Sampath Bank', 'National Savings Bank', 'DFCC Bank', 'NDB Bank', 'Nations Trust Bank']


def cbsl_weekly(today):
    with tempfile.TemporaryDirectory() as tmp:
        jar = os.path.join(tmp, 'jar.txt')
        curl(CBSL_LINK, jar)
        page = curl(CBSL_FORM, jar)
        if not page:
            return None
        fields = {k: html.unescape(v) for k, v in re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page)}
        fields.update({'ctl00$ContentPlaceHolder1$txtFrom': CBSL_FROM, 'ctl00$ContentPlaceHolder1$txtTo': today,
                       'ctl00$ContentPlaceHolder1$btnShow': 'Show'})
        out = curl(CBSL_FORM, jar, urllib.parse.urlencode(fields), CBSL_FORM)
    if not out:
        return None
    weeks = []
    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', out, re.S):
        c = [clean(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)]
        if c and re.match(r'\d{4}-\d\d-\d\d$', c[0]) and len(c) >= 1 + len(CBSL_COLS):
            vals = [pct(v) if v else None for v, k in zip(c[1:], CBSL_COLS) if k]
            weeks.append([c[0]] + vals)
    if len(weeks) < 10:
        return None
    weeks.sort()
    return {'source': 'Central Bank of Sri Lanka — Commercial Bank Lending and Deposit Rates (weekly)', 'url': CBSL_LINK,
            'columns': ['week'] + [k for k in CBSL_COLS if k], 'weeks': weeks}


def main():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    today = now.date().isoformat()
    prev = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    prev_banks = {b['id']: b for b in prev.get('banks', [])}

    cbsl = cbsl_weekly(today) or prev.get('cbsl')
    print('cbsl:', len(cbsl['weeks']) if cbsl else 'FAILED', 'weeks')

    banks = []
    for bid, name, short, owner, url, reader in BANKS:
        page = curl(url)
        got = None
        try:
            got = reader(page) if page else None
        except Exception as e:   # one bank's layout change must not stop the others
            print(f'  {short}: reader error {e}')
        if got and len(got['fd']) >= 3:
            fd = {str(m): {'rate': v, 'aer': aer(v, m)} for m, v in sorted(got['fd'].items())}
            banks.append({'id': bid, 'name': name, 'short': short, 'owner': owner, 'url': url, 'checked': today,
                          'effective': got['effective'], 'fd': fd, 'savings': got['savings'], 'savingsLabel': got['savingsLabel']})
            print(f'  {short}: {len(fd)} terms, effective {got["effective"]}, savings {got["savings"]}')
        elif bid in prev_banks:
            banks.append(prev_banks[bid])
            print(f'  {short}: parse failed, kept entry checked {prev_banks[bid]["checked"]}')
        else:
            print(f'  {short}: parse failed, no previous entry')

    if not banks and not cbsl:
        raise SystemExit('nothing parsed; keeping previous file')
    out = {'tenures': TENURES, 'cbsl': cbsl, 'banks': banks, 'notCovered': NOT_COVERED,
           'aerNote': 'AER = (1 + rate x years)^(1/years) - 1, computed the same way for every bank'}
    old = dict(prev)
    old.pop('generated', None)
    if old == out:
        print('bank-rates.json: unchanged')
        return
    out = {'generated': now.isoformat(timespec='seconds'), **out}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
