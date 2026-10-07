"""Census of Population and Housing by district -> data/census-districts.json

Source: Department of Census and Statistics Census Data Portal
https://www.statistics.gov.lk/DashBoard/censusdataportal/ (2024 and 2012 censuses).
The portal's own endpoint fetch_table_download_data_locs.php returns one JSON
record per line for a whole table (sl_table_YEAR = Sri Lanka, d_table_YEAR =
the 25 districts). Variable names come from alldatasqlite.php.

Each topic keeps a few readable groups (some portal categories are merged,
e.g. the four pipe-borne water sources). Group sums are checked against the
topic total; a topic whose groups miss the total by more than 1 % is dropped.
Census figures only change when a new census is released, so the file is
rewritten only when the content changes. Standard library only.
"""
import datetime as dt, json, os, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'census-districts.json')
BASE = 'https://www.statistics.gov.lk/DashBoard/censusdataportal/'
UA = 'Mozilla/5.0 (compatible; CeylonPulse-data/1.0; +https://github.com/investwithdon7-rgb/ceylonpulse-data)'

# topic: (label, unit, total variable, [(group label, [variables])])
TOPICS = {
    'sector': ('Urban or rural', 'people', 'tot_Sec', [
        ('Urban', ['tot_Sec_Ur', 'tot_Sec_UrEs']), ('Rural', ['tot_Sec_Ru']), ('Estate', ['tot_Sec_RuEs', 'tot_Sec_Es'])]),
    'ethnicity': ('Ethnic group', 'people', 'tot_Eth', [
        ('Sinhalese', ['tot_Eth_Sinhalese']), ('Sri Lankan Tamil', ['tot_Eth_SL_Tamil']), ('Indian Tamil', ['tot_Eth_Ind_Tamil']),
        ('Sri Lanka Moor', ['tot_Eth_SL_Moor']),
        ('Other', ['tot_Eth_Burgher', 'tot_Eth_Malay', 'tot_Eth_SL_Chetty', 'tot_Eth_Bharatha', 'tot_Eth_Veddhas', 'tot_Eth_Other'])]),
    'religion': ('Religion', 'people', 'tot_Ra', [
        ('Buddhist', ['tot_Ra_Buddhist']), ('Hindu', ['tot_Ra_Hindu']), ('Islam', ['tot_Ra_Islam']),
        ('Roman Catholic', ['tot_Ra_Roman_Catholic']), ('Other Christian', ['tot_Ra_Other_Christian']), ('Other', ['tot_Ra_Other'])]),
    'education': ('Highest education (age 5+)', 'people', 'tot_EA', [
        ('Never attended school', ['tot_EA_Neverattendedschool']), ('Primary', ['tot_EA_Primary']), ('Secondary', ['tot_EA_Secondary']),
        ('O/L', ['tot_EA_G_C_E_OL']), ('A/L', ['tot_EA_G_C_E_AL']), ('Degree and above', ['tot_EA_Degreeandabove']),
        ('Special school', ['tot_EA_specialschool'])]),
    'work': ('Work status (age 15+)', 'people', 'tot_EcoAct', [
        ('Employed', ['tot_EcoAct_Emp']), ('Unemployed', ['tot_EcoAct_Unemp']), ('Not in the labour force', ['tot_EcoAct_ENA'])]),
    'migration': ('Why people moved district', 'migrants', 'tot_MRFM', [
        ('Marriage', ['tot_MRFM_marriage']), ('Work or job search', ['tot_MRFM_emp']), ('Education', ['tot_MRFM_education']),
        ('With family', ['tot_MRFM_accompanied']), ('Returned home', ['tot_MRFM_returning']),
        ('Displacement or disaster', ['tot_MRFM_resettled', 'tot_MRFM_disaster', 'tot_MRFM_devprojects']), ('Other', ['tot_MRFM_other'])]),
    'water': ('Drinking water source', 'households', 'tot_SODWHU', [
        ('Pipe-borne', ['SODWHU_nationalwatersupply', 'SODWHU_localauthority', 'SODWHU_community', 'SODWHU_privatewatersupplyproject']),
        ('Protected well', ['SODWHU_protectedwell']), ('Other well', ['SODWHU_semiprotectedwell', 'SODWHU_unprotectedwell']),
        ('Tube well', ['SODWHU_tubewell']), ('Bottled or filtered', ['SODWHU_bottledwater', 'SODWHU_filterwater']),
        ('Other', ['SODWHU_Spring', 'SODWHU_tankriverstreams', 'SODWHU_rainwater', 'SODWHU_bowser', 'SODWHU_other'])]),
    'cooking': ('Cooking fuel', 'households', 'tot_HCF', [
        ('Firewood', ['HCF_firewood']), ('Gas', ['HCF_gas']), ('Electricity', ['HCF_electricity']), ('Kerosene', ['HCF_kerosene']),
        ('Other or none', ['HCF_sawdust', 'HCF_biogas', 'HCF_other', 'HCF_not_relavent'])]),
    'lighting': ('Lighting', 'households', 'tot_HL', [
        ('Grid electricity', ['HL_electricity']), ('Solar', ['HL_solar']), ('Kerosene lamp', ['HL_kerosenelamp']),
        ('Other', ['HL_biogas', 'HL_generator', 'HL_other'])]),
    'toilet': ('Toilet', 'households', 'tot_HTF', [
        ('Own toilet', ['HTF_withinexclusively', 'HTF_premisesexclusively']),
        ('Shared toilet', ['HTF_withinsharing', 'HTF_premisessharing', 'HTF_nosharing']),
        ('Public toilet', ['HTF_publictoilet']), ('No toilet', ['HTF_notusing'])]),
    'housing': ('Type of house', 'housing units', 'tot_THU', [
        ('Permanent', ['THU_Permanent']), ('Semi-permanent', ['THU_Semi_Permenent']), ('Improvised', ['THU_Improvised']),
        ('Unclassified', ['THU_Unclassified'])]),
}


def table(name, year):
    body = json.dumps({'table': f'{name}_{year}', 'year': year})
    r = subprocess.run(['curl', '-sS', '--fail', '-m', '180', '-A', UA, '-H', 'Content-Type: application/json',
                        '-H', f'Referer: {BASE}', '-X', 'POST', BASE + 'fetch_table_download_data_locs.php', '-d', body],
                       capture_output=True)
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.decode('utf-8', 'replace').splitlines():
        line = line.strip().rstrip(',')
        if line.startswith('{'):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out or None


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def topic_values(rec, total_var, groups):
    total = num(rec.get(total_var))
    if not total:
        return None
    vals = [sum(num(rec.get(v)) or 0 for v in vs) for _, vs in groups]
    if abs(sum(vals) - total) > total * 0.01:
        return None
    return {'total': total, 'values': vals}


def build_year(year):
    sl, dist = table('sl_table', year), table('d_table', year)
    if not sl or not dist:
        return None
    rec_sl = sl[0]
    out = {'population': num(rec_sl.get('tot_Pop')), 'topics': {}, 'districts': []}
    for d in sorted(dist, key=lambda r: r.get('ADM_ID', '')):
        out['districts'].append({'id': d.get('ADM_ID'), 'name': d.get('ADM_NAME'), 'population': num(d.get('tot_Pop')),
                                 'male': num(d.get('m_tot_Pop')), 'female': num(d.get('f_tot_Pop'))})
    for key, (label, unit, total_var, groups) in TOPICS.items():
        nat = topic_values(rec_sl, total_var, groups)
        if not nat:
            print(f'  {year} {key}: national groups do not add up; skipped')
            continue
        by = {}
        for d in dist:
            v = topic_values(d, total_var, groups)
            if v:
                by[d.get('ADM_NAME')] = v
        out['topics'][key] = {'label': label, 'unit': unit, 'groups': [g for g, _ in groups], 'national': nat, 'districts': by}
        print(f'  {year} {key}: national ok, {len(by)} districts')
    return out


def main():
    years = {}
    for y in (2024, 2012):
        b = build_year(y)
        if b:
            years[str(y)] = b
    if '2024' not in years:
        raise SystemExit('2024 tables unavailable; keeping previous file')
    out = {'source': 'Department of Census and Statistics — Census of Population and Housing (Census Data Portal)',
           'url': BASE, 'years': years}
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding='utf-8'))
        old.pop('generated', None)
        if old == out:
            print('census-districts.json: unchanged')
            return
    out = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **out}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', OUT, os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()
