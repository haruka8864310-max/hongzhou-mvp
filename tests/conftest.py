import pytest
import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    import japan_sources
    import fenghsin
    import china_sources,news_sources
    monkeypatch.setattr(china_sources,'fetch',lambda url,audit:(_ for _ in ()).throw(RuntimeError('External network disabled in unit tests')))
    monkeypatch.setattr(news_sources,'fetch',lambda url,audit:(_ for _ in ()).throw(RuntimeError('External network disabled in unit tests')))
    monkeypatch.setattr(fenghsin,'fetch',lambda url:(_ for _ in ()).throw(RuntimeError('External network disabled in unit tests')))
    monkeypatch.setattr(japan_sources,'fetch',lambda url:(_ for _ in ()).throw(RuntimeError('External network disabled in unit tests')))
    monkeypatch.setenv('HONGZHOU_DISABLE_SCHEDULER', '1')
    monkeypatch.setattr(app, 'ROOT', tmp_path / 'data')
    monkeypatch.setattr(app, 'DB', tmp_path / 'data' / 'hongzhou.db')
    monkeypatch.setattr(app, 'BACKUPS', tmp_path / 'data' / 'backups')
