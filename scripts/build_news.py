"""Sri Lanka news headlines -> data/news.json

Browsers can only reach these RSS feeds through slow public proxies (Ada Derana
not at all), so the hourly job reads them directly and merges the latest
headlines into one small file. Standard library only. If every feed fails the
previous file is kept.

topics.invest = investment headlines for the Invest section: a Google News
search (browsers can't fetch it: rss2json refuses it, the proxies are slow)
plus the Daily Mirror business feed, kept to titles matching INVEST_RE, 14 days.
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
GNEWS = 'https://news.google.com/rss/search?q={q}&hl=en-LK&gl=LK&ceid=LK:en'
TOPICS = {
    'invest': {
        'feeds': [
            ('Google News', GNEWS.format(q='sri+lanka+(BOI+OR+%22board+of+investment%22+OR+FDI+OR+%22foreign+investment%22+OR+%22Port+City%22+OR+%22investment+zone%22)+when:14d')),
            ('Daily Mirror', 'https://www.dailymirror.lk/rss/business/215'),
        ],
        're': re.compile(r'\bBOI\b|board of investment|\bFDI\b|foreign (?:direct )?investment|investors?\b|investment (?:zone|project|agreement|approval|promotion|climate)|port city|special economic zone|\bEPZ\b|export processing zone|joint venture|bilateral investment|strategic development project', re.I),
        'days': 14, 'keep': 12,
    },
    # Safety & Justice: scam, fraud and phishing warnings (CERT, CBSL, police)
    'scam': {
        'feeds': [
            ('Google News', GNEWS.format(q='sri+lanka+(scam+OR+fraud+OR+phishing+OR+%22pyramid+scheme%22+OR+%22Sri+Lanka+CERT%22+OR+%22unauthorised+deposit%22)+when:30d')),
        ],
        're': re.compile(r'scam|fraud|phishing|swindl|pyramid scheme|ponzi|fake (?:link|website|message|sms|app|job|account|page)|cyber ?crime|unauthori[sz]ed (?:deposit|financial)|impersonat', re.I),
        # must read as a warning to the public, not a fraud arrest or court case
        'need': re.compile(r'warn|alert|beware|caution|urge[ds]?|vigilan|advis|be careful|fake|impersonat|don.t|do not|how to', re.I),
        'skip': re.compile(r'\b(India|Indian|Thai|Thailand|Japan|Malaysia|Singapore|Philippines|Cambodia|Myanmar|Pakistan|Bangladesh|Nepal|UAE|Dubai)\b', re.I),
        'days': 30, 'keep': 10,
    },
}


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
        src = source
        if source == 'Google News':
            # Google titles end in " - Publisher"; the publisher is also in <source>
            src = clean(it.findtext('source')) or source
            if src != source and title.endswith(' - ' + src):
                title = title[:-len(src) - 3]
            else:
                title = re.sub(r'\s+-\s+[^-]+$', '', title)
        out.append({'title': title, 'link': link, 'pubDate': when(it.findtext('pubDate')), 'source': src})
    return out if source == 'Google News' else out[:PER_FEED]


def topic(cfg, now):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg['days'])).isoformat(timespec='seconds')
    items = []
    for source, url in cfg['feeds']:
        raw = fetch(url)
        got = [i for i in (parse(raw, source) if raw else []) if cfg['re'].search(i['title'])
               and ('need' not in cfg or cfg['need'].search(i['title']))
               and ('skip' not in cfg or not cfg['skip'].search(i['title']))]
        print(f'  topic {source}: {len(got)}')
        items += got
    seen, out = set(), []
    for i in sorted(items, key=lambda x: x['pubDate'] or '', reverse=True):
        k = re.sub(r'\W+', ' ', i['title'].lower())[:70]
        if k in seen or not i['pubDate'] or i['pubDate'] > now or i['pubDate'] < cutoff:
            continue
        seen.add(k)
        out.append(i)
    return out[:cfg['keep']]


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
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    topics = {}
    for name, cfg in TOPICS.items():
        topics[name] = topic(cfg, now) or old.get('topics', {}).get(name, [])
    payload = {'sources': ok, 'items': merged[:KEEP], 'topics': topics}
    if old:
        old.pop('generated', None)
        if old == payload:
            print('news.json: unchanged')
            return
    out = {'generated': now, **payload}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'news.json: updated ({len(merged[:KEEP])} headlines from {len(ok)} feeds)')


if __name__ == '__main__':
    main()
