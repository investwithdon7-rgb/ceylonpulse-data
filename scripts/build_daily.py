"""Daily CeylonPulse climate data -> data/enso.json, data/outlook-3m.json, data/status.json

Runs in GitHub Actions every morning (Sri Lanka time). Standard library only.
The output shapes match what js/elnino.js on the website expects, so the
browser can use these files instead of calling the APIs itself.
"""
import datetime as dt, html, json, os, re, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SEASONAL = 'https://seasonal-api.open-meteo.com/v1/seasonal'
CPC = 'https://www.cpc.ncep.noaa.gov/'
COLOMBO = dt.timezone(dt.timedelta(hours=5, minutes=30))
MIN_NORMAL_MM = 15  # below this a month is normally almost dry
UA = {'User-Agent': 'CeylonPulse-data (github.com/investwithdon7-rgb/ceylonpulse-data)'}


def get(url, as_json=True, tries=4, timeout=90):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                raw = r.read()
            if as_json:
                return json.loads(raw)
            for enc in ('utf-8', 'cp1252'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
            return raw.decode('latin-1')
        except Exception as e:
            print(f'  retry {i + 1} {url[:80]}: {e}')
            time.sleep(15 * (i + 1))
    return None


def today_colombo():
    return dt.datetime.now(COLOMBO).date()


# ── ENSO status (NOAA CPC) ─────────────────────────────────────────
def parse_index(text):
    rows = []
    for line in text.splitlines():
        m = re.match(r'^([A-Z]{3})\s+(\d{4})\s+(?:(-?\d+\.\d+)\s+)?(-?\d+\.\d+)$', line.strip())
        if m:
            rows.append([m.group(1), int(m.group(2)), float(m.group(4))])
    return rows


def parse_discussion(page):
    t = re.sub(r'(?is)<(script|style).*?</\1>', ' ', page)
    flat = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
    alert = re.search(r'ENSO Alert System Status:\s*(.+?)\s*Synopsis:', flat, re.I)
    syn = re.search(r'Synopsis:\s*(.+?\.)\s', flat, re.I)
    if not alert or not syn:
        return None
    body = flat[flat.index(syn.group(1)) + len(syn.group(1)):]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', body)
    issued = re.search(r'NCEP/NWS\s+(\d{1,2} [A-Z][a-z]+ \d{4})', flat)
    nxt = re.search(r'scheduled for (\d{1,2} [A-Z][a-z]+ \d{4})', flat)
    return {
        'issued': issued.group(1) if issued else '',
        'alert': alert.group(1).strip(),
        'synopsis': syn.group(1).strip(),
        'outlook': next((s for s in sentences if '% chance' in s and not s.startswith('In summary')), ''),
        'nextIssue': nxt.group(1) if nxt else '',
    }


def enso_status():
    oni_t = get(CPC + 'data/indices/oni.ascii.txt', False)
    roni_t = get(CPC + 'data/indices/RONI.ascii.txt', False)
    disc_t = get(CPC + 'products/analysis_monitoring/enso_advisory/ensodisc.shtml', False)
    oni = parse_index(oni_t or '')
    roni = parse_index(roni_t or '')
    disc = parse_discussion(disc_t) if disc_t else None
    if len(oni) <= 12 or len(roni) <= 12 or not disc:
        return None  # keep yesterday's file rather than publish partial data
    return {'source': 'live', 'asOf': today_colombo().isoformat(), 'oni': oni[-24:], 'roni': roni[-24:],
            'discussion': disc, 'discussionLive': True}


def enso_projection():
    lats, lons = [], []
    for la in (-5, 0, 5):
        for lo in (-170, -160, -150, -140, -130, -120):
            lats.append(la)
            lons.append(lo)
    raw = get(f'{SEASONAL}?latitude={",".join(map(str, lats))}&longitude={",".join(map(str, lons))}'
              f'&monthly=sea_surface_temperature_anomaly')
    if not isinstance(raw, list) or not raw[0].get('monthly', {}).get('time'):
        return None
    months = raw[0]['monthly']['time']
    anom = []
    for i in range(len(months)):
        vals = [p['monthly']['sea_surface_temperature_anomaly'][i] for p in raw
                if p['monthly']['sea_surface_temperature_anomaly'][i] is not None]
        anom.append(round(sum(vals) / len(vals), 2) if vals else None)
    peak = 0
    for i, v in enumerate(anom):
        if v is not None and v > (anom[peak] if anom[peak] is not None else -99):
            peak = i
    return {'months': months, 'anom': anom, 'peakIndex': peak}


# ── 3-month outlook: districts + reservoir catchments ──────────────
def tercile_prob(members, normal, thr):
    if not members or not thr or normal is None or normal < MIN_NORMAL_MM:
        return None
    below = sum(1 for v in members if v / normal < thr[0])
    above = sum(1 for v in members if v / normal > thr[1])
    n = len(members)
    return [round(below / n, 2), round((n - below - above) / n, 2), round(above / n, 2)]


def outlook_3m():
    C = json.load(open(os.path.join(ROOT, 'ref', 'lk-climate.json'), encoding='utf-8'))
    lat = ','.join(str(c[0]) for c in C['cells'])
    lon = ','.join(str(c[1]) for c in C['cells'])
    base = f'{SEASONAL}?latitude={lat}&longitude={lon}'
    m_arr = get(f'{base}&monthly=precipitation_mean,precipitation_anomaly,temperature_2m_anomaly')
    if not isinstance(m_arr, list) or len(m_arr) != len(C['cells']):
        return None
    times = m_arr[0]['monthly']['time']
    this_month = today_colombo().isoformat()[:7]
    idx = [i for i, t in enumerate(times) if t[:7] >= this_month][:3]
    if not idx:
        return None
    months = [times[i] for i in idx]
    month_nums = [int(t[5:7]) for t in months]
    last = dt.date.fromisoformat(months[-1])
    end = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    d_arr = get(f'{base}&daily=precipitation_sum&start_date={months[0]}&end_date={end.isoformat()}', timeout=180)
    d_arr = d_arr if isinstance(d_arr, list) else []

    cells = []
    for j, loc in enumerate(m_arr):
        m = loc['monthly']
        rain = [m['precipitation_mean'][i] for i in idx]
        anom = [m['precipitation_anomaly'][i] for i in idx]
        normal = [None if r is None or a is None else max(0, r - a) for r, a in zip(rain, anom)]
        temp = [m['temperature_2m_anomaly'][i] for i in idx]
        members = None
        dd = d_arr[j].get('daily') if j < len(d_arr) else None
        if dd and dd.get('time'):
            keys = sorted(k for k in dd if k.startswith('precipitation_sum'))
            members = []
            for n, mo in enumerate(months):
                pre = mo[:7]
                tots = [sum((v or 0) for t, v in zip(dd['time'], dd[k]) if t.startswith(pre)) for k in keys]
                mean = sum(tots) / len(tots)
                members.append([v * rain[n] / mean for v in tots] if rain[n] is not None and mean > 0.5 else None)
        cells.append({'rain': rain, 'normal': normal, 'temp': temp, 'members': members})
    has_prob = all(c['members'] for c in cells)

    rnd = lambda v: None if v is None else round(v)
    pct_of = lambda r, nm: None if r is None or nm is None or nm < MIN_NORMAL_MM else round((r - nm) / nm * 100)
    mem = lambda c, n: c['members'][n] if c['members'] else None

    districts = {}
    for k, ci in C['districts'].items():
        c = cells[ci]
        districts[k] = {
            'rain': [rnd(v) for v in c['rain']], 'normal': [rnd(v) for v in c['normal']],
            'pct': [pct_of(r, nm) for r, nm in zip(c['rain'], c['normal'])], 'temp': c['temp'],
            'prob': [tercile_prob(mem(c, n), c['normal'][n], C['cells'][ci][2][month_nums[n] - 1]) for n in range(len(months))],
        }

    avg = lambda a: sum(a) / len(a)
    catchments = []
    for ct in C['catchments']:
        cs = [cells[i] for i in ct['cells']]
        rain = [None if any(c['rain'][n] is None for c in cs) else avg([c['rain'][n] for c in cs]) for n in range(len(months))]
        normal = [None if any(c['normal'][n] is None for c in cs) else avg([c['normal'][n] for c in cs]) for n in range(len(months))]
        cm = []
        for n in range(len(months)):
            ms = [mem(c, n) for c in cs]
            cm.append([avg([m[r] for m in ms]) for r in range(len(ms[0]))] if has_prob and all(ms) else None)
        s_rain = None if None in rain else sum(rain)
        s_norm = None if None in normal else sum(normal)
        s_mem = [cm[0][r] + cm[1][r] + cm[2][r] for r in range(len(cm[0]))] if len(months) == 3 and all(cm) else None
        catchments.append({
            'key': ct['key'], 'name': ct['name'], 'reservoirs': ct['reservoirs'], 'lat': ct['lat'], 'lon': ct['lon'],
            'cellCount': len(ct['cells']),
            'rain': [rnd(v) for v in rain], 'normal': [rnd(v) for v in normal],
            'pct': [pct_of(r, nm) for r, nm in zip(rain, normal)],
            'prob': [tercile_prob(cm[n], normal[n], ct['mon'][month_nums[n] - 1]) for n in range(len(months))],
            'season': {'rain': rnd(s_rain), 'normal': rnd(s_norm), 'pct': pct_of(s_rain, s_norm),
                       'prob': tercile_prob(s_mem, s_norm, ct['s3'][month_nums[0] - 1])},
        })
    if not has_prob:
        return None  # the browser computes its own if the ensemble runs are missing
    runs = len(next(m for m in cells[0]['members'] if m))
    return {'months': months, 'districts': districts, 'catchments': catchments, 'hasProb': True, 'runs': runs}


def write(name, payload):
    payload = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', name, os.path.getsize(os.path.join(DATA, name)), 'bytes')


def main():
    os.makedirs(DATA, exist_ok=True)
    status_path = os.path.join(DATA, 'status.json')
    status = json.load(open(status_path, encoding='utf-8')) if os.path.exists(status_path) else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    ok = True

    st, proj = enso_status(), enso_projection()
    if st:
        write('enso.json', {'status': st, 'projection': proj})
        status['enso'] = now
    else:
        print('ENSO: NOAA unavailable, keeping previous file')
        ok = False

    o = outlook_3m()
    if o:
        write('outlook-3m.json', o)
        status['outlook-3m'] = now
        # Keep one snapshot per day so forecast skill can be checked later
        arch = os.path.join(ROOT, 'archive', 'outlook-3m')
        os.makedirs(arch, exist_ok=True)
        json.dump(o, open(os.path.join(arch, f'{today_colombo().isoformat()}.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
    else:
        print('Outlook: seasonal API unavailable, keeping previous file')
        ok = False

    status['lastRun'] = now
    json.dump(status, open(status_path, 'w', encoding='utf-8'), indent=1)
    if not ok:
        raise SystemExit(1)  # mark the Actions run as failed so it is noticed


if __name__ == '__main__':
    main()
