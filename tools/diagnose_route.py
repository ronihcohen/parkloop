"""Offline reproduction: python tools/diagnose_route.py GPX OSM_JSON --km 10."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parkloop.core import read_gpx, RoutingError
from parkloop.parks import ParkGraph, generate_park_route
from parkloop.safety import audit_route


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gpx', type=Path)
    parser.add_argument('osm_json', type=Path)
    parser.add_argument('--km', type=float, default=10)
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    reference = read_gpx(args.gpx)
    data = json.loads(args.osm_json.read_text())
    elements = data['elements'] if isinstance(data, dict) else data
    audit = audit_route(reference, elements)
    report = {'reference_m': reference.distance_m, 'start': reference.geometry[0],
              'reference_audit': {'checked_m': audit.checked_m,
                                  'rejected_m': audit.rejected_m, 'unknown_m': audit.unknown_m}}
    graph = ParkGraph(elements, park_only=False)
    try:
        result = generate_park_route(reference.geometry[0], args.km * 1000,
                                     None, graph=graph, seed=args.seed)
        report['generated'] = {'distance_m': result.route.distance_m,
                               'park_fraction': result.park_fraction,
                               'start_offset_m': result.start_offset_m}
    except RoutingError as error:
        report['generation_error'] = str(error)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
