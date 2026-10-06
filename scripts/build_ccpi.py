"""Colombo Consumer Price Index (CCPI) -> data/ccpi.json

Department of Census and Statistics publishes two monthly tables as PDFs:
  * base 2013 = 100: January 2014 – January 2023 (discontinued)
  * base 2021 = 100: January 2022 onwards (updated every month)
The 2013 series is rescaled onto the 2021 base using December 2022, the last
month both cover, giving one continuous index. Year-on-year inflation is the
published figure (2013 table up to January 2023, 2021 table after).
Kept from January 2016. Needs: pip install pypdf
"""
import datetime as dt, io, json, os, re, subprocess

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'ccpi.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
BASE = 'https://www.statistics.gov.lk/Resource/en/InflationAndPrices/CCPI/'
PDF_2021 = BASE + 'MOVEMENTS_of_CCPI_with_MV_Base2021.pdf'
PDF_2013 = BASE + 'MOVEMENTS_of_CCPI_with_MV_Base2013-100.pdf'
LINK_MONTH = '2022-12'
START = '2016-01'
MONTHS = {m: i + 1 for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July',
                                           'August', 'September', 'October', 'November', 'December'])}


def curl(url):
    r = subprocess.run(['curl', '-sSL', '--fail', '-m', '90', '-A', UA, url], capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout.startswith(b'%PDF') else None


def parse(pdf):
    """{'YYYY-MM': (index, yoy or None)} — numbers after the month are index, m/m %, y/y %, 12-month average."""
    text = '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
    out, year = {}, None
    for line in text.splitlines():
        m = re.match(r'^\s*(?:(\d{4})\s+)?([A-Z][a-z]+)\s+([-\d.\s]+?)\s*$', line)
        if not m or m.group(2) not in MONTHS:
            continue
        year = int(m.group(1)) if m.group(1) else year
        nums = [float(x) for x in m.group(3).split()]
        if year is None or not nums:
            continue
        out[f'{year}-{MONTHS[m.group(2)]:02d}'] = (nums[0], nums[2] if len(nums) >= 3 else None)
    return out


def main():
    p21, p13 = curl(PDF_2021), curl(PDF_2013)
    if not p21 or not p13:
        print('ccpi: a DCS table is unavailable; keeping previous file')
        return
    s21, s13 = parse(p21), parse(p13)
    if LINK_MONTH not in s21 or LINK_MONTH not in s13 or len(s21) < 40:
        print('ccpi: tables not recognised; keeping previous file')
        return
    k = s21[LINK_MONTH][0] / s13[LINK_MONTH][0]
    rows = []
    for ym in sorted(set(s13) | set(s21)):
        if ym < START:
            continue
        if ym <= LINK_MONTH:
            idx = s13[ym][0] * k
        else:
            idx = s21[ym][0]
        yoy = s13[ym][1] if ym <= '2023-01' and ym in s13 else s21.get(ym, (None, None))[1]
        rows.append([ym, round(idx, 2), yoy])
    payload = {
        'source': 'Department of Census and Statistics — Colombo Consumer Price Index',
        'url': 'https://www.statistics.gov.lk/InflationAndPrices/StaticalInformation/MonthlyCCPI',
        'base': '2021 = 100 (2013-base series linked at December 2022)',
        'columns': ['month', 'index', 'yoy'],
        'months': rows,
    }
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8'))
        old.pop('generated', None)
        if old == payload:
            print('ccpi.json: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'ccpi.json: updated ({len(rows)} months, latest {rows[-1]})')


if __name__ == '__main__':
    main()
