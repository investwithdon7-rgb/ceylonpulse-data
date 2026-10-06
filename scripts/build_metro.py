"""Lanka Metro Transit route lines -> data/metro-routes.json

lankametro.lk draws its SmartMetro map from GeoJSON files on Google Cloud
Storage that send no CORS headers, so the website cannot load them directly.
This copies the route lines and stops into one small file. Live bus positions,
stops and the route list come straight from lankametro.lk/api (CORS open).
Standard library only. Rewritten only when the content changes.
"""
import datetime as dt, json, os, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'metro-routes.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
BASE = 'https://storage.googleapis.com/artwork_storage_dev/'
# Files referenced by the SmartMetro page (Makumbura–Colombo and Makumbura–Kadawatha, both directions)
FILES = ['metro/v7.2%20Forward-M-C.geojson', 'metro/v7.2-Return-C-M.geojson',
         'v7.2-Forward-M-K.geojson', 'v7.2-Return-K-M.geojson']


def thin(coords, step=0.00015):
    """[lat, lon] pairs, dropping points closer than ~15 m to the last kept one."""
    out = []
    for lon, lat, *_ in coords:
        if not out or abs(lat - out[-1][0]) + abs(lon - out[-1][1]) > step:
            out.append([round(lat, 5), round(lon, 5)])
    last = [round(coords[-1][1], 5), round(coords[-1][0], 5)]
    if out[-1] != last:
        out.append(last)
    return out


def main():
    routes = []
    for f in FILES:
        r = subprocess.run(['curl', '-sSL', '--fail', '-m', '60', '-A', UA, BASE + f], capture_output=True)
        try:
            fc = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            fc = None
        if not fc:
            print(f'metro: {f} unavailable; keeping previous file')
            return
        line = next((x for x in fc['features'] if x['geometry']['type'] == 'LineString'), None)
        if not line:
            continue
        p = line.get('properties') or {}
        stops = sorted((x for x in fc['features'] if x['geometry']['type'] == 'Point'),
                       key=lambda x: (x.get('properties') or {}).get('sequence') or 0)
        routes.append({
            'code': p.get('route_code'), 'name': p.get('route_name'), 'direction': p.get('direction'),
            'line': thin(line['geometry']['coordinates']),
            'stops': [{'name': (s.get('properties') or {}).get('name'),
                       'lat': round(s['geometry']['coordinates'][1], 5), 'lon': round(s['geometry']['coordinates'][0], 5)}
                      for s in stops],
        })
    payload = {'source': 'Lanka Metro Transit — SmartMetro', 'url': 'https://lankametro.lk/en/smartmetro', 'routes': routes}
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8'))
        old.pop('generated', None)
        if old == payload:
            print('metro-routes.json: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'metro-routes.json: updated ({os.path.getsize(OUT)} bytes, {len(routes)} route lines)')


if __name__ == '__main__':
    main()
