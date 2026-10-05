"""Monthly environment & climate series for Sri Lanka -> data/environment.json

* CO2 per capita, renewable share of electricity, forest share of land:
  Our World in Data grapher CSVs (Global Carbon Project, Ember/Energy Institute, FAO)
* Yearly temperature and rainfall since 1950: ERA5 reanalysis via the Open-Meteo
  archive, averaged over six stations across the island; anomalies vs 1991-2020.
Standard library + curl.
"""
import csv, datetime as dt, io, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'environment.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
OWID = 'https://ourworldindata.org/grapher/{}.csv?country=~LKA&csvType=filtered&useColumnShortNames=true'
STATIONS = [('Colombo', 6.93, 79.86), ('Kandy', 7.29, 80.63), ('Jaffna', 9.66, 80.03), ('Trincomalee', 8.59, 81.22),
            ('Hambantota', 6.12, 81.12), ('Anuradhapura', 8.31, 80.40)]
START = 1950


def curl(url, timeout=180, tries=5):
    for i in range(tries):
        r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode('utf-8', errors='replace')
        print('  retry', i + 1, url[:80], r.stderr.decode(errors='replace')[:100])
        time.sleep(60 * (i + 1))  # Open-Meteo rate-limits heavy archive requests
    return None


def owid(slug, column):
    # csvType=full: some charts ignore the country filter and return only the latest year
    text = curl(OWID.format(slug).replace('csvType=filtered', 'csvType=full'))
    if not text:
        return None
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get('code') == 'LKA' and r.get(column) not in (None, '')]
    return [{'year': int(r['year']), 'value': round(float(r[column]), 3)} for r in rows] or None


def era5_climate():
    last = dt.date.today().year - 1
    per = []
    for name, la, lo in STATIONS:
        raw = curl(f'https://archive-api.open-meteo.com/v1/archive?latitude={la}&longitude={lo}'
                   f'&start_date={START}-01-01&end_date={last}-12-31&daily=temperature_2m_mean,precipitation_sum&timezone=Asia%2FColombo'
                   # ERA5 only: the default blends in a higher-resolution model for recent
                   # years, which inflates recent rainfall and breaks the long-term series
                   '&models=era5')
        if not raw:
            return None
        d = json.loads(raw)['daily']
        t, p = {}, {}
        for day, tv, pv in zip(d['time'], d['temperature_2m_mean'], d['precipitation_sum']):
            y = int(day[:4])
            if tv is not None:
                t.setdefault(y, []).append(tv)
            p[y] = p.get(y, 0) + (pv or 0)
        per.append({y: (sum(v) / len(v), p[y]) for y, v in t.items() if len(v) > 360})
        print('  ERA5', name, 'done')
        time.sleep(30)
    years = sorted(set.intersection(*(set(s) for s in per)))
    temp = [sum(s[y][0] for s in per) / len(per) for y in years]
    rain = [sum(s[y][1] for s in per) / len(per) for y in years]
    base = [i for i, y in enumerate(years) if 1991 <= y <= 2020]
    tb = sum(temp[i] for i in base) / len(base)
    rb = sum(rain[i] for i in base) / len(base)
    return {
        'years': years, 'tempMean': [round(v, 2) for v in temp], 'tempAnom': [round(v - tb, 2) for v in temp],
        'rainTotal': [round(v) for v in rain], 'rainAnomPct': [round((v - rb) / rb * 100, 1) for v in rain],
        'baseline': '1991-2020', 'baselineTemp': round(tb, 2), 'baselineRain': round(rb),
        'stations': [s[0] for s in STATIONS], 'source': 'ERA5 reanalysis (ECMWF) via Open-Meteo, mean of six stations',
    }


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    out = {
        'co2PerCapita': owid('co-emissions-per-capita', 'emissions_total_per_capita') or old.get('co2PerCapita'),
        'renewableElectricity': owid('share-electricity-renewables', 'renewable_share_of_electricity__pct') or old.get('renewableElectricity'),
        'forestShare': owid('forest-area-as-share-of-land-area', 'forest_share') or old.get('forestShare'),
        'climate': era5_climate() or old.get('climate'),
        'sources': {
            'co2PerCapita': 'Global Carbon Budget via Our World in Data (tonnes CO2 per person, fossil fuels and industry)',
            'renewableElectricity': 'Ember and Energy Institute via Our World in Data (% of electricity)',
            'forestShare': 'FAO Global Forest Resources Assessment via Our World in Data (% of land area)',
        },
    }
    if not all(out[k] for k in ('co2PerCapita', 'renewableElectricity', 'forestShare', 'climate')):
        print('environment: a source failed and there is no previous copy:', [k for k in out if not out[k]])
        sys.exit(1)
    cmp_old = dict(old)
    cmp_old.pop('generated', None)
    if cmp_old == out:
        print('environment: unchanged')
        return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **out}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('wrote environment.json', os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
