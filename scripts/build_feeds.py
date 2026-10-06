"""Hourly CeylonPulse feeds -> data/fuel.json, fuel-history.json, reservoirs.json, rivers.json, fires.json

Sources that block browser proxies but answer a normal server request:
  * Fuel prices: Ceylon Petroleum Corporation (ceypetco.gov.lk), plus its price
    revisions since 2015 (historical-prices page)
  * Major reservoir storage: Irrigation Department daily sheet (Water Management Branch)
  * River gauges: Irrigation Department hydrometric network (ArcGIS feature service)
  * Fire detections: NASA FIRMS VIIRS (key from the FIRMS_KEY secret)

Each file is rewritten only when its data changes, so `generated` is the time
the data last changed. If a source fails or looks wrong, the previous file is
kept. Standard library only.
"""
import csv, datetime as dt, html, io, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))

FUEL_URL = 'https://ceypetco.gov.lk/marketing-sales/'
FUEL_HISTORY_URL = 'https://ceypetco.gov.lk/historical-prices/'
RES_SHEET = ('https://docs.google.com/spreadsheets/d/e/2PACX-1vTcSGhi9RESl7CMCl1TQnrKe07Gx5Q696YiSB9jneIHqIP9lifpqSErgI3D5k9KtQXSdW5JpycIIr5e/'
             'pub?output=csv')
GAUGES = 'https://services3.arcgis.com/J7ZFXmR8rSmQ3FGf/arcgis/rest/services/gauges_2_view/FeatureServer/0/query'
FIRMS_BBOX, FIRMS_DAYS = '79.4,5.7,82.1,10.0', 3


def get(url, tries=3, timeout=60):
    """Fetch with curl: it follows Google's published-sheet redirect as-is
    (urllib re-quotes it and gets HTTP 400). curl is on every Actions runner."""
    for i in range(tries):
        r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode('utf-8', errors='replace')
        print(f'  retry {i + 1} {url[:70]}: {r.stderr.decode(errors="replace").strip()[:120]}')
        time.sleep(10 * (i + 1))
    return None


def num(s):
    s = re.sub(r'[^\d.\-]', '', s or '')
    try:
        return float(s) if s not in ('', '-', '.') else None
    except ValueError:
        return None


def save_if_changed(name, payload):
    """Write data/<name> only when the payload (ignoring `generated`) changed."""
    path = os.path.join(DATA, name)
    if os.path.exists(path):
        old = json.load(open(path, encoding='utf-8'))
        old.pop('generated', None)
        if old == payload:
            print(f'{name}: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'{name}: updated ({os.path.getsize(path)} bytes)')


# ── Fuel prices ────────────────────────────────────────────────────
def fuel():
    page = get(FUEL_URL)
    if not page:
        return None
    t = re.sub(r'(?is)<(script|style).*?</\1>', ' ', page)
    text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
    items = re.findall(r'(Lanka [A-Za-z0-9 .()\-]+?)\s+(?:White|Black) Oil\s+Rs\.\s*([\d,]+\.\d+)\s+per Ltr'
                       r'(?:\s+\S*\s*Effect from:\s*(\d{2}-\d{2}-\d{4}))?', text)
    # A name can swallow preceding page text; keep only the last "Lanka ..." part
    products = [{'name': 'Lanka ' + n.rsplit('Lanka ', 1)[-1].strip(), 'price': num(p), 'effective': e or None}
                for n, p, e in items]
    by = {p['name']: p for p in products}

    def pick(*names):
        for n in names:
            if n in by:
                return by[n]['price']
        return None
    out = {
        'source': 'Ceylon Petroleum Corporation (ceypetco.gov.lk)', 'url': FUEL_URL, 'live': True,
        'petrol92': pick('Lanka Petrol 92 Octane'), 'petrol95': pick('Lanka Petrol 95 Octane Euro 4'),
        'diesel': pick('Lanka Auto Diesel'), 'superDiesel': pick('Lanka Super Diesel 4 Star Euro 4'),
        'kerosene': pick('Lanka Kerosene'), 'products': products,
    }
    # Sanity check before publishing: the main grades must be present and plausible
    if not all(out[k] and 100 < out[k] < 2000 for k in ('petrol92', 'petrol95', 'diesel')):
        print('fuel: page layout changed or prices implausible; keeping previous file')
        return None
    return out


# ── Fuel price history (CPC revisions since 2015) ─────────────
FUEL_COLS = {'LP 95': 'petrol95', 'LP 92': 'petrol92', 'LAD': 'diesel', 'LSD': 'superDiesel', 'LK': 'kerosene'}


def fuel_history():
    page = get(FUEL_HISTORY_URL)
    if not page:
        return None
    t = re.sub(r'(?is)<(script|style).*?</\1>', ' ', page)
    cell = lambda c: re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', c))).strip()
    for table in re.findall(r'(?is)<table.*?</table>', t):
        rows = [[cell(c) for c in re.findall(r'(?is)<t[hd].*?</t[hd]>', r)] for r in re.findall(r'(?is)<tr.*?</tr>', table)]
        if not rows or rows[0][:3] != ['Date', 'LP 95', 'LP 92']:
            continue  # the second table is the pre-2006 per-circular list
        idx = {k: rows[0].index(k) for k in FUEL_COLS if k in rows[0]}
        out = []
        for r in rows[1:]:
            m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})$', r[0])
            if not m:
                continue
            out.append([f'{m.group(3)}-{m.group(2)}-{m.group(1)}'] + [num(r[idx[k]]) if k in idx and idx[k] < len(r) else None for k in FUEL_COLS])
        out.sort()
        # Keep 2015 onwards, plus the revision in force on 1 Jan 2015
        before = [r for r in out if r[0] < '2015-01-01']
        out = before[-1:] + [r for r in out if r[0] >= '2015-01-01']
        if len(out) < 20:
            return None  # table layout changed; keep the previous file
        return {'source': 'Ceylon Petroleum Corporation — historical prices', 'url': FUEL_HISTORY_URL, 'units': 'LKR per litre',
                'columns': ['date'] + list(FUEL_COLS.values()), 'revisions': out}
    return None


# ── Major reservoirs (Irrigation Department daily sheet) ───────────
def reservoirs():
    text = get(RES_SHEET)
    if not text:
        return None
    rows = list(csv.reader(io.StringIO(text)))
    hi = next((i for i, r in enumerate(rows) if len(r) > 2 and r[1].strip().upper() == 'RESERVOIR'), None)
    if hi is None:
        print('reservoirs: header row not found')
        return None
    head = [re.sub(r'\s+', ' ', h).strip().upper() for h in rows[hi]]

    def col(*keys):
        for k in keys:
            for j, h in enumerate(head):
                if h.startswith(k):
                    return j
        return None
    C = {k: col(*v) for k, v in {
        'name': ['RESERVOIR'], 'range': ['RANGE'], 'fsd': ['FSD'], 'cap': ['GROSS CAPACITY'], 'dead': ['DEAD STORAGE'],
        'date': ['DATE'], 'depth': ['WATER DEPTH'], 'gross': ['GROSS STORAGE'], 'eff': ['EFFECTIVE STORAGE (ACFT)', 'EFFECTIVE STORAGE'],
        'pct': ['EFFECTIVE STORAGE %'], 'rain': ['RAIN FALL', 'RAINFALL'], 'spill': ['SPILLING'], 'district': ['DISTRICT'],
        'division': ['DIVISION'], 'sluice': ['TOTAL SLUICE'], 'spillq': ['SPILLING (CUSEC)'],
    }.items()}
    # "EFFECTIVE STORAGE %" also starts with "EFFECTIVE STORAGE": find the % column explicitly
    C['pct'] = next((j for j, h in enumerate(head) if h.startswith('EFFECTIVE STORAGE') and '%' in h), C['pct'])
    C['eff'] = next((j for j, h in enumerate(head) if h.startswith('EFFECTIVE STORAGE') and '%' not in h), C['eff'])
    C['spill'] = next((j for j, h in enumerate(head) if h == 'SPILLING'), C['spill'])

    cell = lambda r, k: r[C[k]].strip() if C[k] is not None and C[k] < len(r) else ''
    res = []
    for r in rows[hi + 1:]:
        if not r or not r[0].strip().isdigit():
            if any('SUMMARY' in c.upper() for c in r):
                break
            continue
        pct = num(cell(r, 'pct'))
        res.append({
            'name': cell(r, 'name').title() if cell(r, 'name').isupper() else cell(r, 'name'),
            'range': cell(r, 'range').title(), 'district': cell(r, 'district'),
            'fsdFt': num(cell(r, 'fsd')), 'capacityAcft': num(cell(r, 'cap')), 'depthFt': num(cell(r, 'depth')),
            'effectiveAcft': num(cell(r, 'eff')), 'pct': pct, 'rainMm': num(cell(r, 'rain')),
            'spilling': cell(r, 'spill').lower().startswith('y'), 'date': cell(r, 'date'),
        })
    # Island total from the summary block ("TOTAL", tanks, gross, dead, present, effective, %)
    total = None
    for r in rows:
        if len(r) > 7 and r[1].strip().upper() == 'TOTAL':
            total = {'tanks': num(r[2]), 'grossAcft': num(r[3]), 'presentAcft': num(r[5]), 'effectiveAcft': num(r[6]), 'pct': num(r[7])}
    as_of = next((c.strip() for r in rows[:hi] for c in r if re.match(r'\d{1,2}-[A-Za-z]+-\d{4}$', c.strip())), None)
    if len(res) < 50 or sum(1 for x in res if x['pct'] is not None) < 40:
        print(f'reservoirs: only {len(res)} rows parsed; keeping previous file')
        return None
    return {'source': 'Irrigation Department, Water Management Branch — daily water level & storage of major reservoirs',
            'url': 'https://irrigation.gov.lk/web/index.php?option=com_content&view=article&id=84&Itemid=201&lang=en',
            'asOf': as_of, 'total': total, 'reservoirs': res}


# ── River gauges (Irrigation Department hydrometric network) ───────
def rivers():
    q = GAUGES + '?where=1%3D1&outFields=basin,gauge,water_level,rain_fall,CreationDate,alertpull,minorpull,majorpull' \
                 '&orderByFields=CreationDate+DESC&resultRecordCount=2000&returnGeometry=true&f=json'
    raw = get(q)
    try:
        feats = json.loads(raw)['features'] if raw else []
    except (ValueError, KeyError):
        feats = []
    if not feats:
        return None
    latest, prev = {}, {}
    for f in feats:  # newest first
        a = f['attributes']
        key = (a.get('basin'), a.get('gauge'))
        if key not in latest:
            latest[key] = f
        elif key not in prev:
            prev[key] = a
    cutoff = time.time() * 1000 - 3 * 86400 * 1000  # ignore stations silent for 3+ days
    out = []
    for key, f in latest.items():
        a, g = f['attributes'], f.get('geometry') or {}
        if not a.get('CreationDate') or a['CreationDate'] < cutoff or a.get('water_level') is None:
            continue
        lvl, al, mi, ma = a['water_level'], a.get('alertpull'), a.get('minorpull'), a.get('majorpull')
        status = ('major' if ma and lvl >= ma else 'minor' if mi and lvl >= mi else 'alert' if al and lvl >= al else 'normal')
        p = prev.get(key)
        out.append({
            'basin': a.get('basin'), 'gauge': a.get('gauge'), 'level': lvl, 'rainMm': a.get('rain_fall'),
            'time': dt.datetime.fromtimestamp(a['CreationDate'] / 1000, dt.timezone.utc).isoformat(timespec='minutes'),
            'alert': al, 'minor': mi, 'major': ma, 'status': status,
            'prevLevel': p.get('water_level') if p else None,
            'lat': round(g['y'], 4) if 'y' in g else None, 'lon': round(g['x'], 4) if 'x' in g else None,
        })
    order = {'major': 0, 'minor': 1, 'alert': 2, 'normal': 3}
    out.sort(key=lambda x: (order[x['status']], x['basin'] or '', x['gauge'] or ''))
    if not out:
        return None
    return {'source': 'Irrigation Department hydrometric stations (manual & automatic river gauges)',
            'url': 'https://irrigation.gov.lk/web/index.php?option=com_content&view=article&id=188&Itemid=201&lang=en',
            'units': 'm', 'gauges': out}


# ── NASA FIRMS fires ───────────────────────────────────────────────
def fires():
    key = os.environ.get('FIRMS_KEY')
    if not key:
        print('fires: FIRMS_KEY not set; skipping')
        return None
    allf, ok = [], False
    for src in ('VIIRS_NOAA20_NRT', 'VIIRS_SNPP_NRT'):
        text = get(f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{FIRMS_BBOX}/{FIRMS_DAYS}')
        if text is None or not text.startswith('latitude'):
            continue
        ok = True
        for r in csv.DictReader(io.StringIO(text)):
            try:
                allf.append({'lat': float(r['latitude']), 'lon': float(r['longitude']), 'brightness': float(r['bright_ti4']),
                             'date': r['acq_date'], 'time': r['acq_time'].zfill(4), 'satellite': r['satellite'],
                             'confidence': r['confidence'], 'frp': float(r['frp']), 'daynight': r['daynight']})
            except (KeyError, ValueError):
                pass
    if not ok:
        return None
    allf.sort(key=lambda f: f['date'] + f['time'], reverse=True)
    return {'fires': allf, 'days': FIRMS_DAYS, 'source': 'NASA FIRMS · VIIRS 375 m (NOAA-20 + Suomi-NPP)'}


def main():
    os.makedirs(DATA, exist_ok=True)
    failed = []
    for name, fn in (('fuel.json', fuel), ('fuel-history.json', fuel_history), ('reservoirs.json', reservoirs), ('rivers.json', rivers), ('fires.json', fires)):
        try:
            payload = fn()
        except Exception as e:
            print(f'{name}: error {e}')
            payload = None
        if payload:
            save_if_changed(name, payload)
            if name == 'reservoirs.json':
                # One copy per day builds the storage history behind the trend charts
                arch = os.path.join(ROOT, 'archive', 'reservoirs')
                os.makedirs(arch, exist_ok=True)
                day = dt.datetime.now(COLOMBO).date().isoformat()
                json.dump(payload, open(os.path.join(arch, f'{day}.json'), 'w', encoding='utf-8'),
                          ensure_ascii=False, separators=(',', ':'))
        elif name != 'fires.json' or os.environ.get('FIRMS_KEY'):
            failed.append(name)
    if failed:
        print('Kept previous files for:', ', '.join(failed))
        sys.exit(1)  # mark the run failed so a broken source is noticed


if __name__ == '__main__':
    main()
