"""Independent route model, GPX interchange, and pedestrian service client."""
from dataclasses import dataclass, field
from pathlib import Path
import json
import copy
import math
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

Point = tuple[float, float]


class RoutingError(ValueError):
    pass


def validate_coords(lat, lon):
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Invalid latitude or longitude.')


def haversine_m(a, b, c, d):
    x, y = math.radians(a), math.radians(c)
    h = math.sin((y-x)/2)**2 + math.cos(x)*math.cos(y)*math.sin(math.radians(d-b)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def polyline_length_m(points):
    return sum(haversine_m(*a, *b) for a, b in zip(points, points[1:]))


@dataclass
class Route:
    name: str = 'Untitled run'
    segments: list[list[Point]] = field(default_factory=list)
    elevations: list[list[float | None]] = field(default_factory=list)
    description: str = ''

    @property
    def distance_m(self):
        return sum(polyline_length_m(s) for s in self.segments)

    @property
    def geometry(self):
        return [p for segment in self.segments for p in segment]


def route_from_geometry(points, name):
    return Route(name, [list(points)])


def read_gpx(path):
    root = ET.parse(path).getroot()
    local = lambda el: el.tag.split('}')[-1]
    if local(root) != 'gpx': raise ValueError('This file is not GPX.')
    result = Route(Path(path).stem)
    for el in root.iter():
        if local(el) in ('trk', 'rte'):
            name = next((x.text for x in el if local(x) == 'name'), None)
            if name: result.name = name
            description = next((x.text for x in el if local(x) == 'desc'), None)
            if description: result.description = description
        if local(el) not in ('trkseg', 'rte'): continue
        points, heights = [], []
        for pt in el:
            if local(pt) not in ('trkpt', 'rtept'): continue
            p = float(pt.attrib['lat']), float(pt.attrib['lon'])
            validate_coords(*p)
            points.append(p)
            ele = next((x.text for x in pt if local(x) == 'ele'), None)
            value = float(ele) if ele else None
            heights.append(value if value is None or math.isfinite(value) else None)
        if points:
            result.segments.append(points)
            result.elevations.append(heights)
    if not result.segments: raise ValueError('The GPX contains no track or route points.')
    return result


def write_gpx(route, path):
    if not any(len(s) > 1 for s in route.segments): raise ValueError('Add at least two route points before exporting.')
    ns = 'http://www.topografix.com/GPX/1/1'
    ET.register_namespace('', ns)
    tag = lambda name: f'{{{ns}}}{name}'
    root = ET.Element(tag('gpx'), version='1.1', creator='ParkLoop')
    trk = ET.SubElement(root, tag('trk'))
    ET.SubElement(trk, tag('name')).text = route.name
    ET.SubElement(trk, tag('desc')).text = route.description
    ET.SubElement(trk, tag('type')).text = 'running'
    for i, segment in enumerate(route.segments):
        seg = ET.SubElement(trk, tag('trkseg'))
        for j, (lat, lon) in enumerate(segment):
            validate_coords(lat, lon)
            pt = ET.SubElement(seg, tag('trkpt'), lat=f'{lat:.7f}', lon=f'{lon:.7f}')
            if i < len(route.elevations) and j < len(route.elevations[i]) and route.elevations[i][j] is not None:
                ET.SubElement(pt, tag('ele')).text = str(route.elevations[i][j])
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def fetch_json(url, data=None, headers=None, timeout=30, retries=1, diagnostic=None):
    request = urllib.request.Request(url, data=data, headers={'User-Agent': 'ParkLoop/0.1 desktop GPX editor', **(headers or {})})
    error = None
    for attempt in range(retries):
        if attempt: time.sleep(1)
        try:
            if diagnostic:
                diagnostic(f'HTTP attempt {attempt + 1}/{retries}: {request.get_method()} {url} (timeout={timeout}s)')
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result=json.load(response)
                if diagnostic:
                    diagnostic(f'HTTP response: status={getattr(response, "status", "unknown")}')
                return result
        except Exception as exc:
            error = exc
            if diagnostic:
                diagnostic(f'HTTP attempt {attempt + 1} failed: {type(exc).__name__}: {exc}')
    raise RoutingError(f'Map service unavailable: {error}')


class FootRouter:
    """Pedestrian routing with an explicit option for missing sidewalk evidence."""
    def __init__(self, *, include_unverified=False):
        self._cached = None
        self._cached_source = None
        self.include_unverified = include_unverified

    def _graph(self, points, center, radius):
        from .parks import fetch_graph
        from . import mapdata
        offline = os.environ.get('PARKLOOP_OFFLINE_MAP') or mapdata.offline_path
        source = (str(offline), Path(offline).stat().st_mtime_ns) if offline else None
        if self._cached:
            origin, coverage, created, graph = self._cached
            if (self._cached_source == source and graph.include_unverified == self.include_unverified
                    and (offline or (time.monotonic() - created < 900 and all(
                        haversine_m(*origin, *p) + 500 < coverage for p in points)))):
                return graph
        target = max(4000, 2 * radius)
        graph = fetch_graph(center, target, park_only=False,
                            include_unverified=self.include_unverified, compute_parks=False, include_major=True)
        self._cached = (center, min(16000, target / 2 + 1000), time.monotonic(), graph)
        self._cached_source = source
        return graph

    @staticmethod
    def _snap(graph, point):
        # Split the closest walkable edge, preserving its allowed directions.
        scale = max(1e-9, math.cos(math.radians(point[0])))
        best = None
        seen = set()
        for a, neighbours in graph.edges.items():
            for b in neighbours:
                key = tuple(sorted((a, b)))
                if key in seen: continue
                seen.add(key)
                p, q = graph.points[a], graph.points[b]
                dx, dy = (q[1]-p[1])*scale, q[0]-p[0]
                t = max(0, min(1, ((point[1]-p[1])*scale*dx + (point[0]-p[0])*dy) / (dx*dx+dy*dy)))
                projected = (p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1]))
                distance = haversine_m(*point, *projected)
                if best is None or distance < best[0]: best = distance, a, b, t, projected
        if best is None or best[0] > 80:
            raise RoutingError('Point is too far from a verified path or sidewalk. Choose a nearby path.')
        _, a, b, t, projected = best
        if t < 1e-9: return a
        if t > 1-1e-9: return b
        node = min(0, min(graph.points)) - 1
        graph.points[node] = projected
        graph.edges[node] = {}
        evidence = graph.edge_evidence.get(tuple(sorted((a,b))))
        if evidence:
            graph.edge_evidence[tuple(sorted((a,node)))] = evidence
            graph.edge_evidence[tuple(sorted((b,node)))] = evidence
        for source, dest, fraction in ((a,b,t), (b,a,1-t)):
            length = graph.edges.get(source, {}).pop(dest, None)
            if length is not None:
                graph.edges[source][node] = length*fraction
                graph.edges[node][dest] = length*(1-fraction)
        return node

    def calculate_segment(self, start, end):
        return self.calculate_route([start, end])

    def calculate_route(self, points):
        from .safety import audit_route
        if len(points) < 2: raise RoutingError('Choose at least two points.')
        for point in points: validate_coords(*point)
        center = (sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points))
        radius = max(haversine_m(*center, *point) for point in points)
        if radius > 15000: raise RoutingError('Choose closer points for walking-path routing.')
        base = self._graph(points, center, radius)
        graph = copy.copy(base)
        graph.points = dict(base.points)
        graph.edges = {n: dict(edges) for n, edges in base.edges.items()}
        graph.edge_evidence = dict(base.edge_evidence)
        if not graph.points: raise RoutingError('No map-verified pedestrian paths or sidewalks nearby.')
        anchors = []
        for point in points:
            anchors.append(self._snap(graph, point))
        nodes = [anchors[0]]
        for a, b in zip(anchors, anchors[1:]):
            # Manual legs minimize actual distance on the allowed network.
            _, previous = graph.paths(a, destination=b)
            leg = graph.path(previous, a, b)
            if not leg:
                detail = ('mapped pedestrian paths and eligible local roads' if self.include_unverified
                          else 'verified pedestrian paths or sidewalks; try Shortest walking route')
                raise RoutingError(f'No connection using {detail}. The map may have gaps or access restrictions.')
            nodes.extend(leg[1:])
        route = route_from_geometry([graph.points[n] for n in nodes], 'Manual run')
        used_way_ids = {graph.edge_evidence[tuple(sorted((a,b)))]['way_id']
                        for a,b in zip(nodes,nodes[1:])}
        evidence = [el for el in graph.source_elements
                    if el.get('type') == 'way' and el.get('id') in used_way_ids]
        audit = audit_route(route, evidence, include_unverified=self.include_unverified, include_major=True)
        if audit.rejected_m or (not self.include_unverified and not audit.passed):
            raise RoutingError('Road safety check failed. No route returned.')
        route.description = (f'Map-checked {audit.checked_m:.0f} m; unverified {audit.unknown_m:.0f} m. '
                             'Unverified roads lack sidewalk evidence; check conditions before running.'
                             if audit.unknown_m else 'Map-checked pedestrian paths / sidewalks. Local conditions still need checking.')
        return route


def generate_any(start, target, provider, cancel, progress, seed=None, *, graph=None):
    # Share the same sidewalk and no-repeat constraints as park modes. Never
    # bypass the checks through an external router with unknown road tags.
    from .parks import generate_park_route
    return generate_park_route(start, target, provider, cancel=cancel,
        progress=progress, seed=seed, graph=graph, prefer_parks=False).route
