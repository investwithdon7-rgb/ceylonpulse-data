"""Government Gazette, Bills and Acts -> data/gazette.json

documents.gov.lk (Department of Government Printing) was rebuilt in 2026.
Each listing page embeds its records in the Next.js page data
(self.__next_f.push([1,"..."]) chunks) as {"initialData":{"items":[...]}}.
Each item has number, date, descriptions in Sinhala/Tamil/English and one
PDF per language, served through /api/content-file-proxy?file=/<path>.

Security note: in September 2026 the old site served a malicious script.
The rebuilt site was checked on 2026-10-07 (no markers of that attack).
This script only reads the page data; it never runs page scripts.
Standard library only. Keeps the previous file if parsing fails.
"""
import datetime as dt, json, os, re, subprocess, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'gazette.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
SITE = 'https://documents.gov.lk'
PAGES = {'extraordinary': '/web/extra_gazettes', 'gazettes': '/web/gazettes', 'bills': '/web/bills', 'acts': '/web/acts'}
KEEP = {'extraordinary': 25, 'gazettes': 6, 'bills': 15, 'acts': 15}


def fetch(path):
    r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', '60', '-A', UA, SITE + path], capture_output=True)
    return r.stdout.decode('utf-8', errors='replace') if r.returncode == 0 else None


def flight_text(html):
    """Concatenate the decoded Next.js flight-data strings."""
    out = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return ''.join(out)


def json_array_after(text, key):
    i = text.find(key)
    if i < 0:
        return None
    j = text.find('[', i)
    depth, in_str, esc = 0, False, False
    for k in range(j, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j:k + 1])
                except ValueError:
                    return None
    return None


def pdf_url(path):
    return f'{SITE}/api/content-file-proxy?file=' + urllib.parse.quote('/' + path.lstrip('/'), safe='/')


def simplify(item):
    files = {}
    for c in item.get('contents') or []:
        lang = {'ENGLISH': 'en', 'SINHALA': 'si', 'TAMIL': 'ta'}.get(c.get('language'))
        if lang and c.get('uploadedFile'):
            files[lang] = pdf_url(c['uploadedFile'])
    no = next((str(v) for k, v in item.items() if k.endswith('NoText') and v), None)
    if not no and item.get('gazetteNo') is not None:
        no = f"{item['gazetteNo']}" + (f"/{item['gazetteSubNo']}" if item.get('gazetteSubNo') else '')
    title = (item.get('descriptionEnglish') or item.get('titleEnglish') or item.get('nameEnglish')
             or item.get('descriptionSinhala') or item.get('titleSinhala') or '')
    return {'no': no, 'date': (item.get('date') or item.get('publishedDate') or '')[:10], 'title': title.strip(),
            'files': files} if (title or files) else None


def main():
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    old.pop('generated', None)
    payload = {'source': 'Department of Government Printing', 'url': SITE + '/home'}
    for key, path in PAGES.items():
        html = fetch(path)
        text = flight_text(html) if html else ''
        if key == 'gazettes':
            # weekly issues: the page lists issue dates; the parts load in the browser
            dates = json_array_after(text, '"initialData":{"dates":') or []
            rows = [{'no': None, 'date': d[:10], 'title': 'Weekly Gazette (Parts I-IV)', 'files': {},
                     'page': SITE + path} for d in dates]
        else:
            items = json_array_after(text, '"initialData":{"items":')
            rows = [r for r in (simplify(x) for x in (items or [])) if r]
        if rows:
            rows.sort(key=lambda r: r['date'], reverse=True)
            payload[key] = rows[:KEEP[key]]
            print(f'  {key}: {len(rows)} (latest {rows[0]["date"]} {rows[0]["no"]})')
        elif key in old:
            payload[key] = old[key]
            print(f'  {key}: page not parsed; kept previous')
        else:
            print(f'  {key}: page not parsed')
    if not any(k in payload for k in PAGES):
        print('gazette: nothing parsed; keeping previous file')
        return
    if payload == old:
        print('gazette.json: unchanged')
        return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'gazette.json: updated ({os.path.getsize(OUT)} bytes)')


if __name__ == '__main__':
    main()
