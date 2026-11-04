import pytest
import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv('HONGZHOU_DISABLE_SCHEDULER', '1')
    monkeypatch.setattr(app, 'ROOT', tmp_path / 'data')
    monkeypatch.setattr(app, 'DB', tmp_path / 'data' / 'hongzhou.db')
    monkeypatch.setattr(app, 'BACKUPS', tmp_path / 'data' / 'backups')
