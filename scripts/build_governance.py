"""Governance, transparency and digital government scores -> data/governance.json

Sources (all free, no key):
  World Bank Worldwide Governance Indicators (WGI) - 6 dimensions, 0-100 score
      and -2.5..2.5 estimate, 1996-latest (api.worldbank.org, GOV_WGI_*).
  Transparency International Corruption Perceptions Index - score 0-100 and
      rank among all scored countries (via Our World in Data grapher CSV).
  V-Dem judicial constraints on the executive and rule of law - 0-1
      (via Our World in Data grapher CSV).
  UN E-Government Survey - EGDI, E-Participation and sub-indices
      (publicadministration.un.org/egovkb, Sri Lanka = id 161).
  World Bank ICT indicators - internet users, mobile subscriptions, broadband.

There is no regular international dataset for judicial *efficiency*
(case backlog / disposal time) for Sri Lanka, so none is published here.
Standard library only. Each block keeps its previous value if a source fails.
"""
import csv, datetime as dt, html, io, json, os, re, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'governance.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
PEERS = ['LKA', 'IND', 'BGD', 'PAK', 'NPL', 'MDV', 'THA', 'MYS', 'VNM', 'IDN', 'SGP']
WGI = {'CC': 'Control of Corruption', 'RQ': 'Regulatory Quality', 'RL': 'Rule of Law',
       'GE': 'Government Effectiveness', 'VA': 'Voice & Accountability', 'PV': 'Political Stability'}
ICT = {'internet': 'IT.NET.USER.ZS', 'mobile': 'IT.CEL.SETS.P2', 'broadband': 'IT.NET.BBND.P2'}


def get(url, timeout=90):
    r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url], capture_output=True)
    return r.stdout.decode('utf-8', errors='replace') if r.returncode == 0 else None


def wb_series(code, country='LKA'):
    txt = get(f'https://api.worldbank.org/v2/country/{country}/indicator/{code}?format=json&per_page=200')
    try:
        rows = json.loads(txt)[1] or []
    except (TypeError, ValueError, IndexError):
        return None
    pts = sorted((int(r['date']), round(r['value'], 3)) for r in rows if r.get('value') is not None)
    return pts or None


def owid(slug):
    txt = get(f'https://ourworldindata.org/grapher/{slug}.csv?csvType=full&useColumnShortNames=true')
    if not txt:
        return None
    rd = csv.reader(io.StringIO(txt))
    head = next(rd)
    return [dict(zip(head, r)) for r in rd]


def wgi():
    out = {}
    for k, name in WGI.items():
        sc, est = wb_series(f'GOV_WGI_{k}.SC'), wb_series(f'GOV_WGI_{k}.EST')
        if sc:
            out[k] = {'name': name, 'score': sc, 'estimate': est or []}
    if not out:
        return None
    # latest-year peer comparison for the score
    peers = {}
    for k in out:
        txt = get(f'https://api.worldbank.org/v2/country/{";".join(PEERS)}/indicator/GOV_WGI_{k}.SC'
                  f'?format=json&per_page=500&mrnev=1')
        try:
            rows = json.loads(txt)[1] or []
        except (TypeError, ValueError, IndexError):
            continue
        peers[k] = {r['countryiso3code']: round(r['value'], 1) for r in rows if r.get('value') is not None}
    return {'source': 'World Bank Worldwide Governance Indicators',
            'url': 'https://www.worldbank.org/en/publication/worldwide-governance-indicators',
            'note': 'Percentile-style score 0-100 (higher = better governance).',
            'indicators': out, 'peers': peers}


def cpi():
    rows = owid('ti-corruption-perception-index')
    if not rows:
        return None
    col = next((c for c in rows[0] if c.startswith('cpi')), None)
    by_year = {}
    for r in rows:
        if r.get('code') and len(r['code']) == 3 and r.get(col):
            by_year.setdefault(int(r['year']), {})[r['code']] = float(r[col])
    series, ranks = [], []
    for y in sorted(by_year):
        d = by_year[y]
        if 'LKA' in d:
            series.append([y, d['LKA']])
            ranks.append([y, 1 + sum(1 for v in d.values() if v > d['LKA']), len(d)])
    if not series:
        return None
    latest = max(by_year)
    return {'source': 'Transparency International CPI (via Our World in Data)',
            'url': 'https://www.transparency.org/en/cpi', 'note': 'Score 0-100 (100 = very clean).',
            'score': series, 'rank': ranks,
            'peers': {c: by_year[latest][c] for c in PEERS if c in by_year[latest]}, 'peerYear': latest}


def vdem():
    out = {}
    for key, slug, name in [('judicial', 'judicial-constraints-on-the-executive-index', 'Judicial constraints on the executive'),
                            ('ruleOfLaw', 'rule-of-law-index', 'Rule of law')]:
        rows = owid(slug)
        if not rows:
            continue
        col = next((c for c in rows[0] if c.endswith('estimate_best')), None)
        pts = sorted([int(r['year']), round(float(r[col]), 3)] for r in rows
                     if r.get('code') == 'LKA' and r.get(col) and int(r['year']) >= 1990)
        if pts:
            out[key] = {'name': name, 'series': pts}
    if not out:
        return None
    return {'source': 'V-Dem Institute (via Our World in Data)', 'url': 'https://v-dem.net/',
            'note': 'Index 0-1 (higher = stronger independent courts / rule of law).', **out}


def egdi():
    page = get('https://publicadministration.un.org/egovkb/en-us/Data/Country-Information/id/161-Sri-Lanka')
    if not page:
        return None
    clean = lambda c: re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', '', c))).strip()
    series = {}
    for t in re.findall(r'<table.*?</table>', page, re.S):
        rows = [[clean(c) for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', r, re.S)]
                for r in re.findall(r'<tr.*?</tr>', t, re.S)]
        if len(rows) < 2 or len(rows[0]) < 3 or not rows[0][1].isdigit():
            continue
        years = [int(y) for y in rows[0][1:]]
        entry = {}
        for r in rows[1:]:
            kind = 'rank' if '(Rank)' in r[0] else 'value' if '(Value)' in r[0] else None
            if kind:
                entry[kind] = sorted([y, float(v) if kind == 'value' else int(v)]
                                     for y, v in zip(years, r[1:]) if re.fullmatch(r'[\d.]+', v))
        if entry:
            series[rows[0][0]] = entry
    if 'E-Government Development Index' not in series:
        return None
    return {'source': 'UN E-Government Survey', 'outOf': 193,
            'url': 'https://publicadministration.un.org/egovkb/en-us/Data/Country-Information/id/161-Sri-Lanka',
            'note': 'Index 0-1, surveyed every two years; rank among 193 UN member states.', 'series': series}


def ict():
    out = {k: wb_series(c) for k, c in ICT.items()}
    out = {k: [p for p in v if p[0] >= 2000] for k, v in out.items() if v}
    return {'source': 'World Bank (ITU data)', 'note': 'Internet users: % of population; '
            'mobile & broadband: subscriptions per 100 people.', **out} if out else None


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    old.pop('generated', None)
    payload = {}
    for key, fn in [('wgi', wgi), ('cpi', cpi), ('vdem', vdem), ('egdi', egdi), ('ict', ict)]:
        try:
            val = fn()
        except Exception as e:  # keep previous block on any source failure
            print(f'  {key}: error {e}')
            val = None
        if val:
            payload[key] = val
            print(f'  {key}: ok')
        elif key in old:
            payload[key] = old[key]
            print(f'  {key}: failed; kept previous')
        else:
            print(f'  {key}: failed')
    if not payload:
        print('governance: nothing built')
        return
    if payload == old:
        print('governance.json: unchanged')
        return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'governance.json: updated ({os.path.getsize(OUT)} bytes)')


if __name__ == '__main__':
    main()
