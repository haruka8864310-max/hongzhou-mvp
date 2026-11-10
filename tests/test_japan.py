from datetime import datetime,timezone
from urllib.error import HTTPError
from fastapi.testclient import TestClient
import app,japan_sources as jp,global_market as gm
import re


def layout():
    # Synthetic text only; no publisher PDF or real price values in Git.
    header=' '.join(jp.PLANTS)
    rows=['2025年10月1日','国内鉄スクラップ購入価格表','適用開始日：2025年10月2日',header]
    prices=f'{jp.GRADES[0]:<18}'+''.join(f'{10000+i*100:>10,}' for i in range(8))
    centres=[int((m.start()+m.end())/2) for m in re.finditer(r'\d{1,3},\d{3}',prices)]
    for indices,term in [(range(8),'陸上'),((0,2,5),'海上')]:
        line=[' ']*120
        for i in indices:line[centres[i]-1:centres[i]+1]=term
        rows.append(''.join(line))
    for grade in jp.GRADES:rows.append(f'{grade:<18}'+''.join(f'{10000+i*100:>10,}' for i in range(8)))
    return '\n'.join(rows)


def test_pdf_layout_mapping_strict():
    parsed=jp.parse_layout(layout(),'2025-10-02')
    assert len(parsed['prices'])==64
    assert parsed['prices'][0]==('田原工場','電特A','海上／陸上同欄',10000)
    assert parsed['prices'][7][0]=='東京湾岸'
    assert parsed['prices'][7][-1]==10700
    for bad in [layout().replace('田原工場','UNKNOWN'),layout().replace('10,700',''),layout().replace('2025年10月2日','2025年10月3日'),layout().replace('海上','陸上')]:
        try:jp.parse_layout(bad,'2025-10-02');assert False,'bad layout accepted'
        except ValueError:pass


def test_connector_atomic_versions_failure_and_cooldown(monkeypatch):
    with TestClient(app.app) as c:
        monkeypatch.setattr(jp,'robots',lambda:(None,'TEST allow'))
        urls=[('2025-10-02',jp.PREFIX+'2025/2025.10.02.pdf'),('2025-10-01',jp.PREFIX+'2025/2025.10.01.pdf')]
        monkeypatch.setattr(jp,'links',lambda _:urls)
        monkeypatch.setattr(jp,'fetch',lambda _:b'TEST fixture')
        monkeypatch.setattr(jp,'parse_pdf',lambda raw,day:{**jp.parse_layout(layout(),'2025-10-02'),'quote_date':day})
        r=c.post('/api/japan/update').json();assert r['ok'] and r['status']['new_versions']==128
        rows=gm.available();assert len(rows)==128
        series=rows[0]['series_id'];assert len(c.get('/api/global/history',params={'series':series,'months':0}).json()['points'])==2
        assert c.post('/api/japan/update').json()['skipped']
        saved=jp.state();saved['checked_at']='2020-01-01T00:00:00+00:00';jp.save_state(saved)
        monkeypatch.setattr(jp,'fetch',lambda _:(_ for _ in ()).throw(OSError('TEST network offline')))
        failed=c.post('/api/japan/update').json();assert not failed['ok']
        assert jp.state()['success_at']==r['status']['success_at']
        assert len(gm.available())==128
        assert c.get('/api/japan/status').json()['jisri']['stored']==0
        assert '第三方' in c.get('/api/japan/status').json()['jisri']['reason']
        backup=c.get('/api/portable-backup').content
        assert c.post('/api/restore',files={'file':('backup.zip',backup)}).status_code==200
        assert len(gm.available())==128


def test_known_official_links_only_and_robots_denial(monkeypatch):
    raw=('<a href="'+jp.PREFIX+'2025/2025.10.02.pdf">table</a><a href="https://example.com/price.pdf">other</a>').encode()
    assert len(jp.links(raw))==1
    with TestClient(app.app) as c:
        monkeypatch.setattr(jp,'robots',lambda:(_ for _ in ()).throw(PermissionError('TEST robots disallow')))
        result=c.post('/api/japan/update').json();assert not result['ok']
        assert gm.available()==[]
        assert 'robots' in result['message']


def legacy_layout(suspended=False):
    plants=jp.PLANTS if suspended else jp.PLANTS[:7]
    centers=[22+i*12 for i in range(len(plants))]
    header=[' ']*125
    for p,x in zip(plants,centers):header[x-len(p)//2:x-len(p)//2+len(p)]=p
    lines=['2025年1月1日','国内鉄スクラップ購入価格表','2025年1月2日',''.join(header)]
    for indices,term in [(range(len(plants)),'陸上'),((0,2,5),'海上')]:
        row=[' ']*125
        for i in indices:row[centers[i]-1:centers[i]+1]=term
        lines.append(''.join(row))
    for grade in jp.GRADES:
        row=[' ']*125;row[:len(grade)]=grade
        for i,x in enumerate(centers):
            if suspended and i==4:continue
            row[x-3:x+3]='10,000'
        lines.append(''.join(row))
    if suspended:
        row=[' ']*125;row[centers[4]-1:centers[4]+2]='休止中';lines.append(''.join(row))
    return '\n'.join(lines)


def test_legacy_seven_site_and_explicit_suspended_column():
    assert len(jp.parse_legacy_layout(legacy_layout(),'2025-01-02')['prices'])==56
    result=jp.parse_legacy_layout(legacy_layout(True),'2025-01-02')
    assert len(result['prices'])==56
    assert not any(p[0]=='高松' for p in result['prices'])
    assert any(p[0]=='東京湾岸' for p in result['prices'])
    import pytest
    for text in [legacy_layout(True).replace('休止中',''),legacy_layout().replace('10,000','',1)]:
        with pytest.raises(ValueError):jp.parse_legacy_layout(text,'2025-01-02')
