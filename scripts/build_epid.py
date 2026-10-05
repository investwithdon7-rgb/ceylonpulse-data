"""Weekly Epidemiological Report (Epidemiology Unit, Ministry of Health) -> data/epidemiology.json

Reads "Table 1: Distribution of Notified Diseases reported by Medical Officers
of Health" from each WER PDF: weekly (A) and year-to-date (B) cases for every
RDHS division. Only reports not already in the file are downloaded.
Needs: pip install pypdf
"""
import datetime as dt, io, json, os, re, subprocess, sys

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'epidemiology.json')
LIST_URL = 'https://www.epid.gov.lk/weekly-epidemiological-report'
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
# Column pairs (A = this week, B = year to date) in Table 1, in order
DISEASES = ['dengue', 'dysentery', 'encephalitis', 'entericFever', 'foodPoisoning', 'leptospirosis', 'typhus',
            'viralHepatitis', 'rabies', 'chickenpox', 'meningitis', 'leishmaniasis', 'tuberculosis', 'leprosy']
KEEP = ['dengue', 'leptospirosis', 'typhus', 'viralHepatitis', 'chickenpox', 'tuberculosis']
MAX_WEEKS = 60


def curl(url, binary=False, timeout=90):
    r = subprocess.run(['curl', '-sSL', '--fail', '-m', str(timeout), '-A', UA, url], capture_output=True)
    if r.returncode != 0:
        print('  fetch failed', url[-60:], r.stderr.decode(errors='replace')[:100])
        return None
    return r.stdout if binary else r.stdout.decode('utf-8', errors='replace')


def parse_report(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages:
        t = page.extract_text() or ''
        if 'Table 1' not in t or 'RDHS' not in t:
            continue
        week = re.search(r'\((\d+)\w*\s+Week\)', t)
        period = re.search(r'Table 1:.*?(\d{1,2})\w*\s*[–-]\s*(\d{1,2})\w*\s+([A-Za-z]{3})\w*\s+(\d{4})', t, re.S)
        rows = {}
        for line in t.splitlines():
            m = re.match(r'^\s*([A-Za-z][A-Za-z .]*?)\s+((?:\d+\s+){29}\d+)\s*$', line)
            if m:
                nums = [int(x) for x in m.group(2).split()]
                rows[m.group(1).strip()] = {d: [nums[2 * i], nums[2 * i + 1]] for i, d in enumerate(DISEASES)}
        if 'SRILANKA' not in rows and 'SRI LANKA' not in rows:
            return None
        printed = rows.pop('SRILANKA', None) or rows.pop('SRI LANKA')
        # National figures = sum of the RDHS rows. The printed SRI LANKA row is
        # occasionally wrong (Vol 53 No 1 prints 144 dengue cases; Colombo alone had 388).
        total = {d: [sum(r[d][0] for r in rows.values()), sum(r[d][1] for r in rows.values())] for d in DISEASES}
        if printed['dengue'] != total['dengue']:
            print(f"  note: printed national dengue {printed['dengue']} != sum of districts {total['dengue']}")
        end = None
        if period:
            try:
                end = dt.datetime.strptime(f'{period.group(2)} {period.group(3)} {period.group(4)}', '%d %b %Y').date().isoformat()
            except ValueError:
                pass
        return {'week': int(week.group(1)) if week else None, 'weekEnd': end, 'total': total, 'rdhs': rows}
    return None


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    series = {s['id']: s for s in old.get('series', [])}
    unparsed = set(old.get('unparsed', []))  # reports with a different layout; don't re-download daily
    latest_detail = old.get('latest')

    page = curl(LIST_URL)
    if not page:
        sys.exit(1)
    links = []
    for m in re.finditer(r'(https://www\.epid\.gov\.lk/storage/post/pdfs/[^"\' ]+?Vol_(\d+)_no_(\d+)-english\.pdf)', page):
        rid = f'{int(m.group(2))}-{int(m.group(3)):02d}'
        if rid not in [l[0] for l in links]:
            links.append((rid, m.group(1)))
    links.sort(key=lambda l: tuple(map(int, l[0].split('-'))), reverse=True)
    links = links[:MAX_WEEKS]

    changed = False
    for rid, url in links:
        if (rid in series or rid in unparsed) and not (latest_detail is None and rid == links[0][0]):
            continue
        pdf = curl(url, binary=True)
        if not pdf:
            continue
        try:
            rep = parse_report(pdf)
        except Exception as e:
            print('  parse error', rid, e)
            rep = None
        if not rep:
            print('  no Table 1 in', rid)
            unparsed.add(rid)
            changed = True
            continue
        vol, no = rid.split('-')
        # Volume N covers year 1973 + N; week 50+ in an early issue is the previous year.
        # Some reports misprint the year in the table header, so trust the volume.
        if rep['weekEnd']:
            yr = 1973 + int(vol) - (1 if rep['week'] and rep['week'] >= 50 and int(no) <= 3 else 0)
            rep['weekEnd'] = f"{yr}{rep['weekEnd'][4:]}"
        series[rid] = {'id': rid, 'vol': int(vol), 'no': int(no), 'week': rep['week'], 'weekEnd': rep['weekEnd'],
                       **{d: rep['total'][d][0] for d in KEEP}}
        if rid == links[0][0]:
            latest_detail = {'id': rid, 'week': rep['week'], 'weekEnd': rep['weekEnd'], 'url': url,
                             'national': {d: rep['total'][d] for d in KEEP},
                             'rdhs': {k: {d: v[d] for d in KEEP} for k, v in rep['rdhs'].items()}}
        print('  parsed', rid, 'week', rep['week'], 'dengue', rep['total']['dengue'])
        changed = True

    if not changed and old:
        print('epidemiology: no new reports')
        return
    if not latest_detail or len(latest_detail.get('rdhs', {})) < 20:
        print('epidemiology: latest report did not parse; keeping previous file')
        sys.exit(1)
    ordered = sorted(series.values(), key=lambda s: (s['vol'], s['no']))[-MAX_WEEKS:]
    out = {
        'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'source': 'Epidemiology Unit, Ministry of Health — Weekly Epidemiological Report, Table 1 (notified cases by RDHS)',
        'url': LIST_URL, 'latest': latest_detail, 'series': ordered, 'unparsed': sorted(unparsed),
        'note': 'A = cases notified in the week; year-to-date counts restart each January. Reports are published several weeks after the week they cover.',
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('wrote epidemiology.json', os.path.getsize(OUT), 'bytes,', len(ordered), 'weeks')


if __name__ == '__main__':
    main()
