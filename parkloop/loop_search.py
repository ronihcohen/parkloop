"""Bounded search over complete edge-simple loops, not two greedy shortest paths.

Degree-two chains are compressed only for search. Directed access, physical
edge identity, park distance and original geometry are retained. Beam pruning is
explicitly reported; exhausting a budget is never a proof of impossibility.
"""
from dataclasses import dataclass
import heapq
import math
import random
from .core import RoutingError
from .quality import has_repeated_path


@dataclass(frozen=True)
class Arc:
    edge_id: int
    end: int
    distance: float
    park_m: float
    nodes: tuple


@dataclass
class SearchStats:
    expanded: int = 0
    closed: int = 0
    within_distance: int = 0
    geometry_rejected: int = 0
    beam_pruned: bool = False
    budget_reached: bool = False
    depth_reached: bool = False

    @property
    def incomplete(self):
        return self.beam_pruned or self.budget_reached or self.depth_reached


@dataclass(frozen=True)
class SearchLimits:
    beam_width: int = 192
    max_expansions: int = 100000
    max_depth: int = 256


@dataclass
class LoopSearchResult:
    nodes: list | None
    distance_m: float
    park_fraction: float
    stats: SearchStats


def compress_graph(graph, root):
    """Keep junctions and root, replace degree-two chains by oriented arcs."""
    neighbors = {}
    for a, outgoing in graph.edges.items():
        for b in outgoing:
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)
    junctions = {n for n, linked in neighbors.items() if len(linked) != 2} | {root}
    adjacency = {}
    seen = set()
    edge_id = 0
    for start in sorted(junctions):
        for first in sorted(neighbors.get(start, ())):
            key = tuple(sorted((start, first)))
            if key in seen: continue
            path = [start, first]
            seen.add(key)
            previous, node = start, first
            while node not in junctions:
                following = next(n for n in neighbors[node] if n != previous)
                key = tuple(sorted((node, following)))
                if key in seen: break
                seen.add(key)
                path.append(following)
                previous, node = node, following
            distance = park_m = 0.0
            forward = backward = True
            for a, b in zip(path, path[1:]):
                length = graph.edges.get(a, {}).get(b, graph.edges.get(b, {}).get(a))
                distance += length
                park_m += length * graph.park_edges.get(tuple(sorted((a, b))), 0.0)
                forward &= b in graph.edges.get(a, {})
                backward &= a in graph.edges.get(b, {})
            if forward:
                adjacency.setdefault(start, []).append(Arc(edge_id, node, distance, park_m, tuple(path)))
            if backward:
                adjacency.setdefault(node, []).append(Arc(edge_id, start, distance, park_m, tuple(reversed(path))))
            edge_id += 1
    return adjacency


def return_distances(adjacency, root):
    """Admissible lower bound: shortest directed return with all edges free."""
    reverse = {}
    for a, arcs in adjacency.items():
        for arc in arcs:
            reverse.setdefault(arc.end, []).append((a, arc.distance))
    distances = {root: 0.0}
    queue = [(0.0, root)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]: continue
        for previous, length in reverse.get(node, ()):
            candidate = distance + length
            if candidate < distances.get(previous, math.inf):
                distances[previous] = candidate
                heapq.heappush(queue, (candidate, previous))
    return distances


def shortest_pair_candidates(graph, root, target_m, prefer_parks, rng, cancel):
    """Cheap incumbents complement complete-loop exploration on large maps."""
    physical, _ = graph.paths(root)
    anchors = [n for n, d in physical.items() if n != root and d < target_m * .55]
    for penalty in ((.5, 2.0, 5.0) if prefer_parks else (0.0,)):
        _, previous = graph.paths(root, park_penalty=penalty)
        for _ in range(min(40, len(anchors))):
            if cancel is not None and cancel.is_set(): raise RoutingError('Cancelled.')
            desired = target_m * rng.uniform(.25, .5)
            anchor = min(rng.sample(anchors, min(45, len(anchors))),
                         key=lambda n: abs(physical[n] - desired))
            outward = graph.path(previous, root, anchor)
            blocked = {tuple(sorted(e)) for e in zip(outward, outward[1:])}
            _, back_previous = graph.paths(anchor, park_penalty=penalty, blocked_edges=blocked, destination=root)
            back = graph.path(back_previous, anchor, root)
            if not back: continue
            nodes = outward + back[1:]
            distance = green = 0.0
            for a, b in zip(nodes, nodes[1:]):
                length = graph.edges[a][b]
                distance += length
                green += length * graph.park_edges.get(tuple(sorted((a,b))), 0)
            if target_m * .95 <= distance <= target_m * 1.05:
                fraction = green / distance
                if (not prefer_parks or fraction >= .5) and not has_repeated_path([graph.points[n] for n in nodes]):
                    yield nodes, distance, fraction


def find_loop(graph, root, target_m, *, prefer_parks=True, cancel=None,
              progress=None, seed=None, limits=None, excluded_signatures=None):
    limits = limits or SearchLimits()
    if limits.beam_width < 1 or limits.max_expansions < 1 or limits.max_depth < 1:
        raise ValueError('Search budgets must be positive.')
    adjacency = compress_graph(graph, root)
    lower = return_distances(adjacency, root)
    stats = SearchStats()
    best = None
    excluded_signatures = excluded_signatures or set()
    def already_offered(nodes):
        return frozenset(tuple(sorted((graph.points[a],graph.points[b])))
                         for a,b in zip(nodes,nodes[1:])) in excluded_signatures
    minimum, maximum = target_m * .95, target_m * 1.05
    rng = random.Random(seed)
    if progress: progress('Finding initial loop candidates…')
    # This is an incumbent only, never a reason to conclude no loop exists.
    for nodes, distance, fraction in shortest_pair_candidates(
            graph, root, target_m, prefer_parks, rng, cancel):
        if already_offered(nodes): continue
        score = ((1-fraction)*.8 if prefer_parks else 0) + abs(distance-target_m)/target_m*.15
        if best is None or score < best[0]:
            best = score, nodes, distance, fraction
    # node, used undirected chain bitset, length, park distance, oriented arcs
    frontier = [(root, 0, 0.0, 0.0, ())]
    for depth in range(limits.max_depth):
        if cancel is not None and cancel.is_set(): raise RoutingError('Cancelled.')
        if progress and depth % 8 == 0:
            progress(f'Exploring complete loops: {stats.expanded:,} branches checked…')
        following = []
        equivalent = set()
        for node, used, length, green, path in frontier:
            if cancel is not None and cancel.is_set(): raise RoutingError('Cancelled.')
            for arc in adjacency.get(node, ()):
                if stats.expanded >= limits.max_expansions:
                    stats.budget_reached = True
                    break
                stats.expanded += 1
                bit = 1 << arc.edge_id
                if used & bit: continue
                new_length = length + arc.distance
                if new_length + lower.get(arc.end, math.inf) > maximum + 1e-6: continue
                new_used = used | bit
                key = (arc.end, new_used)
                if key in equivalent: continue
                equivalent.add(key)
                new_green = green + arc.park_m
                new_path = path + (arc,)
                if arc.end == root:
                    stats.closed += 1
                    if minimum <= new_length <= maximum:
                        stats.within_distance += 1
                        fraction = new_green / new_length
                        if not prefer_parks or fraction >= .5:
                            score = ((1 - fraction) * .8 if prefer_parks else 0) + abs(new_length-target_m)/target_m * .15
                            if best is None or score < best[0]:
                                nodes = [root]
                                for step in new_path: nodes.extend(step.nodes[1:])
                                if already_offered(nodes):
                                    continue
                                if has_repeated_path([graph.points[n] for n in nodes]):
                                    stats.geometry_rejected += 1
                                else:
                                    best = score, nodes, new_length, fraction
                # Passing through root is allowed, but no chain may be reused.
                # This also supports edge-simple figure-eight loops.
                following.append((arc.end, new_used, new_length, new_green, new_path))
            if stats.budget_reached: break
        if stats.budget_reached: break
        if not following:
            frontier = []
            break
        def priority(state):
            node, _, length, green, _ = state
            gap = max(0, target_m - length - lower.get(node, 0)) / target_m
            outside = (length-green)/target_m if prefer_parks else 0
            return gap + outside * 1.6
        # Preserve alternatives at different junctions, distances, and first
        # branches; a cheaper prefix cannot dominate one with different edges.
        rng.shuffle(following)
        following.sort(key=priority)
        if len(following) > limits.beam_width:
            stats.beam_pruned = True
            buckets, selected, deferred = set(), [], []
            for state in following:
                node, _, length, _, path = state
                bucket = (node, int(length/target_m*24), path[0].edge_id)
                if bucket in buckets:
                    deferred.append(state)
                else:
                    buckets.add(bucket)
                    selected.append(state)
            frontier = (selected + deferred)[:limits.beam_width]
        else:
            frontier = following
    else:
        stats.depth_reached = bool(frontier)
    if best is None:
        return LoopSearchResult(None, 0.0, 0.0, stats)
    return LoopSearchResult(best[1], best[2], best[3], stats)
