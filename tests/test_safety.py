import io
import sqlite3
import zipfile
import pytest
from fastapi.testclient import TestClient
import app


@pytest.fixture
def client():
    with TestClient(app.app) as c:
        yield c


def test_ui_and_customer_trade_flow(client):
    page = client.get('/')
    assert page.status_code == 200
    assert 'compareform' in page.text
    assert 'restoreform' in client.get('/legacy').text
    assert client.post('/api/customers', json={'name': '測試專用'}).status_code == 200
    customer = client.get('/api/customers').json()[0]
    trade = dict(date='2026-11-04', customer_id=customer['id'], kind='buy', material='測試鐵', kg=100, unit_price=10)
    assert client.post('/api/trades', json=trade).status_code == 200
    assert client.get('/api/summary').json()[0]['balance_kg'] == 100
    assert client.post('/api/trades', json={**trade, 'kg': -1}).status_code == 422
    assert client.post('/api/trades', json={**trade, 'date': '2026-02-30'}).status_code == 400
    assert client.post('/api/trades', json={**trade, 'customer_id': 999}).status_code == 400
    assert client.post('/api/customers', json={'name': '   '}).status_code == 422


def test_restore_preserves_current_snapshot(client):
    client.post('/api/customers', json={'name': '備份內容'})
    snapshot = client.get('/api/portable-backup')
    client.post('/api/customers', json={'name': '還原前資料'})
    response = client.post('/api/restore', files={'file': ('snapshot.zip', snapshot.content)})
    assert response.status_code == 200
    assert len(client.get('/api/customers').json()) == 1
    previous = list(app.BACKUPS.glob('before-restore-*.db'))
    assert len(previous) == 1
    with app.connect(previous[0]) as c:
        assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == 2


@pytest.mark.parametrize('payload', [b'not sqlite', b'PK invalid zip'])
def test_invalid_restore_keeps_data(client, payload):
    client.post('/api/customers', json={'name': '保留資料'})
    before = app.DB.read_bytes()
    assert client.post('/api/restore', files={'file': ('bad.zip', payload)}).status_code == 400
    assert app.DB.read_bytes() == before


def test_duplicate_zip_rejected(client):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('hongzhou.db', app.DB.read_bytes())
        with pytest.warns(UserWarning):
            z.writestr('hongzhou.db', app.DB.read_bytes())
    assert client.post('/api/restore', files={'file': ('bad.zip', stream.getvalue())}).status_code == 400


def test_schema_mismatch_and_newer_version(client, tmp_path):
    invalid = tmp_path / 'invalid.db'
    with sqlite3.connect(invalid) as c:
        for table in ('prices', 'customers', 'trades'):
            c.execute(f'CREATE TABLE {table}(id INTEGER)')
    with pytest.raises(ValueError):
        app.restore_database(invalid)
    backup = app.backup_database(as_zip=False)
    with app.connect(backup) as c:
        c.execute('INSERT INTO schema_migrations(version) VALUES(99)')
    with pytest.raises(ValueError):
        app.restore_database(backup)


def test_local_request_protection(client):
    assert client.get('/', headers={'host': 'attacker.example'}).status_code == 400
    assert client.post('/api/backup', headers={'origin': 'https://attacker.example'}).status_code == 403


def test_initialization_is_idempotent(client):
    client.post('/api/customers', json={'name': '既有資料'})
    app.setup_database()
    app.setup_database()
    assert len(client.get('/api/customers').json()) == 1
    with app.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 5
