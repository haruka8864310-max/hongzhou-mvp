import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
import app, weekly_research as wr, scrap_forecast as sf
from test_scrap_analysis import prepare

spec=importlib.util.spec_from_file_location('research_cli',Path(__file__).resolve().parents[1]/'scripts/research_cli.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


def test_scores_recall_and_baselines_do_not_invent_accuracy():
    assert sf.classification_metrics([])['accuracy'] is None
    tests=[{'actual':'up','prediction':'up'},{'actual':'up','prediction':'flat'},
           {'actual':'down','prediction':'down'}]
    r=sf.classification_metrics(tests)
    assert r['accuracy']==pytest.approx(2/3)
    assert r['recall']=={'down':1,'flat':None,'up':.5}
    assert r['confusion_matrix']['up']['flat']==1
    rows=[{'origin':'2025-01-05T23:59:59Z','outcome_known_at':'2025-01-12T00:00:00Z',
           'label':'up','features':{'fh_change_1w':-.1},'previous_direction':'down'},
          {'origin':'2025-01-06T23:59:59Z','outcome_known_at':'2025-01-13T00:00:00Z',
           'label':'flat','features':{'fh_change_1w':0},'previous_direction':'flat'}]
    b=sf.baseline_backtest(rows)
    assert b['previous_direction']['accuracy']==.5
    assert b['flat']['accuracy']==.5


def test_weekly_table_preserves_provenance_and_blocks_future(monkeypatch,tmp_path):
    with TestClient(app.app):
        prepare(monkeypatch)
        ctx=wr.prepare()
        assert ctx['summary']['weeks']==2
        assert any(r['status']=='not_known_at_origin' for r in ctx['rows'])
        assert all(r['price'] is None or (r['known_at']<=r['origin'] and r['source_url']) for r in ctx['rows'])
        saved=wr.persist(ctx,tmp_path/'analytics')
        again=wr.persist(ctx,tmp_path/'analytics')
        with sqlite3.connect(saved['database']) as c:
            assert c.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0]==2
            assert c.execute('SELECT COUNT(*) FROM weekly_observations').fetchone()[0]==len(ctx['rows'])*2
            assert c.execute("SELECT COUNT(*) FROM weekly_observations WHERE status!='known' AND price IS NOT NULL").fetchone()[0]==0
            assert c.execute('SELECT COUNT(*) FROM weekly_observations WHERE source_url IS NOT NULL').fetchone()[0]>0
        assert saved['run_id']!=again['run_id']
        other=tmp_path/'other';other.mkdir()
        with sqlite3.connect(other/'weekly-analysis.sqlite3') as c:c.execute('CREATE TABLE precious(id)')
        with pytest.raises(ValueError):wr.persist(ctx,other)


def test_cli_real_data_reports_blocked_training_without_fake_model(monkeypatch,tmp_path,capsys):
    with TestClient(app.app):
        prepare(monkeypatch)
        for command in ['weekly','train','backtest']:
            rc=cli.main([command,'--output-dir',str(tmp_path/'reports')])
            report=json.loads(capsys.readouterr().out)
            assert rc==(0 if command=='weekly' else 2)
            assert report['data']['weeks']==2
            if command=='train':assert all(not m['trained'] for m in report['models'])
            if command=='backtest':
                assert all(f['accuracy'] is None and f['metrics']['recall']['up'] is None for f in report['forecasts'])
                assert all(f['missing_training_samples']==52 and f['missing_test_samples']==20 for f in report['forecasts'])
        assert not list((tmp_path/'reports').rglob('candidate-*.txt'))


def test_cli_engine_calls_real_packages_for_four_horizons(tmp_path):
    pytest.importorskip('skforecast');pytest.importorskip('lightgbm')
    r=cli.verify_engine(tmp_path/'test-engine-only')
    assert r['market_data'] is False
    assert all(h['trained'] and h['rolling_tests']>=20 for h in r['horizons'])
    assert len(list((tmp_path/'test-engine-only').glob('candidate-*.txt')))==4
