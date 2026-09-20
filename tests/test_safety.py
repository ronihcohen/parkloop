import pytest
from parkloop.safety import assess_way,audit_route
from parkloop.parks import ParkGraph
from parkloop.core import Route,FootRouter,RoutingError


@pytest.mark.parametrize('tags',[
    {'highway':'motorway','sidewalk':'both','foot':'yes'},
    {'highway':'motorway_link','sidewalk':'yes'},
    {'highway':'trunk','sidewalk':'yes'},
    {'highway':'trunk_link','foot':'yes'},
    {'highway':'residential','motorroad':'yes','sidewalk':'both'},
    {'highway':'primary'}, {'highway':'secondary','foot':'yes'},
    {'highway':'residential'}, {'highway':'service','sidewalk':'no'},
    {'highway':'living_street'}, {'highway':'track','foot':'yes'},
    {'highway':'primary','sidewalk':'separate'},
    {'highway':'residential','sidewalk':'both','sidewalk:both':'no'},
    {'highway':'residential','sidewalk':'both','sidewalk:left':'no','sidewalk:right':'no'},
    {'highway':'service','sidewalk:right':'yes','sidewalk:right:access':'private'},
    {'highway':'residential','sidewalk':'yes','foot':'use_sidepath'},
    {'highway':'footway','foot':'no'}, {'highway':'path','access':'private'},
    {'highway':'cycleway'}, {'highway':'footway','construction':'yes'},
    {'highway':'footway','foot:conditional':'no @ (night)'},
    {'highway':'footway','footway':'crossing','crossing':'unmarked'},
    {'highway':'footway','motor_vehicle':'yes'},
])
def test_exclude_unsafe_or_unverified_ways(tags):
    assert not assess_way(tags).allowed


@pytest.mark.parametrize('tags',[
    {'highway':'footway'}, {'highway':'footway','footway':'sidewalk'},
    {'highway':'path'}, {'highway':'pedestrian'}, {'highway':'steps'},
    {'highway':'cycleway','foot':'designated'},
    {'highway':'footway','footway':'crossing','crossing':'traffic_signals'},
    {'highway':'primary','sidewalk':'both'},
    {'highway':'secondary','sidewalk':'right'},
    {'highway':'residential','sidewalk':'left'},
    {'highway':'service','sidewalk:both':'yes'},
    {'highway':'residential','sidewalk':'no','sidewalk:right':'yes'},
    {'highway':'path','access':'private','foot':'yes'},
])
def test_allow_explicit_pedestrian_paths_and_sidewalk_evidence(tags):
    assert assess_way(tags).allowed


def way(ids,points,tags,way_id=1):
    return {'type':'way','id':way_id,'nodes':ids,'geometry':[{'lat':a,'lon':b} for a,b in points],'tags':tags}


def test_sidewalk_separate_requires_actual_footway():
    elements=[way([1,2],[(0,0),(0,.01)],{'highway':'primary','sidewalk':'separate'}),
              way([3,4],[(.0001,0),(.0001,.01)],{'highway':'footway','footway':'sidewalk'},2)]
    graph=ParkGraph(elements,park_only=False)
    assert 1 not in graph.points and 3 in graph.points
    report=audit_route(Route(segments=[[(0,0),(0,.01)]]),elements)
    assert not report.passed and report.rejected_m>1000
    assert audit_route(Route(segments=[[(.0001,0),(.0001,.01)]]),elements).passed


def test_unknown_gpx_edges_not_approved_by_nearby_sidewalk():
    elements=[way([1,2],[(0,0),(0,.01)],{'highway':'footway'})]
    report=audit_route(Route(segments=[[(.00001,0),(.00001,.01)]]),elements)
    assert not report.passed and report.unknown_m>1000


def test_snapping_router_cannot_cross_road_without_sidewalk(monkeypatch):
    elements=[way([1,2],[(0,0),(0,.001)],{'highway':'footway'}),
              way([2,3],[(0,.001),(0,.002)],{'highway':'primary','sidewalk':'no'},2),
              way([3,4],[(0,.002),(0,.003)],{'highway':'footway'},3)]
    graph=ParkGraph(elements,park_only=False)
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw:graph)
    with pytest.raises(RoutingError,match='No connection'):
        FootRouter().calculate_segment((0,0),(0,.003))


def test_snapping_router_uses_verified_sidewalk(monkeypatch):
    elements=[way([1,2],[(0,0),(0,.003)],{'highway':'residential','sidewalk':'both'})]
    graph=ParkGraph(elements,park_only=False)
    monkeypatch.setattr('parkloop.parks.fetch_graph',lambda *a,**kw:graph)
    result=FootRouter().calculate_segment((0,0),(0,.003))
    assert audit_route(result,elements).passed


def test_gpx_coordinate_rounding_does_not_erase_road_evidence():
    points=[(32.1234567,34.1234567),(32.1245678,34.1245678)]
    elements=[way([1,2],points,{'highway':'footway'})]
    rounded=[tuple(round(v,6) for v in p) for p in points]
    report=audit_route(Route(segments=[rounded]),elements)
    assert report.passed and report.unknown_m==0


def test_rounded_road_without_sidewalk_still_fails():
    points=[(32.1234567,34.1234567),(32.1245678,34.1245678)]
    elements=[way([1,2],points,{'highway':'primary'})]
    rounded=[tuple(round(v,6) for v in p) for p in points]
    report=audit_route(Route(segments=[rounded]),elements)
    assert not report.passed and report.rejected_m>100
