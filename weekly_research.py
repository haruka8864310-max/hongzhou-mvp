"""Auditable weekly PIT view, persisted separately from company/source databases."""
import json
import sqlite3
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import scrap_analysis as sa

APPLICATION_ID=0x485A5752


def friday_cutoff(target_monday):
    """Taipei Friday 23:59:59 before an actual Monday quote.

    U.S. Friday closing data are NOT yet public at this cutoff; publication
    timestamps, not matching calendar dates, control whether they can enter.
    """
    day=date.fromisoformat(target_monday)
    if day.weekday()!=0:raise ValueError('目標必須為星期一，未把假日後報價當作週一')
    return sa.endday((day-timedelta(days=3)).isoformat())


def prepare(as_of=None,max_age_days=35,target_id=None,chosen=None):
    import scrap_forecast as sf
    now=sa.timestamp(as_of) if as_of else sa.clock()
    data=sa.datasets(now.isoformat())
    defaults=sa.catalog(now.isoformat())['defaults']
    target_id,chosen=target_id or defaults['a'],chosen or defaults['b']
    if target_id not in data:
        raise ValueError('缺少豐興廢鋼絕對價格；未用其他價格代替')
    if data[target_id]['market']!='tw_scrap' or data[target_id]['origin']!='fenghsin_facts':
        raise ValueError('預測目標必須為豐興廢鋼收購價')
    sources=sf.feature_sources(data,chosen)
    slots={'fenghsin':target_id,**sources}
    weeks=sa.week_rows(data[target_id])
    vintages={}
    rows=[]
    for week in sorted(weeks)[-156:]:
        cutoff=min(sa.endday(sf.week_end(week)),now)
        vintage=sa.datasets(cutoff.isoformat())
        vintages[week]=vintage
        day=cutoff.astimezone(sa.TZ).date().isoformat()
        for slot,key in slots.items():
            series=vintage.get(key)
            p=sa.latest(series['points'],day,sf.observation_age_limit(series,max_age_days),cutoff) if series else None
            # Metadata of a later-acquired quote can explain a blocked row;
            # its PRICE is always NULL. It cannot enter model features.
            current=data.get(key)
            candidate=sa.latest(current['points'],day,sf.observation_age_limit(current,max_age_days)) if current else None
            evidence=p or candidate
            status='known' if p else 'not_known_at_origin' if candidate else 'missing_or_stale' if current else 'source_unavailable'
            rows.append({'week':week,'origin':cutoff.isoformat(),'slot':slot,'series_id':key,
                'market':current['market'] if current else slot,'scope':current.get('scope') if current else None,
                'currency':current.get('currency') if current else None,'unit':current.get('unit') if current else None,
                'frequency':current.get('frequency') if current else None,
                'price':p['price'] if p else None,'observation_id':evidence['id'] if evidence else None,
                'quote_date':evidence['quote_date'] if evidence else None,'published_at':evidence.get('published_at') if evidence else None,
                'published_date':evidence.get('published_date') if evidence else None,
                'known_at':evidence['known_at'] if evidence else None,'retrieved_at':evidence.get('retrieved_at') if evidence else None,
                'first_seen_at':evidence.get('first_seen_at') if evidence else None,
                'source':evidence.get('source') if evidence else None,'source_url':evidence.get('source_url') if evidence else None,
                'age_days':evidence.get('age_days') if evidence else None,'status':status})
    summary={'as_of':now.isoformat(),'weeks':len(weeks),'rows':len(rows),
             'age_policy_days':{'daily':7,'weekly_or_observed':max_age_days,'monthly':95,'annual':370},
             'states':dict(Counter(r['status'] for r in rows)),
             'known_target_weeks':sum(r['slot']=='fenghsin' and r['status']=='known' for r in rows),
             'source_inventory':[{'slot':slot,'id':key,'count':len(data[key]['points']) if key in data else 0,
                 'earliest':data[key]['points'][0]['quote_date'] if key in data else None,
                 'latest':data[key]['points'][-1]['quote_date'] if key in data else None} for slot,key in slots.items()]}
    return {'now':now,'data':data,'target_id':target_id,'chosen':chosen,'sources':sources,'vintages':vintages,'rows':rows,'summary':summary}


def persist(context,folder):
    """Append-only derived research sidecar; never create/replace hongzhou.db."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'weekly-analysis.sqlite3'
    existed=path.exists()
    run_id=uuid.uuid4().hex
    with sqlite3.connect(path) as c:
        if existed and c.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:
            raise ValueError('拒絕寫入非本研究工具建立的資料庫')
        c.execute(f'PRAGMA application_id={APPLICATION_ID}')
        c.execute('''CREATE TABLE IF NOT EXISTS research_runs(
            id TEXT PRIMARY KEY,created_at TEXT NOT NULL,as_of TEXT NOT NULL,summary_json TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS weekly_observations(
            run_id TEXT NOT NULL REFERENCES research_runs(id),week TEXT NOT NULL,origin TEXT NOT NULL,
            slot TEXT NOT NULL,series_id TEXT,market TEXT,scope TEXT,currency TEXT,unit TEXT,frequency TEXT,
            price REAL,observation_id TEXT,quote_date TEXT,published_at TEXT,published_date TEXT,
            known_at TEXT,retrieved_at TEXT,first_seen_at TEXT,source TEXT,source_url TEXT,age_days INTEGER,
            status TEXT NOT NULL,PRIMARY KEY(run_id,week,slot),
            CHECK(price IS NULL OR (status='known' AND known_at<=origin)))''')
        c.execute('INSERT INTO research_runs VALUES(?,?,?,?)',(run_id,sa.clock().isoformat(),context['summary']['as_of'],json.dumps(context['summary'],ensure_ascii=False)))
        for row in context['rows']:
            record={'run_id':run_id,**row}
            keys=list(record)
            c.execute(f"INSERT INTO weekly_observations({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",list(record.values()))
    return {'run_id':run_id,'database':str(path.resolve()),'rows':len(context['rows'])}
