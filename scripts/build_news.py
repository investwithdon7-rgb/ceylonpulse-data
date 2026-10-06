"""Sri Lanka news headlines -> data/news.json

Browsers can only reach these RSS feeds through slow public proxies (Ada Derana
not at all), so the hourly job reads them directly and merges the latest
headlines into one small file. Standard library only. If every feed fails the
previous file is kept.
"""
import datetime as dt, email.utils, html, json, os, re, subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'news.json')
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'
FEEDS = [
    ('Ada Derana', 'https://www.adaderana.lk/rss.php'),
    ('Daily Mirror', 'https://www.dailymirror.lk/rss/breaking_news/108'),
    ('The Island', 'https://www.island.lk/feed/'),
    ('Newswire', 'https://www.newswire.lk/feed/'),
    ('ColomboPage', 'http://www.colombopage.com/feed/'),
    ('OnLanka', 'https://www.onlanka.com/feed/'),
    ('Tamil Guardian', 'https://www.tamilguardian.com/rss.xml'),
]
PER_FEED, KEEP = 12, 60


def fetch(url):
    r = subprocess.run(['curl', '-sSL', '--fail', '--compressed', '-m', '40', '-A', UA, url], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def when(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))  # local feeds without a zone
    return d.astimezone(dt.timezone.utc).isoformat(timespec='seconds')


def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def parse(raw, source):
    # Some feeds carry stray bytes before the XML declaration
    raw = raw[raw.find(b'<'):] if b'<' in raw else raw
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter('item'):
        title = clean(it.findtext('title'))
        link = (it.findtext('link') or '').strip()
        if not title or not link.startswith('http'):
            continue
        out.append({'title': title, 'link': link, 'pubDate': when(it.findtext('pubDate')), 'source': source})
    return out[:PER_FEED]


def main():
    items, ok = [], []
    for source, url in FEEDS:
        raw = fetch(url)
        got = parse(raw, source) if raw else []
        print(f'  {source}: {len(got)}')
        if got:
            ok.append(source)
            items += got
    if not items:
        print('news: every feed failed; keeping previous file')
        return
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    seen, merged = set(), []
    for i in sorted(items, key=lambda x: x['pubDate'] or '', reverse=True):
        k = re.sub(r'\W+', ' ', i['title'].lower())[:70]
        if k in seen or (i['pubDate'] and i['pubDate'] > now):
            continue
        seen.add(k)
        merged.append(i)
    payload = {'sources': ok, 'items': merged[:KEEP]}
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8'))
        old.pop('generated', None)
        if old == payload:
            print('news.json: unchanged')
            return
    out = {'generated': now, **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'news.json: updated ({len(merged[:KEEP])} headlines from {len(ok)} feeds)')


if __name__ == '__main__':
    main()
