"""Distinct loop alternatives with separate map-evidence statistics."""
from dataclasses import dataclass
from .core import RoutingError
from .parks import ParkGraph, fetch_elements, generate_park_route
from .quality import shared_path_fraction, MAX_ALTERNATIVE_SHARED_FRACTION
from .safety import audit_route


@dataclass
class Alternative:
    result: object
    audit: object
    target_m: float

    @property
    def summary(self):
        r, a = self.result, self.audit
        distance = r.route.distance_m
        return (f'{distance / 1000:.2f} km ({(distance/self.target_m-1):+.1%} from target) · '
                f'{r.park_fraction:.0%} green-zone coverage\n'
                f'Navigation turns: {r.turn_count}\n'
                f'Map-checked: {a.checked_m/1000:.2f} km ({a.checked_m/distance:.0%})\n'
                f'Unverified: {a.unknown_m/1000:.2f} km ({a.unknown_m/distance:.0%}) · '
                f'Excluded: {a.rejected_m:.0f} m\n'
                f'Repeated paths: 0 m · Start offset: {r.start_offset_m:.0f} m')


def generate_alternatives(start, target_m, *, elements=None, strict=False,
                          prefer_parks=True, cancel=None, progress=None, count=3,
                          diagnostic=None):
    elements = fetch_elements(start, target_m, diagnostic=diagnostic) if elements is None else elements
    if diagnostic: diagnostic(f'Building route graph from {len(elements)} map elements')
    alternatives, signatures, errors = [], set(), []
    for allow_unknown in (False, True):
        graph = ParkGraph(elements, park_only=strict, include_unverified=allow_unknown)
        if diagnostic:
            diagnostic(f'Graph ready: nodes={len(graph.points)}, directed_origins={len(graph.edges)}, '
                       f'include_unverified={allow_unknown}')
        for attempt in range(4):
            if cancel is not None and cancel.is_set(): raise RoutingError('Cancelled.')
            if progress:
                progress(f'Finding alternatives: {len(alternatives)} found; '
                         + ('including unverified local roads…' if allow_unknown else 'map-checked paths…'))
            try:
                result = generate_park_route(start, target_m, None, graph=graph,
                    seed=attempt, strict=strict, prefer_parks=prefer_parks,
                    cancel=cancel, progress=progress, excluded_signatures=signatures)
            except RoutingError as error:
                if diagnostic: diagnostic(f'Route search attempt {attempt + 1} failed: {error}')
                if cancel is not None and cancel.is_set(): raise
                errors.append(str(error))
                # Connectivity/size failures cannot change with another seed.
                if 'Search limit reached' not in str(error): break
                continue
            points = result.route.geometry
            signature = frozenset(tuple(sorted((a,b))) for a,b in zip(points,points[1:]))
            overlap=max((shared_path_fraction(points, previous.result.route.geometry)
                         for previous in alternatives),default=0.0)
            if overlap > MAX_ALTERNATIVE_SHARED_FRACTION:
                if diagnostic:
                    diagnostic(f'Candidate rejected as too similar: {overlap:.1%} shared path')
                continue
            signatures.add(signature)
            audit = audit_route(result.route, elements, include_unverified=True)
            alternatives.append(Alternative(result, audit, target_m))
            if diagnostic:
                diagnostic(f'Alternative {len(alternatives)}: distance={result.route.distance_m:.1f}m, '
                           f'green={result.park_fraction:.1%}, checked={audit.checked_m:.1f}m, '
                           f'unverified={audit.unknown_m:.1f}m, turns={result.turn_count}')
            if len(alternatives) >= count: break
        if len(alternatives) >= count: break
    if not alternatives: raise RoutingError(errors[-1] if errors else 'No alternatives found.')
    alternatives.sort(key=lambda a: (a.audit.unknown_m > 0, a.audit.unknown_m,
                                     -a.result.park_fraction, abs(a.result.route.distance_m-target_m)))
    return alternatives
