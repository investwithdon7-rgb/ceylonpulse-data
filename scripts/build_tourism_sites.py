"""Where tourists go -> data/tourism-sites.json

Source: SLTDA "Year in Review" (annual statistical report, published around
April for the previous year), https://www.sltda.gov.lk/en/annual-statistical-report
Its tables give foreign and local visitors for each ticketed attraction:
  national parks (Dept of Wildlife Conservation), conservation forests (Forest
  Dept), Central Cultural Fund heritage sites, national museums, zoos,
  botanical gardens; plus tourist accommodation rooms by province.
Monthly figures per site only appear as charts (image labels), so they are
not extracted. Tables are found by their titles, not their numbers, because
SLTDA renumbers them each year. Each row is checked (local + foreign = total
where the table has a total column); rows that fail are dropped and listed.

Site coordinates are approximate (park entrance or town) and only used to
place map markers. Needs: pip install pypdf
"""
import datetime as dt, io, json, os, re, subprocess

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'tourism-sites.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
INDEX = 'https://www.sltda.gov.lk/en/annual-statistical-report'

# key: (title regex, label, managed by, column index of local, foreign, total or None)
TABLES = {
    'parks':   (r'Visitors to major national parks', 'National parks & marine parks', 'Department of Wildlife Conservation', 0, 2, 6),
    'heritage': (r'administered by Central Cultural Fund', 'Heritage sites', 'Central Cultural Fund', 1, 0, 2),
    'forests': (r'visitors & revenue to conservation forests', 'Forests & eco parks', 'Forest Department', 0, 1, 2),
    'gardens': (r'visitors & revenue to national botanical gardens', 'Botanical gardens', 'Department of National Botanic Gardens', 2, 0, None),
    'museums': (r'visitors & revenue to national museums', 'Museums', 'Department of National Museums', 2, 0, None),
}
ZOO_TITLE = r'Visitors & revenue to zoological gardens'
ROOMS_TITLE = r'Total rooms by province'

# Approximate locations (lat, lon) for map markers, keyed by lower-case name prefix
COORDS = {
    'yala': (6.37, 81.52), 'horton plains': (6.80, 80.80), 'udawalawa': (6.47, 80.89), 'wasgomuwa': (7.72, 80.93),
    'minneriya': (8.03, 80.83), 'bundala': (6.20, 81.24), 'kaudulla': (8.15, 80.92), 'galoya': (7.21, 81.53),
    'kumana': (6.52, 81.70), 'wilpattu': (8.45, 80.00), 'maduruoya': (7.62, 81.17), 'lahugala': (6.88, 81.70),
    'pigeon island': (8.72, 81.21), 'hikkaduwa': (6.14, 80.10), 'eth athuru': (6.43, 80.86), 'kalawewa': (7.98, 80.53),
    'bareef': (8.33, 79.75), 'mirissa': (5.95, 80.46), 'girithale': (7.98, 80.93), 'samanala': (6.81, 80.50),
    'angammedilla': (7.87, 80.93), 'galways land': (6.97, 80.78), 'horagolla': (7.07, 80.13), 'wasgamuwa': (7.72, 80.93),
    'sigiriya': (7.957, 80.760), 'polonnaruwa': (7.94, 81.00), 'jethawanaya': (8.35, 80.40), 'abhayagiriya': (8.37, 80.40),
    'jaffna fort': (9.66, 80.01), 'buduruwagala': (6.79, 81.36), 'ritigala': (8.11, 80.65), 'galle museum': (6.03, 80.22),
    'trincomalee': (8.58, 81.24), 'ibbankatuwa': (7.88, 80.63), 'namal uyana': (8.07, 80.32),
    'hurulu': (8.12, 80.75), 'kunckles': (7.45, 80.80), 'knuckles': (7.45, 80.80), 'sinharaja': (6.43, 80.42),
    'piduruthalagala': (7.00, 80.77), 'udawattakele': (7.30, 80.64), 'kanneliya': (6.25, 80.35), 'badulla (elle': (6.86, 81.05),
    'peradeniya': (7.27, 80.60), 'hakgala': (6.93, 80.82), 'avissawella': (6.95, 80.21), 'gampaha': (7.09, 80.00),
    'mirijjawila': (6.15, 81.07), 'dehiwala': (6.86, 79.87), 'pinnawala zoo': (7.29, 80.38), 'pinnawala': (7.30, 80.39),
    'safari park': (6.23, 80.93), 'colombo national museum': (6.91, 79.86), 'galle maritime': (6.03, 80.22),
    'national museum of natural': (6.91, 79.86), 'galle national museum': (6.03, 80.22), 'kandy national museum': (7.29, 80.64),
    'kandy museum': (7.29, 80.64), 'rathnapura': (6.68, 80.40), 'dutch museum': (6.94, 79.85), 'independence memorial': (6.90, 79.87),
    'hambantota national': (6.12, 81.12), 'katharagama': (6.41, 81.33), 'dambulla': (7.86, 80.65), 'polonnaruwa museum': (7.94, 81.00),
}
# Friendlier display names
RENAME = {'Udawalawa': 'Udawalawe', 'Kunckles Conservation': 'Knuckles Range', 'Eth Athuru Sevana': 'Elephant Transit Home (Eth Athuru Sevana)',
          'Bareef - Kalpitiya': 'Bar Reef, Kalpitiya', 'Mirissa': 'Mirissa (whale watching)', 'Pinnawala': 'Pinnawala Elephant Orphanage',
          'Safari Park': 'Ridiyagama Safari Park', 'Samanala Adaviya': 'Samanala Adaviya (Adam\'s Peak)', 'Galoya': 'Gal Oya',
          'Badulla (Elle Gala)': 'Ella Rock (Ella Gala)', 'Dehiwala': 'Dehiwala Zoo'}


def curl(url, binary=False, timeout=120):
    r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace')


def find_report():
    page = curl(INDEX)
    if not page:
        return None, None
    best = None
    for href in re.findall(r'href="([^"]+\.pdf)"', page):
        m = re.search(r'Year[_ -]?in[_ -]?Review[_ -]?(\d{4})', href, re.I)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), href.replace(' ', '%20'))
    return best if best else (None, None)


def num(tok):
    tok = tok.replace('*', '').replace(',', '')
    try:
        return float(tok)
    except ValueError:
        return None


def table_text(pages, title):
    """Text from the table title to its Source/Total line (may continue on the next page)."""
    for i, t in enumerate(pages):
        m = re.search(title, t, re.I)
        if m:
            return t[m.end():] + '\n' + (pages[i + 1] if i + 1 < len(pages) else '')
    return None


HEADER = re.compile(r'visitors|tourists|income|revenue|tickets|number|total|\(rs|locations?\b|^museums$|^parks$|name of|^location', re.I)
ROW = re.compile(r'^\s*([^\d\n]*?[A-Za-z][^\d\n]*?)\s+((?:\*?[\d,]+(?:\.\d+)?\s*){2,})$')


def rows(text):
    """(name, [numbers]) rows; a digit-free line is the start of the next row's name."""
    out, carry = [], ''
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'^(Source|TOTAL|Total)\b', line, re.I):
            if re.match(r'^(Source)', line, re.I) or out:
                break
        m = ROW.match((carry + ' ' + line).strip() if carry else line)
        if m:
            name = re.sub(r'\s+', ' ', m.group(1).replace('*', '')).strip(' -')
            out.append((name, [num(t) for t in m.group(2).split()]))
            carry = ''
        elif not re.search(r'\d', line) and len(line) < 60 and not HEADER.search(line):
            carry = line   # a wrapped name: keep only the line just before the numbers
        else:
            carry = ''
    return out


def coords(name):
    n = name.lower()
    for k in sorted(COORDS, key=len, reverse=True):
        if n.startswith(k) or k in n:
            return COORDS[k]
    return None


def site(name, local, foreign):
    disp = RENAME.get(name, name)
    c = coords(name)
    return {'name': disp, 'local': int(local), 'foreign': int(foreign), 'total': int(local + foreign),
            'lat': c[0] if c else None, 'lon': c[1] if c else None}


def parse_table(pages, spec, dropped):
    title, label, body, li, fi, ti = spec
    text = table_text(pages, title)
    if not text:
        return None
    sites = []
    for name, v in rows(text):
        if re.match(r'(total|sub destination)', name, re.I) or max(li, fi, ti or 0) >= len(v):
            continue
        loc, frn = v[li], v[fi]
        if loc is None or frn is None or (ti is not None and abs(loc + frn - (v[ti] or 0)) > 1):
            dropped.append(f'{label}: {name} {v}')
            continue
        if loc + frn > 0:
            sites.append(site(name, loc, frn))
    sites.sort(key=lambda s: -s['total'])
    return {'label': label, 'managedBy': body, 'sites': sites} if sites else None


def parse_zoos(pages, dropped):
    text = table_text(pages, ZOO_TITLE)
    if not text:
        return None
    sites, name, pending = [], '', ''
    for line in text.splitlines():
        line = line.strip()
        if re.match(r'^Total\s+20\d\d', line):
            break
        m = re.match(r'^(.*?)\s*(20\d\d)\s+(.+)$', line)
        if not m:
            # A zoo name split over several lines ("Pinnawala" / "Zoo") sits just above its first year row
            pending = (pending + ' ' + line).strip() if line and not re.search(r'\d', line) and len(line) < 30 and not HEADER.search(line) else ''
            continue
        if m.group(1) or pending:
            name = ((pending + ' ' + m.group(1)).strip() if pending else m.group(1).strip())
        pending = ''
        if int(m.group(2)) < 2000 or not name:
            continue
        v = [num(t) for t in m.group(3).split()]
        # Domestic n, revenue, Foreign n, revenue, [online income], Total n, revenue: keep the latest year per zoo
        if len(v) >= 6 and abs(v[0] + v[2] - v[-2]) <= 1:
            sites = [s for s in sites if s['_n'] != name] + [{**site(name, v[0], v[2]), '_n': name, '_y': int(m.group(2))}]
        else:
            dropped.append(f'Zoos: {name} {m.group(2)} {v}')
    for s in sites:
        s.pop('_n'), s.pop('_y')
    sites.sort(key=lambda s: -s['total'])
    return {'label': 'Zoos & elephant orphanage', 'managedBy': 'Department of National Zoological Gardens', 'sites': sites} if sites else None


def parse_rooms(pages):
    text = table_text(pages, ROOMS_TITLE)
    if not text:
        return None
    out = []
    for name, v in rows(text):
        if 'Province' in name and len(v) >= 2:
            out.append({'province': name.replace(' Province', ''), 'rooms': int(v[0]), 'prev': int(v[1])})
    return out or None


def main():
    year, url = find_report()
    if not url:
        raise SystemExit('Year in Review link not found; keeping previous file')
    pdf = curl(url, binary=True)
    if not pdf:
        raise SystemExit('download failed; keeping previous file')
    pages = [p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages]
    dropped = []
    cats = {k: parse_table(pages, spec, dropped) for k, spec in TABLES.items()}
    cats['zoos'] = parse_zoos(pages, dropped)
    cats = {k: v for k, v in cats.items() if v}
    rooms = parse_rooms(pages)
    if len(cats) < 3:
        raise SystemExit(f'only {len(cats)} tables parsed; keeping previous file')
    for k, c in cats.items():
        print(f'  {k}: {len(c["sites"])} sites, top {c["sites"][0]["name"]} {c["sites"][0]["total"]:,}')
    if dropped:
        print('  dropped rows:', *dropped, sep='\n    ')
    out = {'year': year, 'source': f'Sri Lanka Tourism Development Authority — Year in Review {year}',
           'reportUrl': url, 'indexUrl': INDEX, 'categories': cats,
           'rooms': {'year': year, 'prevYear': year - 1, 'provinces': rooms} if rooms else None,
           'dropped': dropped}
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8'))
        old.pop('generated', None)
        if old == out:
            print('tourism-sites.json: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **out}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', OUT)


if __name__ == '__main__':
    main()
