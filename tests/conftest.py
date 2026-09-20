import pytest


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch,tmp_path):
    monkeypatch.setattr('parkloop.mapdata.cache_dir',lambda:tmp_path/'maps')
    monkeypatch.setattr('parkloop.mapdata.offline_path',None)
    monkeypatch.delenv('PARKLOOP_OFFLINE_MAP',raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Tests must use explicit map fixtures, not live network services')
    monkeypatch.setattr('parkloop.core.fetch_json', forbidden)
    monkeypatch.setattr('parkloop.parks.fetch_json', forbidden)
