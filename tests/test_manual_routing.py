import time

import pytest

from parkloop.core import FootRouter, Route, RoutingError
from parkloop.parks import ParkGraph
from parkloop.safety import audit_route


def way(points, **tags):
    return {'type': 'way', 'id': 1, 'nodes': list(range(1,len(points)+1)),
            'geometry': [dict(lat=p[0],lon=p[1]) for p in points],
            'tags': {'highway': 'footway', **tags}}


def test_snap_inside_long_edge_and_reuse_map(monkeypatch):
    elements=[way([(32,34),(32,34.02),(32.01,34.02)])]
    graph=ParkGraph(elements,park_only=False)
    calls=[]
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw: calls.append(a) or graph)
    router=FootRouter()
    route=router.calculate_segment((32,34.005),(32.005,34.02))
    assert route.geometry==[(32,34.005),(32,34.02),(32.005,34.02)]
    assert audit_route(route,elements).passed
    router.calculate_segment(route.geometry[-1],(32.006,34.02))
    assert len(calls)==1
    assert len(graph.points)==3  # cached graph was not split in place


def test_snapping_preserves_foot_direction(monkeypatch):
    graph=ParkGraph([way([(32,34),(32,34.02)],**{'oneway:foot':'yes'})],park_only=False)
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw: graph)
    router=FootRouter()
    assert router.calculate_segment((32,34.005),(32,34.015)).distance_m>0
    with pytest.raises(RoutingError,match='No connection'):
        router.calculate_segment((32,34.015),(32,34.005))


def test_partial_edge_audit_does_not_accept_shortcuts():
    elements=[way([(32,34),(32,34.02),(32.01,34.02)])]
    assert not audit_route(Route(segments=[[(32,34.005),(32.005,34.02)]]),elements).passed
    assert not audit_route(Route(segments=[[(32.00001,34.005),(32.00001,34.015)]]),elements).passed


@pytest.mark.parametrize('highway',['service','secondary','primary'])
def test_walking_mode_chooses_distance_over_verification_preference(monkeypatch,highway):
    start,end=(32,34),(32,34.002)
    shortcut=way([start,end],highway=highway)
    detour=way([start,(32.001,34),(32.001,34.002),end])
    detour.update(id=2,nodes=[1,3,4,2])
    elements=[shortcut,detour]
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw:ParkGraph(elements,**kw))
    shortest=FootRouter(include_unverified=True).calculate_segment(start,end)
    verified=FootRouter().calculate_segment(start,end)
    assert shortest.geometry==[start,end]
    assert shortest.distance_m<verified.distance_m
    assert 'unverified' in shortest.description
    assert len(verified.geometry)==4
    if highway in {'primary','secondary'}:
        auto_graph=ParkGraph(elements,park_only=False,include_unverified=True)
        assert 2 not in auto_graph.edges[1]


def test_missing_sidewalk_connection_is_explicitly_unverified(monkeypatch):
    elements=[way([(32,34),(32,34.002),(32.002,34.002)],highway='service')]
    def fetch(*a,**kw):
        return ParkGraph(elements,**kw)
    monkeypatch.setattr('parkloop.parks.fetch_graph',fetch)
    route=FootRouter(include_unverified=True).calculate_segment((32,34.001),(32.001,34.002))
    assert len(route.geometry)==3
    assert 'unverified' in route.description
    assert audit_route(route,elements,include_unverified=True).unknown_m>0
    with pytest.raises(RoutingError):
        FootRouter().calculate_segment((32,34.001),(32.001,34.002))


@pytest.mark.parametrize('tags',[
    {'highway':'service','sidewalk':'no'},
    {'highway':'service','access':'private'},
    {'highway':'primary','sidewalk':'no'},
    {'highway':'motorway'},
    {'highway':'service','foot':'no'},
])
def test_unverified_mode_still_excludes_restricted_roads(monkeypatch,tags):
    elements=[way([(32,34),(32,34.002)],**tags)]
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw:ParkGraph(elements,**kw))
    with pytest.raises(RoutingError):
        FootRouter(include_unverified=True).calculate_segment((32,34),(32,34.002))


def test_drag_reroutes_and_failure_preserves_route(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from parkloop.app import Window
    from parkloop.mapview import MapView
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *a:None)
    elements=[way([(32,34),(32,34.002),(32.002,34.002)])]
    graph=ParkGraph(elements,park_only=False)
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw:graph)
    w=Window()
    original=Route(segments=[[(32,34),(32,34.002)]])
    w.commit(original)
    def finish():
        deadline=time.monotonic()+2
        while w.busy and time.monotonic()<deadline:
            app.processEvents();time.sleep(.001)
        assert not w.busy
    w.move_point(0,1,32.002,34.002);finish()
    assert w.route.geometry==[(32,34),(32,34.002),(32.002,34.002)]
    w.undo_route()
    assert w.route.geometry==original.geometry
    w.redo_route()
    w.insert_point(0,1,32,34.001);finish()
    assert w.route.geometry==[(32,34),(32,34.001),(32,34.002),(32.002,34.002)]
    w.delete_point(0,1);finish()
    assert w.route.geometry==[(32,34),(32,34.002),(32.002,34.002)]
    w.move_point(0,1,32,34.001);finish()
    assert audit_route(w.route,elements).passed
    before=w.route.geometry
    w.move_point(0,2,33,35);finish()
    assert w.route.geometry==before
    w.close()
