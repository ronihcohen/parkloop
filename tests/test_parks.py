import threading
import pytest
from parkloop.parks import ParkGraph,generate_park_route,park_geometry
from parkloop.core import RoutingError,route_from_geometry,haversine_m


def way(ids,points,tags):
    return {'type':'way','nodes':ids,'geometry':[{'lat':a,'lon':b} for a,b in points],'tags':tags}


def fixture():
    area=way([],[(0,0),(0,.1),(.1,.1),(.1,0),(0,0)],{'leisure':'park'})
    ring=[(.02,.02),(.02,.04),(.04,.04),(.04,.02),(.02,.02)]
    return [area,way([1,2,3,4,1],ring,{'highway':'footway'})]


class NoService:
    def calculate_segment(self,*args):raise AssertionError('No network calls expected')


def test_park_loop_is_contained_and_closed():
    g=ParkGraph(fixture());start=g.points[1]
    target=sum(g.edges[a][b] for a,b in [(1,2),(2,3),(3,4),(4,1)])
    r=generate_park_route(start,target,NoService(),graph=g,seed=4)
    assert abs(r.route.distance_m-target)/target<.05
    assert r.route.geometry[0]==r.route.geometry[-1]==start
    assert r.park_fraction==pytest.approx(1)
    assert r.repeated_fraction==0


def test_private_and_nonpedestrian_edges_rejected():
    elements=fixture()+[way([1,5],[(.02,.02),(.06,.06)],{'highway':'path','access':'private'}),way([1,6],[(.02,.02),(.07,.07)],{'highway':'cycleway'})]
    g=ParkGraph(elements)
    assert 5 not in g.points and 6 not in g.points


def test_holes_and_edges_crossing_holes_excluded():
    outer=fixture()[0]['geometry']
    inner=[{'lat':a,'lon':b} for a,b in [(.045,.045),(.045,.055),(.055,.055),(.055,.045),(.045,.045)]]
    relation={'type':'relation','tags':{'leisure':'park'},'members':[{'role':'outer','geometry':outer},{'role':'inner','geometry':inner}]}
    g=ParkGraph([relation,way([1,2],[(.03,.05),(.07,.05)],{'highway':'path'})])
    assert not g.points


def test_strict_refuses_outside_start():
    with pytest.raises(RoutingError,match='outside'):
        generate_park_route((-.01,0),10000,NoService(),graph=ParkGraph(fixture()),strict=True)


def test_cancel_does_not_produce_route():
    cancel=threading.Event();cancel.set()
    with pytest.raises(RoutingError,match='Cancelled'):
        generate_park_route((.02,.02),10000,NoService(),graph=ParkGraph(fixture()),cancel=cancel)


def test_disconnected_short_component_does_not_fake_target():
    with pytest.raises(RoutingError,match='without repeated paths'):
        generate_park_route((.02,.02),30000,NoService(),graph=ParkGraph(fixture()),seed=1)


def test_prefer_parks_allows_bridge_outside_boundary_to_complete_loop():
    # Northern path sits outside the park polygon. Keeping it creates a real
    # mostly-park loop; removing it forces an out-and-back on the same paths.
    elements=fixture()
    elements[0]=way([],[(0,0),(0,.1),(.039,.1),(.039,0),(0,0)],{'leisure':'park'})
    g=ParkGraph(elements,park_only=False)
    target=sum(g.edges[a][b] for a,b in [(1,2),(2,3),(3,4),(4,1)])
    result=generate_park_route(g.points[1],target,NoService(),graph=g,seed=4)
    assert result.route.geometry[0]==result.route.geometry[-1]
    assert .7 < result.park_fraction < .8
    assert result.repeated_fraction==0
    assert result.route.distance_m==pytest.approx(target)


def test_loop_quality_prefers_good_loop_to_exact_distance_retracing():
    from parkloop.parks import loop_quality
    assert loop_quality(10200,10000,.94,0) < loop_quality(10000,10000,1,.15)
    assert loop_quality(10000,10000,.9,0) < loop_quality(10000,10000,.6,0)
    assert loop_quality(11000,10000,1,0) > loop_quality(10000,10000,.9,0)


@pytest.mark.parametrize('strict',[False,True])
def test_start_on_dead_end_cannot_return_along_shared_access_path(strict):
    elements=fixture()+[way([5,1],[(.019,.02),(.02,.02)],{'highway':'footway'})]
    graph=ParkGraph(elements,park_only=strict)
    target=sum(graph.edges[a][b] for a,b in [(1,2),(2,3),(3,4),(4,1)])+2*graph.edges[5][1]
    with pytest.raises(RoutingError,match='without repeated paths'):
        generate_park_route(graph.points[5],target,NoService(),graph=graph,seed=4,strict=strict)


def test_return_path_cannot_reuse_outward_edge_in_either_direction():
    graph=ParkGraph(fixture())
    _, previous=graph.paths(2,blocked_edges={(1,2)})
    assert graph.path(previous,2,1)==[2,3,4,1]
    _, previous=graph.paths(1,blocked_edges={(1,2)})
    assert graph.path(previous,1,2)==[1,4,3,2]


def test_duplicate_osm_ids_cannot_hide_same_physical_path():
    # Distinct IDs describe the same middle 2 km of a line in reverse.
    points=[(.02,.02),(.02,.05),(.02,.04),(.02,.03),(.02,.02)]
    graph=ParkGraph([fixture()[0],way([1,2,3,4,1],points,{'highway':'footway','oneway:foot':'yes'})],park_only=False)
    target=sum(graph.edges[a][b] for a,b in [(1,2),(2,3),(3,4),(4,1)])
    with pytest.raises(RoutingError,match='without repeated paths'):
        generate_park_route(graph.points[1],target,NoService(),graph=graph,seed=4)
