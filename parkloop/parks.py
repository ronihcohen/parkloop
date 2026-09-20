"""Park-constrained loops on OSM pedestrian edges, with measured park coverage."""
from __future__ import annotations
import heapq
import math
import os
import threading
import urllib.parse
from dataclasses import dataclass
from shapely.geometry import Polygon, LineString, Point
from shapely import prepare
from shapely.ops import polygonize, unary_union
from .quality import has_repeated_path, bridge_edges
from .safety import assess_way, assess_candidate_way, audit_route
from .core import (haversine_m, polyline_length_m, validate_coords, fetch_json,
                   Route, RoutingError, route_from_geometry)


@dataclass
class ParkResult:
    route: Route
    park_fraction: float
    start_offset_m: float
    repeated_fraction: float


def park_geometry(elements):
    """Join multipolygon outer ways and subtract inner rings (ponds, exclusions)."""
    areas = []
    for el in elements:
        if el.get('tags', {}).get('leisure') not in ('park', 'garden', 'nature_reserve'):
            continue
        if el['type'] == 'way':
            pts = [(p['lon'], p['lat']) for p in el.get('geometry', [])]
            if len(pts) >= 4 and pts[0] == pts[-1]:
                areas.append(Polygon(pts).buffer(0))
        elif el['type'] == 'relation':
            rings = {'outer': [], 'inner': []}
            for m in el.get('members', []):
                pts = [(p['lon'], p['lat']) for p in m.get('geometry', [])]
                role = m.get('role') or 'outer'
                if role in rings and len(pts) > 1:
                    rings[role].append(LineString(pts))
            outer = unary_union(list(polygonize(rings['outer'])))
            inner = unary_union(list(polygonize(rings['inner'])))
            areas.append(outer.difference(inner))
    return unary_union(areas)


class ParkGraph:
    def __init__(self, elements, *, park_only=True, include_unverified=False, compute_parks=True, include_major=False):
        self.include_unverified = include_unverified
        self.area = park_geometry(elements) if compute_parks or park_only else Polygon()
        prepare(self.area)
        self.points = {}
        self.edges = {}
        self.park_edges = {}
        self.park_only = park_only
        self.source_elements = elements
        self.edge_evidence = {}
        self.safety_policy = 1
        for el in elements:
            tags = el.get('tags', {})
            if el.get('type') != 'way':
                continue
            decision = assess_candidate_way(tags,include_major=include_major) if include_unverified else assess_way(tags)
            if not decision.allowed:
                continue
            ids, geom = el.get('nodes', []), el.get('geometry', [])
            if len(ids) != len(geom):
                continue
            for a, b, p, q in zip(ids, ids[1:], geom, geom[1:]):
                x, y = (p['lat'], p['lon']), (q['lat'], q['lon'])
                # Full edge containment, not just endpoints. No bridge across excluded water.
                line = LineString([(x[1], x[0]), (y[1], y[0])])
                if self.area.covers(line):
                    fraction = 1.0
                elif self.area.disjoint(line):
                    fraction = 0.0
                else:
                    fraction = min(1.0, line.intersection(self.area).length / line.length) if line.length else 0.0
                if park_only and not self.area.covers(line):
                    continue
                self.park_edges[tuple(sorted((a, b)))] = fraction
                d = haversine_m(*x, *y)
                if d <= 0:
                    continue
                self.points[a], self.points[b] = x, y
                self.edge_evidence[tuple(sorted((a, b)))] = {
                    "way_id": el.get("id"), "tags": dict(tags), "category": decision.category}
                direction = tags.get('oneway:foot', 'no')
                if direction != '-1': self.edges.setdefault(a, {})[b] = d
                if direction not in ('yes', '1', 'true'): self.edges.setdefault(b, {})[a] = d

    def paths(self, source, penalty=None, park_penalty=0.0, blocked_edges=None, destination=None):
        distances, previous = {source: 0.0}, {}
        queue = [(0.0, source)]
        while queue:
            d, a = heapq.heappop(queue)
            if d != distances[a]: continue
            if a == destination: break
            for b, length in self.edges.get(a, {}).items():
                edge = tuple(sorted((a, b)))
                if blocked_edges and edge in blocked_edges:
                    continue
                outside = 1 - getattr(self, 'park_edges', {}).get(edge, 1.0)
                cost = d + length * (1 + outside * park_penalty + (penalty or {}).get(edge, 0))
                if cost < distances.get(b, math.inf):
                    distances[b], previous[b] = cost, a
                    heapq.heappush(queue, (cost, b))
        return distances, previous

    @staticmethod
    def path(previous, start, end):
        result = [end]
        while result[-1] != start:
            if result[-1] not in previous: return []
            result.append(previous[result[-1]])
        return result[::-1]


def fetch_elements(start, target_m):
    from .mapdata import offline_elements, cached_elements, save_elements
    local = offline_elements()
    if local is not None:
        return local
    # A closed route cannot reach farther than half its length.
    radius = min(16000, target_m / 2 + 1000)
    provider = os.environ.get('PARKLOOP_OVERPASS_URL', 'https://overpass-api.de/api/interpreter')
    cached = cached_elements(start, radius, provider)
    if cached is not None:
        return cached
    around = f'(around:{radius:.0f},{start[0]:.6f},{start[1]:.6f})'
    query = f'''[out:json][timeout:40];(
      way[leisure~"^(park|garden|nature_reserve)$"]{around};
      relation[leisure~"^(park|garden|nature_reserve)$"]{around};
      way[highway]{around};
    );out geom;'''
    data = fetch_json(provider,
        data=urllib.parse.urlencode({'data': query}).encode(),
        headers={'User-Agent': 'ParkLoop/0.1 (desktop route planner)'}, timeout=55, retries=2)
    if data.get('remark'):
        raise RoutingError(f'Map download incomplete: {data["remark"]}')
    elements = data.get('elements', [])
    if elements: save_elements(start, radius, provider, elements)
    return elements


def fetch_graph(start, target_m, *, park_only=True, include_unverified=False, compute_parks=True, include_major=False):
    return ParkGraph(fetch_elements(start, target_m), park_only=park_only,
                     include_unverified=include_unverified, compute_parks=compute_parks, include_major=include_major)


def generate_park_route(start, target_m, provider, *, graph=None, seed=None,
                        cancel=None, progress=None, strict=False, prefer_parks=True, excluded_signatures=None):
    validate_coords(*start)
    if not math.isfinite(target_m) or not 1000 <= target_m <= 30000:
        raise ValueError('Choose a distance between 1 and 30 km.')
    cancel = cancel or threading.Event()
    if progress: progress('Loading park boundaries and pedestrian paths…')
    graph = graph if graph is not None else fetch_graph(start, target_m, park_only=strict)
    if strict and not getattr(graph, 'park_only', True):
        import copy
        graph = copy.copy(graph)
        graph.edges = {a: {b: length for b, length in neighbors.items()
                          if graph.area.covers(LineString([(graph.points[a][1], graph.points[a][0]),
                                                          (graph.points[b][1], graph.points[b][0])]))}
                       for a, neighbors in graph.edges.items()}
        nodes = {n for a, neighbors in graph.edges.items() for n in (a, *neighbors) if neighbors}
        graph.points = {n: graph.points[n] for n in nodes}
        graph.park_only = True
    if cancel.is_set():
        raise RoutingError('Cancelled.')
    if strict and not graph.area.covers(Point(start[1], start[0])):
        raise RoutingError('The start is outside mapped park boundaries. Move it into the park, or choose Park loop to include access paths.')
    return generate_preferred_loop(start, target_m, provider, graph, seed, cancel, progress, prefer_parks=prefer_parks, excluded_signatures=excluded_signatures)


def loop_metrics(graph, nodes):
    """Distance-weighted quality: count every traversal of an already used edge."""
    distance = park_m = repeated = 0.0
    seen = set()
    for a, b in zip(nodes, nodes[1:]):
        length = graph.edges[a][b]
        edge = tuple(sorted((a, b)))
        distance += length
        park_m += length * graph.park_edges.get(edge, 0.0)
        if edge in seen:
            repeated += length
        seen.add(edge)
    return distance, park_m / max(distance, 1), repeated / max(distance, 1)


def loop_quality(distance, target, park_fraction, repeated_fraction):
    """Repetition is invalid, regardless of distance accuracy or park coverage."""
    error = abs(distance - target) / target
    return (repeated_fraction > 0, error > .05, park_fraction < .5,
            (1 - park_fraction) * .8 + error * .15)


def generate_preferred_loop(start, target, provider, graph, seed, cancel, progress, *, prefer_parks=True, excluded_signatures=None):
    """Search the complete pedestrian graph, allowing short park connections.

    Unlike strict mode, mapped bridges and paths outside park polygons remain
    available. This makes two-bank river loops possible and avoids returning
    along the same path just to maintain 100% polygon containment.
    """
    if getattr(graph, 'safety_policy', None) != 1:
        raise RoutingError('Road safety metadata is missing. Reload current map data before generating a route.')
    # A bridge in the graph (not necessarily a physical bridge) is a single
    # access connection that every closed route must retrace. Remove these
    # before choosing a snapped start; no-repeat loops can never use them.
    import copy
    graph = copy.copy(graph)
    blocked = bridge_edges(graph.edges)
    graph.edges = {a:{b:d for b,d in outgoing.items() if tuple(sorted((a,b))) not in blocked}
                   for a,outgoing in graph.edges.items()}
    active = {n for a,outgoing in graph.edges.items() for b in outgoing for n in (a,b)}
    graph.points = {n:graph.points[n] for n in active}
    if not graph.points:
        raise RoutingError('No loop without repeated paths exists in the verified pedestrian network. Try another start.')
    from .loop_search import find_loop
    # Try nearby roots independently: directed reachability and usable cycles
    # can differ even inside the same underlying undirected component.
    candidates = sorted((n for n in graph.points
                         if haversine_m(*start, *graph.points[n]) <= 80),
                        key=lambda n: haversine_m(*start, *graph.points[n]))
    maximum_available = 0.0
    eligible = []
    # An undirected component's total length is a safe upper bound even for
    # one-way graphs. Compute it once, not a full Dijkstra per nearby vertex.
    neighbors = {}
    for a, outgoing in graph.edges.items():
        for b in outgoing:
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)
    capacities = {}
    for node in candidates:
        if cancel.is_set(): raise RoutingError('Cancelled.')
        if node not in capacities:
            reached, stack, unique = {node}, [node], {}
            while stack:
                if cancel.is_set(): raise RoutingError('Cancelled.')
                a = stack.pop()
                for b in neighbors.get(a, ()):
                    key = tuple(sorted((a,b)))
                    unique[key] = graph.edges.get(a, {}).get(b, graph.edges.get(b, {}).get(a))
                    if b not in reached:
                        reached.add(b); stack.append(b)
            capacity = sum(unique.values())
            capacities.update(dict.fromkeys(reached, capacity))
        available = capacities[node]
        maximum_available = max(maximum_available, available)
        if available >= target * .95:
            eligible.append(node)
    if not eligible:
        raise RoutingError(
            f'The map-checked network within 80 m of this start has at most '
            f'{maximum_available / 1000:.2f} km of reachable cycle-capable paths; '
            f'at least {target * .95 / 1000:.2f} km is needed without repeated paths. '
            'Excluded roads and missing sidewalk data may disconnect the network. '
            'Choose another start or distance, or check the map data.')
    best = None
    incomplete = len(eligible) > 8
    for root in eligible[:8]:
        result = find_loop(graph, root, target, prefer_parks=prefer_parks,
                           cancel=cancel, progress=progress, seed=seed, excluded_signatures=excluded_signatures)
        incomplete |= result.stats.incomplete
        if result.nodes is None:
            continue
        quality = loop_quality(result.distance_m, target,
                               result.park_fraction if prefer_parks else 1.0, 0)
        if best is None or quality < best[0]:
            best = quality, root, result
        # Prefer the nearest start that provides a compliant route.
        break
    if best is None:
        if incomplete:
            raise RoutingError('Search limit reached without finding a loop without repeated paths '
                               'within 5% of the requested distance and park preference. '
                               'A valid loop may still exist; try generating again or another distance.')
        raise RoutingError('No loop without repeated paths meets the requested distance '
                           'and park preference in the map-checked network near this start.')
    _, root, result = best
    nodes, distance, park_fraction = result.nodes, result.distance_m, result.park_fraction
    repeated = 0.0
    offset = haversine_m(*start, *graph.points[root])
    label = 'park loop' if prefer_parks else 'walking loop'
    route = route_from_geometry([graph.points[n] for n in nodes], f'{target / 1000:g} km {label}')
    allow_unknown = getattr(graph, 'include_unverified', False)
    audit = audit_route(route, graph.source_elements, include_unverified=allow_unknown)
    if audit.rejected_m or (audit.unknown_m and not allow_unknown):
        raise RoutingError('Road safety check failed. No route returned.')
    route.description = (f'Map-checked {audit.checked_m:.0f} m; unverified {audit.unknown_m:.0f} m; loop: {park_fraction:.1%} inside mapped parks; '
                         f'no repeated mapped paths; requested {target / 1000:g} km; '
                         f'start snapped by {offset:.0f} m.')
    return ParkResult(route, park_fraction, offset, repeated)
