"""Conservative map-tag checks, not a guarantee of on-the-ground safety."""
from dataclasses import dataclass, field
from collections import defaultdict
import math
from .core import haversine_m

PEDESTRIAN = {'footway', 'pedestrian', 'path', 'steps'}
ROADS = {'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary',
         'tertiary_link', 'residential', 'unclassified', 'service', 'living_street'}
ALLOWED_FOOT = {'yes', 'designated', 'permissive', 'official'}


@dataclass(frozen=True)
class WaySafety:
    allowed: bool
    category: str
    reason: str


def assess_way(tags):
    """Missing sidewalk data never counts as evidence that a road has one."""
    deny = lambda reason: WaySafety(False, 'excluded', reason)
    highway = tags.get('highway', '')
    foot = tags.get('foot', '')
    if highway in {'motorway', 'motorway_link', 'trunk', 'trunk_link'} or tags.get('motorroad') in {'yes', '1', 'true'}:
        return deny('Motorway, trunk, or motorroad')
    if highway in {'construction', 'proposed', 'raceway'} or tags.get('construction') not in (None, '', 'no'):
        return deny('Construction or non-operational road')
    if foot and foot not in ALLOWED_FOOT:
        return deny('Pedestrian access restricted or separate sidepath required')
    if tags.get('access', '') not in ('', 'yes', 'designated', 'permissive', 'official') and foot not in ALLOWED_FOOT:
        return deny('Public pedestrian access not established')
    for key, value in tags.items():
        if value and key.endswith(':conditional') and (key.startswith(('foot', 'access', 'sidewalk'))):
            return deny('Conditional pedestrian access cannot be verified')
    if any(tags.get(k, '') not in ('', *ALLOWED_FOOT) for k in ('foot:forward', 'foot:backward')):
        return deny('Directional pedestrian restriction')
    if tags.get('crossing') in ('no', 'unmarked', 'informal'):
        return deny('No marked pedestrian crossing confirmed')
    if highway in PEDESTRIAN:
        if tags.get('motor_vehicle') in {'yes', 'designated'} or tags.get('motorcar') in {'yes', 'designated'}:
            return deny('Pedestrian path explicitly shared with general motor traffic')
        return WaySafety(True, 'pedestrian_path', 'Mapped pedestrian path')
    if highway == 'cycleway':
        if foot in ALLOWED_FOOT:
            return WaySafety(True, 'shared_path', 'Mapped cycleway with explicit pedestrian access')
        return deny('Cycleway without confirmed pedestrian access')
    if highway not in ROADS:
        return deny('Not a verified pedestrian path or sidewalk road')
    base = tags.get('sidewalk', '')
    defaults = {'both': ('yes', 'yes'), 'yes': ('yes', 'yes'),
                'left': ('yes', 'no'), 'right': ('no', 'yes')}.get(base, (base, base))
    both = tags.get('sidewalk:both')
    sides = [tags.get('sidewalk:left', both if both is not None else defaults[0]),
             tags.get('sidewalk:right', both if both is not None else defaults[1])]
    if 'separate' in sides:
        return deny('Use the separately mapped sidewalk, not the carriageway')
    for side, value in zip(('left', 'right'), sides):
        if value != 'yes': continue
        access = tags.get(f'sidewalk:{side}:access', tags.get('sidewalk:both:access', tags.get('sidewalk:access', '')))
        foot_access = tags.get(f'sidewalk:{side}:foot', tags.get('sidewalk:both:foot', tags.get('sidewalk:foot', '')))
        if access not in ('', *ALLOWED_FOOT) or foot_access not in ('', *ALLOWED_FOOT): continue
        return WaySafety(True, 'sidewalk_road', 'Road with explicitly tagged usable sidewalk')
    return deny('Sidewalk absent, unknown, or inaccessible')


def assess_candidate_way(tags, *, include_major=False):
    """Allow missing sidewalk evidence on local roads, never explicit hazards."""
    decision = assess_way(tags)
    if decision.allowed:
        return decision
    # Only this specific missing-evidence case is eligible for alternatives.
    # Major roads, separate sidewalks and explicit access restrictions stay out.
    if (decision.reason == 'Sidewalk absent, unknown, or inaccessible'
            and tags.get('highway') in ({'residential', 'unclassified', 'service', 'living_street', 'tertiary'}
                                       | ({'primary','primary_link','secondary','secondary_link','tertiary_link'} if include_major else set()))
            and not any(k.startswith('sidewalk') for k in tags)):
        return WaySafety(True, 'unverified', 'Road: sidewalk evidence missing from map')
    return decision


def coordinate_edge(a, b):
    return tuple(sorted((tuple(round(v, 7) for v in a), tuple(round(v, 7) for v in b))))


@dataclass
class SafetyAudit:
    checked_m: float = 0.0
    rejected_m: float = 0.0
    unknown_m: float = 0.0
    categories_m: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)

    @property
    def passed(self):
        return self.checked_m > 0 and self.rejected_m == 0 and self.unknown_m == 0


def audit_route(route, elements, *, include_unverified=False, include_major=False):
    """Match GPX edges to OSM, allowing only 0.2 m of coordinate rounding.

    An unmatched GPX edge is unverified, even when a footpath is nearby. This
    avoids approving an unsafe carriageway merely because a sidewalk parallels it.
    """
    evidence = defaultdict(list)
    for way in elements:
        if way.get('type') != 'way' or 'highway' not in way.get('tags', {}): continue
        geometry = [(p['lat'], p['lon']) for p in way.get('geometry', [])]
        decision = assess_candidate_way(way['tags'],include_major=include_major) if include_unverified else assess_way(way['tags'])
        for a, b in zip(geometry, geometry[1:]):
            evidence[coordinate_edge(a, b)].append((decision, way.get('id'), way['tags']))
    # GPX exporters often round OSM coordinates from seven decimals to six.
    # Allow that precision loss, never a broad nearest-road/sidewalk match.
    tolerance = 0.2
    origin = route.geometry[0] if route.geometry else (0,0)
    xscale = 111320 * math.cos(math.radians(origin[0]))
    def xy(p): return ((p[1]-origin[1])*xscale, (p[0]-origin[0])*111320)
    # Snapped endpoints can lie inside a mapped edge. Both ends must lie on
    # the same source edge within the existing rounding tolerance.
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    source_edges = list(evidence)
    lines = [LineString([xy(a), xy(b)]) for a, b in source_edges]
    tree = STRtree(lines)
    def cell(p):
        x,y=xy(p)
        return math.floor(x/tolerance),math.floor(y/tolerance)
    vertices = {p for edge in evidence for p in edge}
    buckets = defaultdict(list)
    for point in vertices: buckets[cell(point)].append(point)
    def close_vertices(point):
        cx,cy=cell(point);x,y=xy(point)
        return [p for dx in (-1,0,1) for dy in (-1,0,1)
                for p in buckets.get((cx+dx,cy+dy),())
                if math.hypot(x-xy(p)[0],y-xy(p)[1])<=tolerance]
    report = SafetyAudit()
    for segment in route.segments:
        for a, b in zip(segment, segment[1:]):
            length = haversine_m(*a, *b)
            if length == 0: continue
            matches = evidence.get(coordinate_edge(a, b), [])
            if not matches:
                matches = [match for x in close_vertices(a) for y in close_vertices(b)
                           for match in evidence.get(coordinate_edge(x,y),())]
            if not matches:
                line = LineString([xy(a), xy(b)])
                matches = [match for index in tree.query(line.buffer(tolerance))
                           if lines[index].buffer(tolerance).covers(line)
                           for match in evidence[source_edges[index]]]
            allowed = next((match for match in matches if match[0].allowed), None)
            if allowed and allowed[0].category == 'unverified':
                report.unknown_m += length
                report.issues.append({'reason': allowed[0].reason, 'length_m': length,
                                      'way_id': allowed[1], 'start': a, 'end': b})
            elif allowed:
                report.checked_m += length
                category = allowed[0].category
                report.categories_m[category] = report.categories_m.get(category, 0) + length
            elif matches:
                report.rejected_m += length
                decision, way_id, tags = matches[0]
                report.issues.append({'way_id': way_id, 'highway': tags.get('highway'),
                                     'name': tags.get('name'), 'reason': decision.reason,
                                     'length_m': length, 'start': a, 'end': b})
            else:
                report.unknown_m += length
                report.issues.append({'reason': 'No mapped edge match within 0.2 m; not verified',
                                     'length_m': length, 'start': a, 'end': b})
    return report
