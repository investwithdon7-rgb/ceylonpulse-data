"""Country comparison series -> data/compare.json

Sri Lanka against regional and aspirational peers, one block per metric:
  gdppc      GDP per capita, current USD
  gdppc_ppp  GDP per capita, PPP (current international $)
  growth     real GDP growth, %
  inflation  consumer price inflation, %
  fdi        foreign direct investment, net inflows, % of GDP

Actual values come from the World Bank API. Years after a country's last World
Bank figure come from the IMF World Economic Outlook (datamapper API) and are
flagged as projections. Per-capita levels are chained: the IMF's projected change
is applied to the latest World Bank level, so the line does not jump where the
two sources' level estimates differ. Rates (growth, inflation) are used as-is.
The WEO currently publishes no projections for some countries (Sri Lanka among
them); those simply have no projected points.

Sri Lanka's inflation uses the official Colombo CPI (data/ccpi.json, annual
average of the monthly index) wherever it covers a full year: the World Bank's
FP.CPI.TOTL.ZG for LKA departs from it (2025: -4.8 % vs -0.5 %).

Also writes lka_fdi_usd (net FDI inflows, USD) for the Invest section.
Standard library only. Each block keeps its previous value if a source fails.
"""
import datetime as dt, json, os, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'compare.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
COUNTRIES = {'LKA': 'Sri Lanka', 'IND': 'India', 'BGD': 'Bangladesh', 'VNM': 'Viet Nam',
             'THA': 'Thailand', 'MYS': 'Malaysia', 'MDV': 'Maldives', 'SGP': 'Singapore'}
FIRST_YEAR = 2000
LAST_PROJ_YEAR = 2030
METRICS = {
    'gdppc':     {'label': 'GDP per capita', 'unit': 'USD', 'wb': 'NY.GDP.PCAP.CD', 'imf': 'NGDPDPC', 'chain': True},
    'gdppc_ppp': {'label': 'GDP per capita (PPP)', 'unit': 'Intl $', 'wb': 'NY.GDP.PCAP.PP.CD', 'imf': 'PPPPC', 'chain': True},
    'growth':    {'label': 'GDP growth', 'unit': '%', 'wb': 'NY.GDP.MKTP.KD.ZG', 'imf': 'NGDP_RPCH', 'chain': False},
    'inflation': {'label': 'Inflation', 'unit': '%', 'wb': 'FP.CPI.TOTL.ZG', 'imf': 'PCPIPCH', 'chain': False},
    'fdi':       {'label': 'Foreign investment (FDI)', 'unit': '% of GDP', 'wb': 'BX.KLT.DINV.WD.GD.ZS', 'imf': None, 'chain': False},
}


def get(url, timeout=120):
    r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url], capture_output=True)
    return r.stdout.decode('utf-8', errors='replace') if r.returncode == 0 else None


def wb(code):
    """{iso: {year: value}} for all COUNTRIES, or None."""
    cc = ';'.join(COUNTRIES)
    txt = get(f'https://api.worldbank.org/v2/country/{cc}/indicator/{code}?format=json&per_page=2000&date={FIRST_YEAR}:{LAST_PROJ_YEAR}')
    try:
        rows = json.loads(txt)[1] or []
    except (TypeError, ValueError, IndexError):
        return None
    out = {}
    for r in rows:
        if r.get('value') is None or r.get('countryiso3code') not in COUNTRIES:
            continue
        out.setdefault(r['countryiso3code'], {})[int(r['date'])] = r['value']
    return out or None


def imf(code):
    """{iso: {year: value}} or None."""
    txt = get(f'https://www.imf.org/external/datamapper/api/v1/{code}')
    try:
        vals = json.loads(txt)['values'][code]
    except (TypeError, ValueError, KeyError):
        return None
    out = {iso: {int(y): v for y, v in vals[iso].items() if v is not None} for iso in COUNTRIES if iso in vals}
    return out


def imf_source():
    txt = get('https://www.imf.org/external/datamapper/api/v1/indicators')
    try:
        return json.loads(txt)['indicators']['NGDPDPC']['source'].strip()
    except (TypeError, ValueError, KeyError):
        return 'IMF World Economic Outlook'


def ccpi_annual():
    """{year: % change in the annual average CCPI} for complete years."""
    try:
        with open(os.path.join(ROOT, 'data', 'ccpi.json'), encoding='utf-8') as f:
            months = json.load(f)['months']
    except (OSError, ValueError, KeyError):
        return {}
    by = {}
    for m, idx, _ in months:
        if idx is not None:
            by.setdefault(int(m[:4]), []).append(idx)
    avg = {y: sum(v) / 12 for y, v in by.items() if len(v) == 12}
    return {y: avg[y] / avg[y - 1] * 100 - 100 for y in avg if y - 1 in avg}


def build_metric(m, override=None):
    actual = wb(m['wb'])
    if not actual:
        return None
    if override and 'LKA' in actual:
        actual['LKA'].update(override)
    proj_src = imf(m['imf']) if m['imf'] else None
    series = {}
    for iso in COUNTRIES:
        a = actual.get(iso, {})
        if not a:
            continue
        last = max(a)
        pts = [[y, round(a[y], 2), 0] for y in sorted(a)]
        p = (proj_src or {}).get(iso, {})
        if p:
            base = p.get(last) if m['chain'] else None
            for y in range(last + 1, LAST_PROJ_YEAR + 1):
                if y not in p:
                    continue
                if m['chain']:
                    if not base:
                        break
                    v = a[last] * p[y] / base
                else:
                    v = p[y]
                pts.append([y, round(v, 2), 1])
        series[iso] = pts
    return {'label': m['label'], 'unit': m['unit'], 'wb': m['wb'], 'imf': m['imf'], 'series': series,
            'note': 'Sri Lanka: Colombo CPI annual average (DCS)' if override else None}


def main():
    prev = {}
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            prev = json.load(f)
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
           'countries': COUNTRIES,
           'sources': {'actual': 'World Bank World Development Indicators', 'projection': imf_source()},
           'metrics': {}}
    for key, m in METRICS.items():
        blk = build_metric(m, ccpi_annual() if key == 'inflation' else None)
        out['metrics'][key] = blk or prev.get('metrics', {}).get(key)
        print(key, 'ok' if blk else 'FAILED (kept previous)')
    fdi = wb('BX.KLT.DINV.CD.WD')
    out['lka_fdi_usd'] = sorted([y, round(v)] for y, v in fdi['LKA'].items()) if fdi and fdi.get('LKA') else prev.get('lka_fdi_usd')
    if not any(out['metrics'].values()):
        raise SystemExit('no data')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
