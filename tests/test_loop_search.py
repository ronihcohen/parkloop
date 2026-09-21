import math
import threading
import pytest
from parkloop.core import RoutingError
from parkloop.parks import ParkGraph, generate_park_route
from parkloop.loop_search import find_loop, SearchLimits, compress_graph, navigation_turn_count
from parkloop.quality import has_repeated_path


def wheel():
    points = {i: (.04 + .0143 * math.sin(i * math.tau / 12),
                  .04 + .0143 * math.cos(i * math.tau / 12)) for i in range(12)}
    points[12] = (.04, .04)
    pairs = [(i, (i+1) % 12) for i in range(12)] + [(i, 12) for i in range(12)]
    elements = [{'type': 'way', 'nodes': [a,b],
                 'geometry': [{'lat': points[n][0], 'lon': points[n][1]} for n in (a,b)],
                 'tags': {'highway': 'footway'}} for a,b in pairs]
    graph = ParkGraph(elements, park_only=False)
    target = sum(graph.edges[i][(i+1)%12] for i in range(12))
    return graph, target


def test_long_loop_with_shortcuts_that_defeat_greedy_outward_return():
    graph, target = wheel()
    _, previous = graph.paths(0)
    # Even exhaustively trying every anchor with the old greedy construction
    # misses the known perimeter. No stochastic anchor sampling is involved.
    for anchor in graph.points:
        outward = graph.path(previous, 0, anchor)
        blocked = {tuple(sorted(e)) for e in zip(outward, outward[1:])}
        _, back_previous = graph.paths(anchor, blocked_edges=blocked)
        back = graph.path(back_previous, anchor, 0)
        nodes = outward + back[1:] if back else []
        length = sum(graph.edges[a][b] for a,b in zip(nodes,nodes[1:]))
        assert not .95 * target <= length <= 1.05 * target
    result = generate_park_route(graph.points[0], target, None, graph=graph,
                                 prefer_parks=False, seed=1)
    assert result.route.geometry[0] == result.route.geometry[-1] == graph.points[0]
    assert abs(result.route.distance_m - target) / target <= .05
    assert not has_repeated_path(result.route.geometry)


def test_budget_exhaustion_is_explicit():
    graph, target = wheel()
    result = find_loop(graph, 0, target, prefer_parks=False,
                       limits=SearchLimits(max_expansions=1))
    assert result.nodes is None
    assert result.stats.budget_reached and result.stats.incomplete


def test_compressed_one_way_ring_keeps_direction_and_geometry():
    graph, _ = wheel()
    graph.edges = {i: {(i+1)%12: graph.edges[i][(i+1)%12]} for i in range(12)}
    target = sum(next(iter(e.values())) for e in graph.edges.values())
    arcs = compress_graph(graph, 0)[0]
    assert len(arcs) == 1
    result = find_loop(graph, 0, target, prefer_parks=False)
    assert result.nodes == list(range(12)) + [0]
    assert not result.stats.incomplete


def test_search_cancellation():
    graph, target = wheel()
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RoutingError, match='Cancelled'):
        find_loop(graph, 0, target, cancel=cancel)


def test_navigation_turns_count_junction_choices_not_path_curvature():
    class Graph: pass
    graph=Graph()
    graph.points={0:(0,0),1:(0,.01),2:(.01,.01),3:(-.01,.01)}
    graph.edges={0:{1:1},1:{0:1,2:1,3:1},2:{1:1},3:{1:1}}
    graph.edge_evidence={(0,1):{'way_id':10},(1,2):{'way_id':10},(1,3):{'way_id':11}}
    assert navigation_turn_count(graph,[0,1,2])==1
    # The same bend on a degree-two section of one mapped way is path shape,
    # not a navigation instruction.
    graph.edges={0:{1:1},1:{0:1,2:1},2:{1:1}}
    assert navigation_turn_count(graph,[0,1,2])==0
    graph.edge_evidence[(1,2)]={'way_id':12}
    assert navigation_turn_count(graph,[0,1,2])==1
