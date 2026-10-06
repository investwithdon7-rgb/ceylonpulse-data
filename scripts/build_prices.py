"""CBSL Daily Price Report -> data/prices.json + archive/prices/YYYY.json

The Central Bank publishes a two-page PDF every working day:
  page 1: the day's biggest price moves, each with the reason CBSL gives
  page 2: wholesale and retail prices (yesterday / today) of ~40 foods at
          Pettah, Dambulla, Narahenpita, Marandagahamula (rice) and
          Peliyagoda / Negombo (fish)

Page 2 is a table, so numbers are placed into columns by their x position on
the page rather than by their order in the text (empty cells shift the order).
Only reports not yet archived are downloaded; PDFs are parsed in memory and
never stored. The archive keeps one compact JSON file per year.
Needs: pip install pypdf
Usage: python scripts/build_prices.py [--backfill DAYS] [--max N]
"""
import datetime as dt, io, json, os, re, subprocess, sys

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
ARCH = os.path.join(ROOT, 'archive', 'prices')
LIST_URL = 'https://www.cbsl.gov.lk/en/statistics/economic-indicators/price-report'
PDF_URL = 'https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/statistics/pricerpt/price_report_{}_e.pdf'
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))

# x position (pt) of each Yesterday/Today column on page 2
COLS = [170, 209, 251, 290, 337, 378, 421, 458, 503, 545]
# Market of each column pair, by table section
MARKETS = {
    'default': (('wholesale', 'Pettah'), ('wholesale', 'Dambulla'), ('retail', 'Pettah'), ('retail', 'Dambulla'), ('retail', 'Narahenpita')),
    'rice':    (('wholesale', 'Pettah'), ('wholesale', 'Marandagahamula'), ('retail', 'Pettah'), ('retail', 'Dambulla'), ('retail', 'Narahenpita')),
    'fish':    (('wholesale', 'Peliyagoda'), ('wholesale', 'Negombo'), ('retail', 'Pettah'), ('retail', 'Negombo'), ('retail', 'Narahenpita')),
}
# Section headers on page 2 (text fragment -> group)
HEADERS = {'V  E  G': 'vegetables', 'O  T  H': 'other', 'F  R  U': 'fruits', 'R  I  C': 'rice',
           'Marandagahamula': 'rice', 'F  I  S': 'fish', 'Peliyagoda': 'fish'}
# Items whose price history is kept in prices.json (a household basket)
BASKET = ['Samba', 'Nadu', 'Kekulu (White)', 'Red Dhal', 'Sugar (White)', 'Egg (White)', 'Coconut (Avg.)',
          'Coconut oil', 'Big Onion (Imp)', 'Red Onion (Local)', 'Potato (Local)', 'Dried Chilli (Imp)',
          'Beans', 'Carrot', 'Tomato', 'Green Chilli', 'Lime', 'Kelawalla', 'Salaya', 'Sprat (Imp)']
HISTORY_DAYS = 365  # basket history in prices.json (report days)


def curl(url, binary=False, timeout=90):
    r = subprocess.run(['curl', '-sSL', '--fail', '-m', str(timeout), '-A', UA, url], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout if binary else r.stdout.decode('utf-8', errors='replace')


def num(s):
    s = s.replace(',', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_table(page):
    chunks = []
    page.extract_text(visitor_text=lambda t, cm, tm, fd, fs: t.strip() and chunks.append((tm[5], tm[4], t.strip())))
    headers = sorted(((y, g) for y, x, t in chunks for k, g in HEADERS.items() if k in t), reverse=True)
    rows = {}
    for y, x, t in chunks:
        rows.setdefault(round(y), []).append((x, t))
    items = []
    for y in sorted(rows, reverse=True):
        cells = sorted(rows[y])
        name = next((t for x, t in cells if x < 60), None)
        unit = next((t for x, t in cells if 100 < x < 150 and t.startswith('Rs')), None)
        if not name or not unit:
            continue
        vals = [None] * len(COLS)
        for x, t in cells:
            if x < 150:
                continue
            toks = t.split()
            i = min(range(len(COLS)), key=lambda k: abs(COLS[k] - x))
            for tok in toks:  # occasionally two cells arrive as one text run
                if i < len(COLS):
                    vals[i] = num(tok) if tok != 'n.a.' else None
                    i += 1
        group = next((g for hy, g in sorted(headers) if hy > y), 'other')
        mk = MARKETS.get(group, MARKETS['default'])
        name = name.replace('(lmp)', '(Imp)')  # CBSL typo
        item = {'name': name, 'unit': unit.replace('Rs./', ''), 'group': group, 'wholesale': {}, 'retail': {}}
        for p, (kind, market) in enumerate(mk):
            yv, tv = vals[2 * p], vals[2 * p + 1]
            if yv is not None or tv is not None:
                item[kind][market] = [yv, tv]
        if item['wholesale'] or item['retail']:
            items.append(item)
    return items


def parse_notes(text):
    """Page 1: 'Price of X increased in Y market ... due to ...' then 'p Market: 350.00 400.00'."""
    notes, buf = [], []
    skip = re.compile(r'^(Daily Price Report|A Summary of Price|Yesterday\s+Today|Others|Price \(Rs|Rs\.)', re.I)
    for line in text.splitlines():
        line = line.strip()
        if not line or skip.match(line):
            continue
        if re.match(r'^\d+\s+The above', line):
            break
        m = re.match(r'^([pq])\s*([A-Za-z ]+?)\s*:\s*([\d,]+\.\d+)\s+([\d,]+\.\d+)', line)
        if m:
            txt = re.sub(r'\s+', ' ', ' '.join(buf)).strip()
            item = re.search(r'(?:price of|Wholesale price of)\s+(.+?)\s+(?:increased|declined|decreased|remained)', txt, re.I)
            notes.append({'item': item.group(1) if item else None, 'market': m.group(2).strip(),
                          'from': num(m.group(3)), 'to': num(m.group(4)),
                          'dir': 'up' if m.group(1) == 'p' else 'down', 'text': txt})
            buf = []
        elif re.match(r'^[A-Z][a-z]', line) or buf:
            buf.append(line)
    return notes


def parse_report(pdf, day):
    reader = PdfReader(io.BytesIO(pdf))
    if len(reader.pages) < 2:
        return None
    items = parse_table(reader.pages[1])
    if len(items) < 20:
        return None
    return {'date': day, 'items': items, 'notes': parse_notes(reader.pages[0].extract_text() or '')}


def consumer_price(item):
    """Today's retail price at the Colombo consumer market (Narahenpita), else Pettah retail."""
    for m in ('Narahenpita', 'Pettah', 'Dambulla', 'Negombo'):
        v = item['retail'].get(m)
        if v and v[1] is not None:
            return v[1]
    return None


# ── Compact yearly archive ─────────────────────────────────────────
# archive/prices/YYYY.json holds every report of the year:
#   items: [[name, unit, group], ...]            item catalogue (index = position)
#   days:  {date: {"v": [[...], ...], "n": [...]}}
# v[i] lists today's prices for item i in the order of MARKETS[group]
# (wholesale 1, wholesale 2, retail 1, retail 2, retail 3), null if not
# reported or not in that day's report. Yesterday's price is the previous
# report's value, so it is not stored again. n = the day's notes, compact.
def year_path(year):
    return os.path.join(ARCH, f'{year}.json')


def load_year(year):
    p = year_path(year)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'items': [], 'days': {}}


def compact(rep, arch):
    index = {it[0]: i for i, it in enumerate(arch['items'])}
    vals = []
    for item in rep['items']:
        if item['name'] not in index:
            index[item['name']] = len(arch['items'])
            arch['items'].append([item['name'], item['unit'], item['group']])
        mk = MARKETS.get(item['group'], MARKETS['default'])
        row = [(item[kind].get(m) or [None, None])[1] for kind, m in mk]
        i = index[item['name']]
        vals.extend([None] * (i + 1 - len(vals)))
        vals[i] = row
    notes = [[n['item'], n['market'], n['from'], n['to'], n['dir'], n['text']] for n in rep['notes']]
    return {'v': vals, 'n': notes}


def expand(day, rec, arch, prev=None):
    """Rebuild a full report (with yesterday's prices from the previous report)."""
    items = []
    for i, row in enumerate(rec['v']):
        if not row:
            continue
        name, unit, group = arch['items'][i]
        mk = MARKETS.get(group, MARKETS['default'])
        p = prev['v'][i] if prev and i < len(prev['v']) and prev['v'][i] else [None] * len(mk)
        item = {'name': name, 'unit': unit, 'group': group, 'wholesale': {}, 'retail': {}}
        for k, (kind, m) in enumerate(mk):
            if row[k] is not None or p[k] is not None:
                item[kind][m] = [p[k], row[k]]
        items.append(item)
    notes = [dict(zip(('item', 'market', 'from', 'to', 'dir', 'text'), n)) for n in rec['n']]
    return {'date': day, 'items': items, 'notes': notes}


def main():
    os.makedirs(ARCH, exist_ok=True)
    years = {}

    def arch_for(day):
        y = day[:4]
        if y not in years:
            years[y] = load_year(y)
        return years[y]

    # One-time migration: fold old per-day files (YYYY-MM-DD.json) into the yearly file
    for f in sorted(os.listdir(ARCH)):
        if re.match(r'\d{4}-\d{2}-\d{2}\.json$', f):
            rep = json.load(open(os.path.join(ARCH, f), encoding='utf-8'))
            arch_for(rep['date'])['days'][rep['date']] = compact(rep, arch_for(rep['date']))
            os.remove(os.path.join(ARCH, f))
    for f in os.listdir(ARCH):
        if re.match(r'\d{4}\.json$', f):
            arch_for(f[:4] + '-')

    have = {d for a in years.values() for d in a['days']}
    page = curl(LIST_URL) or ''
    days = {f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in re.findall(r'price_report_(\d{8})_e\.pdf', page)}
    if '--backfill' in sys.argv:
        n = int(sys.argv[sys.argv.index('--backfill') + 1])
        today = dt.datetime.now(COLOMBO).date()
        days |= {(today - dt.timedelta(days=i)).isoformat() for i in range(n)
                 if (today - dt.timedelta(days=i)).weekday() < 5}
    limit = int(sys.argv[sys.argv.index('--max') + 1]) if '--max' in sys.argv else 40
    new = sorted(days - have)
    print(f'prices: {len(days)} report dates seen, {len(new)} new')
    for day in new[-limit:]:
        pdf = curl(PDF_URL.format(day.replace('-', '')), binary=True)
        if not pdf or not pdf.startswith(b'%PDF'):
            continue  # holidays have no report
        try:
            rep = parse_report(pdf, day)
        except Exception as e:  # a malformed PDF must not stop the run
            print(f'  {day}: parse error {e}')
            continue
        if not rep:
            print(f'  {day}: table not recognised')
            continue
        arch_for(day)['days'][day] = compact(rep, arch_for(day))
        print(f'  {day}: {len(rep["items"])} items, {len(rep["notes"])} notes')

    for y, a in years.items():
        a['days'] = dict(sorted(a['days'].items()))
        json.dump(a, open(year_path(y), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))

    all_days = sorted((d, y) for y, a in years.items() for d in a['days'])
    if not all_days:
        print('prices: nothing archived; keeping previous file')
        return
    latest_day, ly = all_days[-1]
    prev = all_days[-2] if len(all_days) > 1 else None
    latest = expand(latest_day, years[ly]['days'][latest_day], years[ly],
                    years[prev[1]]['days'][prev[0]] if prev else None)

    # Basket history: one shared date axis, one value list per food (Colombo retail)
    span = all_days[-HISTORY_DAYS:]
    series = {n: [] for n in BASKET}
    for d, y in span:
        rep = expand(d, years[y]['days'][d], years[y])
        got = {it['name']: consumer_price(it) for it in rep['items']}
        for n in BASKET:
            series[n].append(got.get(n))
    payload = {
        'source': 'Central Bank of Sri Lanka — Daily Price Report',
        'url': LIST_URL,
        'date': latest['date'],
        'items': latest['items'],
        'notes': latest['notes'],
        'history': {'dates': [d for d, _ in span], 'series': {k: v for k, v in series.items() if any(x is not None for x in v)}},
    }
    path = os.path.join(DATA, 'prices.json')
    if os.path.exists(path):
        old = json.load(open(path, encoding='utf-8'))
        old.pop('generated', None)
        if old == payload:
            print('prices.json: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'prices.json: updated ({os.path.getsize(path)} bytes, report {latest["date"]})')


if __name__ == '__main__':
    main()
