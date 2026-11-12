import io
import json
import pytest
from fastapi.testclient import TestClient
import app
import run_local


def test_health_and_html_redirects_are_same_service():
    with TestClient(app.app) as client:
        result=client.get('/api/health')
        assert result.status_code==200
        assert result.json()['app']==run_local.APP_ID
        assert result.json()['ui_contract']==run_local.UI_CONTRACT
        assert result.json()['database']=='ok'
        assert result.json()['data_directory']==str(app.ROOT.resolve())
        for path in ['/market.html','/index.html']:
            redirected=client.get(path)
            assert redirected.status_code==200
            assert redirected.url.path=='/'
            assert 'overviewCards' in redirected.text


def test_launcher_rejects_wrong_service_or_data_directory(tmp_path,monkeypatch):
    root=tmp_path/'expected'
    payload={'app':run_local.APP_ID,'ui_contract':run_local.UI_CONTRACT,'database':'ok','data_directory':str(root)}
    monkeypatch.setattr(run_local,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    assert run_local.read_health('http://127.0.0.1:8765',root)['database']=='ok'
    with pytest.raises(ValueError,match='不同的資料目錄'):
        run_local.read_health('http://127.0.0.1:8765',tmp_path/'production')
    payload['ui_contract']='old-version'
    with pytest.raises(ValueError,match='不是目前版本'):
        run_local.read_health('http://127.0.0.1:8765',root)
