"""Agriculture, livestock, fisheries and rural labour -> data/agri.json

Sources (all free, no key):
  World Bank Commodity Price Data (Pink Sheet), monthly: tea auction prices
      (Colombo, Kolkata, Mombasa, 3-auction average), rubber, coconut oil,
      rice and urea in US$ - from 2015.
  FAOSTAT Production (Crops & Livestock), bulk Asia file: area harvested,
      yield and production for Sri Lanka's main crops, livestock numbers and
      milk / egg / meat output - 2000 to latest.
  World Bank WDI: capture fisheries vs aquaculture (t), agriculture share of
      GDP, share of employment in agriculture (total / female / male), female
      labour-force participation and female share of the labour force.

Needs openpyxl. Each block keeps its previous value if a source fails.
"""
import csv, datetime as dt, io, json, os, re, subprocess, tempfile, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'agri.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
FAO_ZIP = 'https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_Asia.zip'
FIRST_YEAR = 2000

PINK = {  # output key -> (Pink Sheet column, label, unit)
    'teaColombo': ('Tea, Colombo', 'Tea - Colombo auction', 'US$/kg'),
    'teaKolkata': ('Tea, Kolkata', 'Tea - Kolkata auction', 'US$/kg'),
    'teaMombasa': ('Tea, Mombasa', 'Tea - Mombasa auction', 'US$/kg'),
    'teaAvg': ('Tea, avg 3 auctions', 'Tea - average of 3 auctions', 'US$/kg'),
    'rubber': ('Rubber, RSS3', 'Natural rubber (RSS3)', 'US$/kg'),
    'coconutOil': ('Coconut oil', 'Coconut oil', 'US$/t'),
    'rice': ('Rice, Thai 5%', 'Rice (Thai 5%)', 'US$/t'),
    'urea': ('Urea', 'Urea fertiliser', 'US$/t'),
}
CROPS = [  # FAOSTAT item -> short label
    ('Rice', 'Paddy rice'), ('Tea leaves', 'Tea (green leaf)'), ('Coconuts, in shell', 'Coconut'),
    ('Natural rubber in primary forms', 'Rubber'), ('Maize (corn)', 'Maize'),
    ('Cinnamon and cinnamon-tree flowers, raw', 'Cinnamon'), ('Pepper (Piper spp.), raw', 'Pepper'),
    ('Cloves (whole stems), raw', 'Cloves'), ('Areca nuts', 'Areca nut'), ('Cashew nuts, in shell', 'Cashew'),
    ('Plantains and cooking bananas', 'Bananas & plantains'), ('Mangoes, guavas and mangosteens', 'Mango'),
    ('Pineapples', 'Pineapple'), ('Cassava, fresh', 'Cassava (manioc)'), ('Potatoes', 'Potato'),
    ('Onions and shallots, dry (excluding dehydrated)', 'Big onion & shallot'),
    ('Chillies and peppers, green (Capsicum spp. and Pimenta spp.)', 'Green chilli'),
    ('Cabbages', 'Cabbage'), ('Tomatoes', 'Tomato'), ('Groundnuts, excluding shelled', 'Groundnut'),
    ('Sugar cane', 'Sugar cane'), ('Vegetables Primary', 'All vegetables'), ('Fruit Primary', 'All fruit'),
]
STOCKS = [('Cattle', 'Cattle', 1), ('Buffalo', 'Buffalo', 1), ('Goats', 'Goats', 1),
          ('Swine / pigs', 'Pigs', 1), ('Chickens', 'Chickens', 1000)]
PRODUCTS = [  # item, label, unit in output, multiplier from FAOSTAT unit
    ('Raw milk of cattle', 'Cow milk', 't', 1), ('Raw milk of buffalo', 'Buffalo milk', 't', 1),
    ('Hen eggs in shell, fresh', 'Hen eggs', 'million eggs', 0.001),
    ('Meat of chickens, fresh or chilled', 'Chicken meat', 't', 1),
    ('Meat of cattle with the bone, fresh or chilled', 'Beef', 't', 1),
]
WB = {
    'capture': 'ER.FSH.CAPT.MT', 'aquaculture': 'ER.FSH.AQUA.MT', 'agriGdp': 'NV.AGR.TOTL.ZS',
    'agriEmp': 'SL.AGR.EMPL.ZS', 'agriEmpFemale': 'SL.AGR.EMPL.FE.ZS', 'agriEmpMale': 'SL.AGR.EMPL.MA.ZS',
    'flfp': 'SL.TLF.CACT.FE.ZS', 'mlfp': 'SL.TLF.CACT.MA.ZS', 'femaleShare': 'SL.TLF.TOTL.FE.ZS',
    'labourForce': 'SL.TLF.TOTL.IN',
}


def curl(url, out=None, timeout=180):
    args = ['curl', '-sSL', '--fail', '--compressed', '-m', str(timeout), '-A', UA, url]
    if out:
        args[1:1] = ['-o', out]
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        return None
    return True if out else r.stdout.decode('utf-8', errors='replace')


def pink():
    import openpyxl
    page = curl('https://www.worldbank.org/en/research/commodity-markets', timeout=60) or ''
    m = re.search(r'https://[^"\']*CMO-Historical-Data-Monthly\.xlsx', page)
    if not m:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'cmo.xlsx')
        if not curl(m.group(0), path):
            return None
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = list(wb['Monthly Prices'].iter_rows(values_only=True))
        wb.close()
    hdr = next(i for i, r in enumerate(rows[:12]) if r and any(c and 'Tea' in str(c) for c in r))
    head = [str(c or '').strip() for c in rows[hdr]]
    updated = next((str(r[0]).replace('Updated on', '').strip() for r in rows[:hdr] if r and r[0] and 'Updated' in str(r[0])), None)
    cols = {k: head.index(col) for k, (col, _, _) in PINK.items() if col in head}
    months, series = [], {k: [] for k in cols}
    for r in rows[hdr + 2:]:
        lab = str(r[0] or '')
        mm = re.fullmatch(r'(\d{4})M(\d{2})', lab)
        if not mm or int(mm.group(1)) < 2015:
            continue
        months.append(f'{mm.group(1)}-{mm.group(2)}')
        for k, j in cols.items():
            v = r[j]
            series[k].append(round(float(v), 3) if isinstance(v, (int, float)) else None)
    while months and all(series[k][-1] is None for k in series):  # trim empty trailing months
        months.pop()
        for k in series:
            series[k].pop()
    if not months:
        return None
    return {'source': 'World Bank Commodity Price Data (Pink Sheet)',
            'url': 'https://www.worldbank.org/en/research/commodity-markets', 'updated': updated,
            'months': months, 'series': {k: {'label': PINK[k][1], 'unit': PINK[k][2], 'values': series[k]} for k in series}}


def fao():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'fao.zip')
        if not curl(FAO_ZIP, path, timeout=300):
            return None
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith('_NOFLAG.csv') and 'Asia' in n)
            with z.open(name) as fh:
                rd = csv.DictReader(io.TextIOWrapper(fh, encoding='latin-1'))
                years = sorted(int(c[1:]) for c in rd.fieldnames if re.fullmatch(r'Y\d{4}', c) and int(c[1:]) >= FIRST_YEAR)
                data = {}
                for r in rd:
                    if r['Area'] == 'Sri Lanka':
                        data[(r['Item'], r['Element'])] = [float(r[f'Y{y}']) if r.get(f'Y{y}') not in (None, '') else None
                                                            for y in years]
    while years and not data.get(('Rice', 'Production'), [None])[-1]:  # drop not-yet-published years
        years.pop()
        data = {k: v[:-1] for k, v in data.items()}
    rnd = lambda xs, m=1, d=0: [None if x is None else round(x * m, d) if d else int(round(x * m)) for x in xs]
    crops = []
    for item, label in CROPS:
        p = data.get((item, 'Production'))
        if p and any(p):
            crops.append({'item': label, 'production': rnd(p), 'area': rnd(data.get((item, 'Area harvested')) or []),
                          'yield': rnd(data.get((item, 'Yield')) or [])})
    stocks = [{'item': label, 'head': rnd(data[(item, 'Stocks')], mult)}
              for item, label, mult in STOCKS if data.get((item, 'Stocks'))]
    products = [{'item': label, 'unit': unit, 'values': rnd(data[(item, 'Production')], mult, 1 if mult < 1 else 0)}
                for item, label, unit, mult in PRODUCTS if data.get((item, 'Production'))]
    if not crops:
        return None
    return {'source': 'FAOSTAT - Production: Crops and livestock products', 'url': 'https://www.fao.org/faostat/en/#data/QCL',
            'note': 'Production in tonnes, area harvested in hectares, yield in kg per hectare. '
                    'Tea is green leaf (made tea is roughly a fifth of this weight).',
            'years': years, 'crops': crops, 'livestock': stocks, 'products': products}


def wb():
    out = {}
    for key, code in WB.items():
        txt = curl(f'https://api.worldbank.org/v2/country/LKA/indicator/{code}?format=json&per_page=200', timeout=60)
        try:
            rows = json.loads(txt)[1] or []
        except (TypeError, ValueError, IndexError):
            continue
        pts = sorted([int(r['date']), round(r['value'], 2)] for r in rows
                     if r.get('value') is not None and int(r['date']) >= 1990)
        if pts:
            out[key] = pts
    if not out:
        return None
    return {'source': 'World Bank WDI (FAO fisheries, ILO modelled labour estimates)',
            'url': 'https://data.worldbank.org/country/sri-lanka', **out}


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    old.pop('generated', None)
    payload = {}
    for key, fn in [('prices', pink), ('fao', fao), ('wb', wb)]:
        try:
            val = fn()
        except Exception as e:  # keep previous block on any source failure
            print(f'  {key}: error {e}')
            val = None
        if val:
            payload[key] = val
            print(f'  {key}: ok')
        elif key in old:
            payload[key] = old[key]
            print(f'  {key}: failed; kept previous')
        else:
            print(f'  {key}: failed')
    if not payload:
        print('agri: nothing built')
        return
    if payload == old:
        print('agri.json: unchanged')
        return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'agri.json: updated ({os.path.getsize(OUT)} bytes)')


if __name__ == '__main__':
    main()
