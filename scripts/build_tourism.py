"""Tourism -> data/tourism.json

  * Tourist arrivals: SLTDA weekly report PDF (latest of the year). Page 2 has
    arrivals by month for 2026, last year and 2018; a later page lists the top
    twenty source markets for the year so far.
  * US travel advisory level for Sri Lanka: travel.state.gov RSS (it sends no
    CORS headers, so the browser cannot read it directly).

Rewritten only when the content changes. Needs: pip install pypdf
"""
import datetime as dt, html, io, json, os, re, subprocess

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
WEEKLY_PAGE = 'https://www.sltda.gov.lk/en/weekly-tourist-arrivals-reports-{}'
US_RSS = 'https://travel.state.gov/_res/rss/TAsTWs.xml'
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December']


def curl(url, binary=False, timeout=90):
    r = subprocess.run(['curl', '-sSL', '--fail', '-m', str(timeout), '-A', UA, url], capture_output=True)
    if r.returncode != 0:
        print('  fetch failed', url[-70:], r.stderr.decode(errors='replace')[:100])
        return None
    return r.stdout if binary else r.stdout.decode('utf-8', errors='replace')


def n(s):
    return int(s.replace(',', '')) if s else None


def latest_weekly():
    year = dt.datetime.now(COLOMBO).year
    for y in (year, year - 1):  # early January may have no report yet
        page = curl(WEEKLY_PAGE.format(y))
        links = re.findall(r'href="([^"]+\.pdf)"', page or '')
        if links:
            return links[-1]
    return None


def parse_weekly(pdf):
    pages = [p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages]
    summary = next((t for t in pages if 'SUMMARY REPORT' in t), None)
    if not summary:
        return None
    years = re.search(r'^\s*(\d{4})\s+(\d{4})\s+(\d{4})\s*$', summary, re.M)
    period = re.search(r'Tourist arrivals from\s+(.+?\d{4})', summary)
    rows = []
    for m in MONTHS:
        r = re.search(rf'^{m}\s+([\d,]+)\s+([\d,]+)(?:\s+([\d,]+))?', summary, re.M)
        if not r:
            return None
        rows.append({'month': m, 'base': n(r.group(1)), 'last': n(r.group(2)), 'this': n(r.group(3))})
    period_n = re.search(r'Tourist arrivals\s+\S.*?\d{4}\s+([\d,]+)\s*$', summary, re.S)
    top = []
    ytd_page = next((t for t in pages if 'Top twenty' in t), '')
    for r in re.finditer(r'^\s*(\d{1,2})\s+([A-Za-z][A-Za-z .,&()\-]+?)\s+([\d,]+)\s*$', ytd_page, re.M):
        top.append({'rank': int(r.group(1)), 'country': r.group(2).strip(), 'arrivals': n(r.group(3))})
    total = re.search(r'TOTAL\s+([\d,]+)', ytd_page)
    ytd_period = re.search(r'(\d{1,2}\w*\s+January\s*[–-]\s*\d{1,2}\w*\s+\w+\s+\d{4})', ytd_page)
    y0, y1, y2 = (int(x) for x in years.groups()) if years else (None, None, None)
    return {
        'years': {'base': y0, 'last': y1, 'this': y2},
        'period': period.group(1).strip() if period else None,
        'periodArrivals': n(period_n.group(1)) if period_n else None,
        'monthly': rows,
        'ytd': sum(r['this'] or 0 for r in rows),
        'topMarketsYtd': top[:20],
        'topMarketsPeriod': ytd_period.group(1) if ytd_period else None,
        'topMarketsTotal': n(total.group(1)) if total else None,
    }


def us_advisory():
    x = curl(US_RSS)
    if not x:
        return None
    for it in re.findall(r'<item>(.*?)</item>', x, re.S):
        title = re.search(r'<title>(.*?)</title>', it, re.S)
        if not title or not title.group(1).strip().startswith('Sri Lanka'):
            continue
        t = html.unescape(title.group(1)).strip()
        lvl = re.search(r'Level\s+(\d)\s*:\s*(.+)$', t)
        desc = html.unescape(re.search(r'<description>(.*?)</description>', it, re.S).group(1))
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(desc)))
        why = re.search(r'due to\s+(.+?)\.', text)
        date = re.search(r'<pubDate>(.*?)</pubDate>', it)
        link = re.search(r'<link>(.*?)</link>', it)
        issued = None
        if date:
            for fmt in ('%a, %d %b %Y', '%a, %d %b %Y %H:%M:%S %Z', '%a, %d %b %Y %H:%M:%S %z'):
                try:
                    issued = dt.datetime.strptime(date.group(1).strip(), fmt).date().isoformat()
                    break
                except ValueError:
                    pass
        return {
            'level': int(lvl.group(1)) if lvl else None,
            'label': lvl.group(2).strip() if lvl else t,
            'reasons': re.sub(r'\s+,', ',', why.group(1)).strip() if why else None,
            'issued': issued,
            'url': link.group(1).strip() if link else 'https://travel.state.gov/',
        }
    return None


def main():
    path = os.path.join(DATA, 'tourism.json')
    old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    old.pop('generated', None)
    payload = dict(old)

    url = latest_weekly()
    if url and url != old.get('arrivals', {}).get('reportUrl'):
        pdf = curl(url, binary=True)
        rep = parse_weekly(pdf) if pdf and pdf.startswith(b'%PDF') else None
        if rep:
            rep.update(source='Sri Lanka Tourism Development Authority — weekly report', reportUrl=url)
            payload['arrivals'] = rep
            print(f"tourism: arrivals {rep['period']} = {rep['periodArrivals']}, ytd {rep['ytd']}")
        else:
            print('tourism: weekly report not recognised; keeping previous arrivals')
    adv = us_advisory()
    if adv:
        payload['usAdvisory'] = adv
    if payload == old or not payload:
        print('tourism.json: unchanged')
        return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'tourism.json: updated ({os.path.getsize(path)} bytes)')


if __name__ == '__main__':
    main()
