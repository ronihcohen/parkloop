"""Geometric validation shared by every automatic route generator."""
import math
from shapely.geometry import LineString
from shapely.ops import unary_union

# Numerical tolerance only (0.1 mm), not permission to repeat a short path.
OVERLAP_EPSILON_M = 0.0001


def repeated_path_m(points):
    """Measure path used more than once, regardless of direction or segmentation.

    Union length counts each geometric section once. Subtracting that from
    traversal length also detects overlapping ways with different OSM node IDs,
    or a return geometry sampled at different points. Point crossings are fine;
    adjacent parallel paths are not treated as the same path.
    """
    if len(points) < 2:
        return 0.0
    lat0, lon0 = points[0]
    xscale = 111320 * math.cos(math.radians(lat0))
    projected = [((lon - lon0) * xscale, (lat - lat0) * 111320)
                 for lat, lon in points]
    lines = [LineString([a, b]) for a, b in zip(projected, projected[1:]) if a != b]
    if not lines:
        return 0.0
    return max(0.0, sum(line.length for line in lines) - unary_union(lines).length)


def has_repeated_path(points):
    return repeated_path_m(points) > OVERLAP_EPSILON_M


def bridge_edges(edges):
    """Undirected bridges cannot appear in a closed route without being reused.

    Iterative Tarjan traversal avoids Python recursion limits on large maps.
    Directed access rules remain in the routing graph after these edges are cut.
    """
    neighbors = {}
    for a, outgoing in edges.items():
        for b in outgoing:
            neighbors.setdefault(a,set()).add(b)
            neighbors.setdefault(b,set()).add(a)
    entered,low,parent = {},{},{}
    bridges = set()
    clock = 0
    for root in neighbors:
        if root in entered: continue
        entered[root]=low[root]=clock;clock+=1
        stack=[(root,iter(neighbors[root]))]
        while stack:
            a,iterator=stack[-1]
            b=next(iterator,None)
            if b is None:
                stack.pop()
                if a in parent:
                    p=parent[a]
                    low[p]=min(low[p],low[a])
                    if low[a]>entered[p]:bridges.add(tuple(sorted((p,a))))
                continue
            if b==parent.get(a):continue
            if b in entered:
                low[a]=min(low[a],entered[b])
            else:
                parent[b]=a;entered[b]=low[b]=clock;clock+=1
                stack.append((b,iter(neighbors[b])))
    return bridges
