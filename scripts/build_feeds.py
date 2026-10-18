"""Daily CeylonPulse feeds -> data/fuel.json, fuel-history.json, reservoirs.json, rivers.json, cse.json, fires.json

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

import health

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))

FUEL_URL = 'https://ceypetco.gov.lk/marketing-sales/'
FUEL_HISTORY_URL = 'https://ceypetco.gov.lk/marketing-sales/historical-prices'
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
        err = r.stderr.decode(errors='replace').strip()
        key = os.environ.get('FIRMS_KEY')
        if key:  # a truncated key slips past Actions' secret masking: redact before cutting
            url, err = url.replace(key, '***'), err.replace(key, '***')
        print(f'  retry {i + 1} {url[:70]}: {err[:120]}')
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
        old.pop('checked', None)  # set by scripts/local_refresh.py
        if old == payload:
            print(f'{name}: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'{name}: updated ({os.path.getsize(path)} bytes)')


# ── Fuel prices ────────────────────────────────────────────────────
# Since Oct 2026 ceypetco.gov.lk is a JavaScript app; its public JSON API holds the prices
FUEL_API = 'https://api.ceypetco.gov.lk/api/admin/'


def fuel_api(path):
    raw = get(FUEL_API + path, tries=2, timeout=30)
    data = json.loads(raw).get('data') if raw else None
    return data if isinstance(data, list) else None


def fuel():
    rows = fuel_api('fuel-prices/active')
    if rows is None:
        return None
    products = [{'name': r['product'].strip(), 'price': float(r['price']),
                 'effective': '-'.join(reversed(r['effectiveDate'][:10].split('-'))) if r.get('effectiveDate') else None}
                for r in rows if r.get('status', 'active') == 'active' and r.get('product') and r.get('price') is not None]
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
        print('fuel: product names changed or prices implausible; keeping previous file')
        return None
    return out


# ── Fuel price history (CPC revisions since 2015) ─────────────
# API rows hold values in the order LP 95, LP 92, LAD, LSD, LK, LIK, FUR 800, FUR 1500 (High), (Low)
FUEL_COLS = {'LP 95': 'petrol95', 'LP 92': 'petrol92', 'LAD': 'diesel', 'LSD': 'superDiesel', 'LK': 'kerosene'}


def fuel_history(current=None):
    rows = fuel_api('historical-prices/active')
    if rows is None:
        return None
    out = {}
    for r in rows:
        m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', r.get('dateLabel', ''))
        if r.get('kind') != 'fuel' or r.get('status', 'active') != 'active' or not m:
            continue
        out[f'{m.group(3)}-{m.group(2)}-{m.group(1)}'] = [num(v) for v in (r.get('values') or [])[:len(FUEL_COLS)]]
    if len(out) < 20:
        return None  # API changed; keep the previous file
    # CPC adds revisions to this list days after they take effect: keep ones already published
    path = os.path.join(DATA, 'fuel-history.json')
    if os.path.exists(path):
        last = max(out)
        for r in json.load(open(path, encoding='utf-8')).get('revisions', []):
            if r[0] > last and r[1:] != out[last]:
                out[r[0]] = r[1:]
    revs = sorted([d] + v for d, v in out.items())
    # ...and today's prices, if they are a revision the list does not have yet
    if current and current.get('petrol92'):
        now = [current.get(k) for k in FUEL_COLS.values()]
        eff = max((p['effective'] for p in current['products'] if p['effective']),
                  key=lambda e: e[6:] + e[3:5] + e[:2], default=None)
        if eff and now != revs[-1][1:]:
            revs.append(['-'.join(reversed(eff.split('-')))] + now)
            revs.sort()
    # Keep 2015 onwards, plus the revision in force on 1 Jan 2015
    before = [r for r in revs if r[0] < '2015-01-01']
    revs = before[-1:] + [r for r in revs if r[0] >= '2015-01-01']
    return {'source': 'Ceylon Petroleum Corporation — historical prices', 'url': FUEL_HISTORY_URL, 'units': 'LKR per litre',
            'columns': ['date'] + list(FUEL_COLS.values()), 'revisions': revs}


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


# ── Colombo Stock Exchange indices (ASPI, S&P SL20) ─────────────────
CSE_API = 'https://www.cse.lk/api/'


def cse_post(path, body=''):
    r = subprocess.run(['curl', '-sS', '--fail', '-m', '40', '-A', UA, '-X', 'POST',
                        '-H', 'Content-Type: application/x-www-form-urlencoded', '-d', body, CSE_API + path], capture_output=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
    except ValueError:
        return None


def cse():
    """Latest ASPI and S&P SL20 plus one year of daily closes (TradingView has no SL20 feed)."""
    out = {}
    for key, latest, chart_id in (('aspi', 'aspiData', 1), ('sl20', 'snpData', 40)):
        now = cse_post(latest)
        hist = cse_post('chartData', f'chartId={chart_id}&period=5')
        if not now or not now.get('value'):
            return None
        series = [[dt.datetime.fromtimestamp(p['d'] / 1000, COLOMBO).date().isoformat(), p['v']] for p in (hist or []) if p.get('v')]
        out[key] = {
            'value': now['value'], 'change': now.get('change'), 'pct': round(now.get('percentage') or 0, 2),
            'high': now.get('highValue'), 'low': now.get('lowValue'),
            'time': dt.datetime.fromtimestamp(now['timestamp'] / 1000, dt.timezone.utc).isoformat(timespec='minutes'),
            'history': series[-260:],
        }
    return {'source': 'Colombo Stock Exchange', 'url': 'https://www.cse.lk/', **out}


# ── NASA FIRMS fires ───────────────────────────────────────────────
def fires():
    key = os.environ.get('FIRMS_KEY')
    if not key:
        print('fires: FIRMS_KEY not set; skipping')
        return None
    allf, ok = [], False
    for src in ('VIIRS_NOAA20_NRT', 'VIIRS_SNPP_NRT'):
        text = get(f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{FIRMS_BBOX}/{FIRMS_DAYS}',
                   tries=2, timeout=40)
        if text is None:
            break  # same server for both satellites: don't spend more of the job on a timeout
        if not text.startswith('latitude'):
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


# Hours a feed may keep failing (previous file kept, GitHub warning) before the run goes red.
# The workflow runs once a day, so 50 h = red on the third missed day in a row
MAX_HOURS = {'fuel.json': 50, 'fuel-history.json': 74, 'reservoirs.json': 50, 'rivers.json': 50,
             'cse.json': 74, 'fires.json': 50}
# Ceypetco's API only answers Sri Lankan connections: scripts/local_refresh.py uploads the fuel
# files from a home PC and stamps fuel.json `checked`; a copy that recent counts as up to date
LOCAL_OK_HOURS = 36


def local_copy_age(name):
    """Hours since the home PC last confirmed the fuel files, or None."""
    if name not in ('fuel.json', 'fuel-history.json'):
        return None
    try:
        checked = json.load(open(os.path.join(DATA, 'fuel.json'), encoding='utf-8'))['checked']
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(checked)).total_seconds() / 3600
    except (OSError, KeyError, ValueError):
        return None


def main():
    os.makedirs(DATA, exist_ok=True)
    results = {}
    for name, fn in (('fuel.json', fuel), ('fuel-history.json', fuel_history), ('reservoirs.json', reservoirs), ('rivers.json', rivers), ('cse.json', cse), ('fires.json', fires)):
        if name == 'fires.json' and not os.environ.get('FIRMS_KEY'):
            print('fires: FIRMS_KEY not set; skipping')
            continue
        error = 'source unavailable or page layout changed'
        try:
            payload = fn(results.get('fuel.json')) if fn is fuel_history else fn()
        except Exception as e:
            print(f'{name}: error {e}')
            payload, error = None, f'error {e}'
        if payload:
            results[name] = payload
            health.ok(name)
            save_if_changed(name, payload)
            if name == 'reservoirs.json':
                # One copy per day builds the storage history behind the trend charts
                arch = os.path.join(ROOT, 'archive', 'reservoirs')
                os.makedirs(arch, exist_ok=True)
                day = dt.datetime.now(COLOMBO).date().isoformat()
                json.dump(payload, open(os.path.join(arch, f'{day}.json'), 'w', encoding='utf-8'),
                          ensure_ascii=False, separators=(',', ':'))
        elif (age := local_copy_age(name)) is not None and age < LOCAL_OK_HOURS:
            print(f'{name}: unreachable from GitHub; home PC copy checked {age:.1f} h ago')
            health.ok(name)
        else:
            print(f'{name}: kept previous file')
            health.fail(name, error, MAX_HOURS[name])
    health.finish()  # exits 1 only when a feed has been failing longer than its limit

if __name__ == '__main__':
    main()
