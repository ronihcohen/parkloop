import threading
import pytest
from parkloop.core import Route,RoutingError,generate_any,polyline_length_m
from parkloop.quality import has_repeated_path,repeated_path_m,shared_path_fraction


@pytest.mark.parametrize('points',[
    [(0,0),(0,.01),(0,0)],  # reverse
    [(0,0),(0,.01),(.01,.01),(0,0),(0,.01)],  # same direction
    [(0,0),(0,.02),(0,.015),(0,.005)],  # partial, differently sampled
])
def test_geometry_detects_repeated_sections(points):
    assert has_repeated_path(points)
    assert repeated_path_m(points)>500


@pytest.mark.parametrize('points',[
    [(0,0),(0,.01),(.01,.01),(.01,0),(0,0)], # closure is not retracing
    [(0,0),(.01,.01),(.01,0),(0,.01),(0,0)], # crossing at one point is fine
    [(0,0),(0,.01),(.00001,.01),(.00001,0),(0,0)], # distinct parallel paths
])
def test_geometry_accepts_non_repeated_loops(points):
    assert not has_repeated_path(points)


def test_alternative_overlap_handles_reverse_and_different_sampling():
    route=[(0,0),(0,.01),(0,.02),(.01,.02)]
    mostly_same=[(0,.02),(0,.015),(0,.005),(0,0),(-.01,0)]
    distinct=[(0,0),(.01,0),(.01,.01),(.01,.02)]
    assert shared_path_fraction(route,mostly_same)==pytest.approx(2/3,rel=.01)
    assert shared_path_fraction(route,distinct)==pytest.approx(0,abs=1e-6)


def road_graph(points):
    from parkloop.parks import ParkGraph
    return ParkGraph([{'type':'way','id':1,'nodes':list(range(len(points)-1))+[0],
        'geometry':[{'lat':a,'lon':b} for a,b in points],
        'tags':{'highway':'footway'}}],park_only=False)


def test_any_walkable_rejects_exact_distance_out_and_back():
    points=[(0,0),(0,.04),(0,0)]
    graph=road_graph(points)
    with pytest.raises(RoutingError,match='without repeated paths'):
        generate_any((0,0),polyline_length_m(points),None,threading.Event(),lambda _:None,seed=4,graph=graph)


def test_any_walkable_uses_verified_graph_without_park_requirement():
    points=[(0,0),(0,.02),(.02,.02),(.02,0),(0,0)]
    graph=road_graph(points)
    result=generate_any((0,0),polyline_length_m(points),None,threading.Event(),lambda _:None,seed=4,graph=graph)
    assert result.geometry[0]==result.geometry[-1]
    assert not has_repeated_path(result.geometry)
    assert 'Map-checked' in result.description


def test_single_access_connections_removed_without_removing_loops():
    from parkloop.quality import bridge_edges
    edges={1:{2:1,3:1,4:1},2:{1:1,3:1},3:{1:1,2:1},4:{1:1,5:1},5:{4:1}}
    assert bridge_edges(edges)=={(1,4),(4,5)}


def test_bridge_detection_does_not_depend_on_directed_access():
    from parkloop.quality import bridge_edges
    assert bridge_edges({1:{2:1},2:{3:1},3:{1:1}})==set()
