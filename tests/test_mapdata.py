import json
import pytest
from parkloop import mapdata, parks
from parkloop.core import RoutingError


ELEMENTS=[{'type':'way','id':1,'nodes':[1,2],
           'geometry':[{'lat':32,'lon':34},{'lat':32,'lon':34.01}],
           'tags':{'highway':'footway'}}]


def test_saved_download_survives_service_failure(monkeypatch):
    calls=[]
    monkeypatch.setattr(parks,'fetch_json',lambda *a,**kw:calls.append(a) or {'elements':ELEMENTS})
    assert parks.fetch_elements((32,34),4000)==ELEMENTS
    monkeypatch.setattr(parks,'fetch_json',lambda *a,**kw:pytest.fail('network should not be used'))
    assert parks.fetch_elements((32.001,34),1000)==ELEMENTS
    assert len(calls)==1
    assert mapdata.cached_elements((33,35),1000,'https://overpass-api.de/api/interpreter') is None


def test_offline_file_routes_without_network(tmp_path,monkeypatch):
    path=tmp_path/'map.json';path.write_text(json.dumps(ELEMENTS))
    monkeypatch.setattr(mapdata,'offline_path',str(path))
    assert parks.fetch_elements((32,34),4000)==ELEMENTS
    from parkloop.core import FootRouter
    assert FootRouter().calculate_segment((32,34.002),(32,34.008)).distance_m>0


def test_invalid_offline_file_does_not_silently_use_network(tmp_path,monkeypatch):
    path=tmp_path/'map.json';path.write_text('{}')
    monkeypatch.setattr(mapdata,'offline_path',str(path))
    with pytest.raises(RoutingError,match='Offline map'):
        parks.fetch_elements((32,34),4000)


def test_partial_download_not_cached(monkeypatch):
    monkeypatch.setattr(parks,'fetch_json',lambda *a,**kw:{'elements':ELEMENTS,'remark':'timeout'})
    with pytest.raises(RoutingError,match='incomplete'):
        parks.fetch_elements((32,34),4000)
    assert not mapdata.cache_dir().exists()


def test_worldwide_downloads_follow_selected_area_and_reuse_each_cache(monkeypatch):
    from urllib.parse import parse_qs
    cities=[(51.5074,-0.1278),(35.6762,139.6503),(-33.8688,151.2093)]
    queries=[]
    def download(url,**kwargs):
        query=parse_qs(kwargs['data'].decode())['data'][0]
        queries.append(query)
        lat,lon=cities[len(queries)-1]
        return {'elements':[{'type':'way','id':len(queries),'nodes':[1,2],
                            'geometry':[{'lat':lat,'lon':lon},{'lat':lat+.001,'lon':lon}],
                            'tags':{'highway':'footway'}}]}
    monkeypatch.setattr(parks,'fetch_json',download)
    results=[parks.fetch_elements(city,4000) for city in cities]
    for city,query in zip(cities,queries):
        assert f'{city[0]:.6f},{city[1]:.6f}' in query
    for city,expected in zip(cities,results):
        assert parks.fetch_elements(city,4000)==expected
    assert len(queries)==3
