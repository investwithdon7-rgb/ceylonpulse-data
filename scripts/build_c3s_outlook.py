"""Monthly multi-model rain outlook from Copernicus C3S -> data/c3s-outlook.json

Run once a month after the 13th (when C3S publishes new forecasts):
    python tools/build_c3s_outlook.py
then upload data/c3s-outlook.json to the site.

Needs a free Copernicus CDS account. In GitHub Actions the token comes from
the CDSAPI_KEY secret (written to ~/.cdsapirc by the workflow); it is never
stored in the repo or the website. Packages: pip install -r requirements.txt

Method (standard C3S practice): for each forecasting system, monthly rain
for every ensemble member is interpolated to district centroids and
catchment sample points, then compared with the terciles of that same
system's 1993-2016 hindcasts for the same start month and lead. The
multi-model chance is the plain average of the systems' chances.
"""
import calendar, concurrent.futures as cf, datetime as dt, json, os, sys, time, urllib.request

import cdsapi
import h5py
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, '.c3s-cache')
OUT = os.path.join(ROOT, 'data', 'c3s-outlook.json')
DATASET = 'seasonal-monthly-single-levels'
AREA = [10.5, 79, 5.5, 82.5]  # N, W, S, E — Sri Lanka plus a margin
HC_YEARS = range(1993, 2017)  # common C3S hindcast period
CENTRES = {'ecmwf': 'ECMWF (Europe)', 'ukmo': 'UK Met Office', 'meteo_france': 'Météo-France', 'dwd': 'DWD (Germany)',
           'cmcc': 'CMCC (Italy)', 'ncep': 'NCEP (USA)', 'jma': 'JMA (Japan)', 'eccc': 'ECCC (Canada)', 'bom': 'BoM (Australia)'}


def token():
    for line in open(os.path.expanduser('~/.cdsapirc'), encoding='utf-8'):
        if line.startswith('key:'):
            return line.split(':', 1)[1].strip()
    raise SystemExit('No key in ~/.cdsapirc')


def constraints(inputs, tries=4):
    req = urllib.request.Request(
        f'https://cds.climate.copernicus.eu/api/retrieve/v1/processes/{DATASET}/constraints',
        data=json.dumps({'inputs': inputs}).encode(), method='POST',
        headers={'PRIVATE-TOKEN': token(), 'Content-Type': 'application/json'})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))  # CDS returns brief 502s under load


def latest_init():
    """Newest start month that has forecasts (C3S publishes on the 13th)."""
    d = dt.date.today().replace(day=1)
    for _ in range(3):
        if constraints({'originating_centre': ['ecmwf'], 'year': [str(d.year)], 'month': [f'{d.month:02d}'],
                        'product_type': ['monthly_mean']}).get('system'):
            return d
        d = (d - dt.timedelta(days=1)).replace(day=1)
    raise SystemExit('No recent C3S forecast found')


def systems_for(init):
    out = []
    for c in CENTRES:
        try:
            got = constraints({'originating_centre': [c], 'year': [str(init.year)], 'month': [f'{init.month:02d}'],
                               'product_type': ['monthly_mean']}).get('system', [])
        except Exception as e:
            print(f'  {c}: constraints failed ({e})')
            got = []
        out += [(c, s) for s in got]
    return out


def fetch(centre, system, years, month, leads, kind):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f'{centre}_{system}_{kind}_{years[0]}-{years[-1]}_m{month:02d}.nc')
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    cdsapi.Client(quiet=True, progress=False).retrieve(DATASET, {
        'originating_centre': centre, 'system': system, 'variable': ['total_precipitation'],
        'product_type': ['monthly_mean'], 'year': [str(y) for y in years], 'month': [f'{month:02d}'],
        'leadtime_month': [str(l) for l in leads], 'data_format': 'netcdf', 'area': AREA,
    }).download(path)
    return path


def read_mm(path, init_month_of_year, leads, years):
    """-> array [year, member, lead, lat, lon] of monthly rain in mm, plus lat, lon."""
    f = h5py.File(path, 'r')
    var = f['tprate']
    a = var[...].astype('float64')
    fill = var.attrs.get('_FillValue')
    if fill is not None:
        a[a == fill] = np.nan
    lat, lon = f['latitude'][...], f['longitude'][...]
    # Lagged-start systems (UKMO, NCEP, JMA) name the start axis indexing_time
    ref = f['forecast_reference_time'] if 'forecast_reference_time' in f else f['indexing_time']
    nmem, nref, nlead = len(f['number']), len(ref), len(f['forecastMonth'])
    assert a.shape == (nmem, nref, nlead, len(lat), len(lon)), a.shape
    a = np.transpose(a, (1, 0, 2, 3, 4))  # -> ref, member, lead, lat, lon
    # rate (m/s) -> monthly total (mm), using each lead's valid month length
    for li, lead in enumerate(f['forecastMonth'][...]):
        for yi, y in enumerate(years):
            m0 = init_month_of_year + int(lead) - 1
            vy, vm = y + (m0 - 1) // 12, (m0 - 1) % 12 + 1
            a[yi, :, li] *= calendar.monthrange(vy, vm)[1] * 86400 * 1000
    f.close()
    return a, lat, lon


def bilinear(field, lat, lon, la, lo):
    """field [..., lat, lon] on a regular grid -> value at (la, lo)."""
    i = np.searchsorted(-lat, -la) - 1  # lat is descending
    j = np.searchsorted(lon, lo) - 1
    i, j = min(max(i, 0), len(lat) - 2), min(max(j, 0), len(lon) - 2)
    ty = (la - lat[i]) / (lat[i + 1] - lat[i])
    tx = (lo - lon[j]) / (lon[j + 1] - lon[j])
    return ((1 - ty) * (1 - tx) * field[..., i, j] + (1 - ty) * tx * field[..., i, j + 1] +
            ty * (1 - tx) * field[..., i + 1, j] + ty * tx * field[..., i + 1, j + 1])


def tercile_prob(fc, hc):
    """fc: member values; hc: hindcast values -> [below, normal, above] or None."""
    fc, hc = fc[~np.isnan(fc)], hc[~np.isnan(hc)]
    if len(fc) < 5 or len(hc) < 30:
        return None
    lo, hi = np.quantile(hc, [1 / 3, 2 / 3])
    b, a = np.mean(fc < lo), np.mean(fc > hi)
    return [float(b), float(1 - a - b), float(a)]


def main():
    init = latest_init()
    today = dt.date.today()
    # Show the current month and the next two (as the site does)
    want = []
    for k in range(3):
        m0 = today.month + k
        want.append(dt.date(today.year + (m0 - 1) // 12, (m0 - 1) % 12 + 1, 1))
    leads = [(w.year - init.year) * 12 + w.month - init.month + 1 for w in want]
    print('init', init, 'valid', [w.isoformat()[:7] for w in want], 'leads', leads)
    systems = systems_for(init)
    print('systems', systems)

    ref = json.load(open(os.path.join(ROOT, 'ref', 'lk-points.json'), encoding='utf-8'))
    pts = {f'd:{k}': [tuple(v)] for k, v in ref['districts'].items()}
    pts.update({f"c:{c['key']}": [tuple(p) for p in c['points']] for c in ref['catchments']})

    def run(cs):
        c, s = cs
        hy = [y for y in HC_YEARS]
        fpath = fetch(c, s, [init.year], init.month, leads, 'fc')
        hpath = fetch(c, s, hy, init.month, leads, 'hc')
        fc, lat, lon = read_mm(fpath, init.month, leads, [init.year])
        hc, hlat, hlon = read_mm(hpath, init.month, leads, hy)
        res = {}
        for key, plist in pts.items():
            fv = np.mean([bilinear(fc[0], lat, lon, la, lo) for la, lo in plist], axis=0)    # member, lead
            hv = np.mean([bilinear(hc, hlat, hlon, la, lo) for la, lo in plist], axis=0)     # year, member, lead
            mon = [tercile_prob(fv[:, i], hv[:, :, i].ravel()) for i in range(len(leads))]
            s3 = tercile_prob(fv.sum(axis=1), hv.sum(axis=2).ravel()) if len(leads) == 3 else None
            res[key] = {'mon': mon, 's3': s3}
        members = int(np.sum(~np.isnan(fc[0, :, 0, 0, 0])))
        print(f'  {c} {s}: done ({members} members)')
        return {'id': f'{c}-{s}', 'name': f'{CENTRES[c]} system {s}', 'members': members, 'res': res}

    models = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for fut in cf.as_completed({ex.submit(run, cs): cs for cs in systems}):
            try:
                models.append(fut.result())
            except Exception as e:
                print('  model failed:', e)
    if len(models) < 3:
        raise SystemExit(f'Only {len(models)} models succeeded; not writing output')
    models.sort(key=lambda m: m['id'])

    def combine(key):
        def mm(get):
            ps = [get(m['res'][key]) for m in models]
            ps = [p for p in ps if p]
            if not ps:
                return None, None
            mean = [round(float(v), 2) for v in np.mean(ps, axis=0)]
            agree = [0, 0, 0]  # models whose most likely category is below / normal / above
            for p in ps:
                agree[int(np.argmax(p))] += 1
            return mean, agree
        mon = [mm(lambda r, i=i: r['mon'][i]) for i in range(len(leads))]
        s3 = mm(lambda r: r['s3'])
        return {'p': [m[0] for m in mon], 'agree': [m[1] for m in mon], 's3': s3[0], 's3agree': s3[1]}

    out = {
        'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'built': today.isoformat(),
        'init': init.isoformat()[:7],
        'months': [w.isoformat() for w in want],
        'hindcast': f'{HC_YEARS[0]}-{HC_YEARS[-1]}',
        'models': [{'id': m['id'], 'name': m['name'], 'members': m['members']} for m in models],
        'districts': {k[2:]: combine(k) for k in pts if k.startswith('d:')},
        'catchments': {k[2:]: combine(k) for k in pts if k.startswith('c:')},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('wrote', OUT, os.path.getsize(OUT), 'bytes,', len(models), 'models')


if __name__ == '__main__':
    main()
