import pytest
from parkloop.safety import assess_candidate_way, audit_route
from parkloop.parks import ParkGraph
from parkloop.alternatives import generate_alternatives


@pytest.mark.parametrize('tags',[
    {'highway':'motorway'}, {'highway':'trunk'},
    {'highway':'primary'}, {'highway':'service','sidewalk':'no'},
    {'highway':'service','foot':'no'}, {'highway':'service','access':'private'},
    {'highway':'service','sidewalk':'separate'},
    {'highway':'service','sidewalk:left':'no'},
])
def test_unverified_alternatives_do_not_admit_explicit_hazards(tags):
    assert not assess_candidate_way(tags).allowed


def test_missing_sidewalk_evidence_is_reported_not_certified():
    pts=[(0,0),(0,.01),(.01,.01),(.01,0),(0,0)]
    elements=[{'type':'way','nodes':[1,2,3,4,1],
               'geometry':[{'lat':a,'lon':b} for a,b in pts],
               'tags':{'highway':'service'}}]
    graph=ParkGraph(elements,park_only=False,include_unverified=True)
    target=sum(graph.edges[a][b] for a,b in [(1,2),(2,3),(3,4),(4,1)])
    alternatives=generate_alternatives(pts[0],target,elements=elements,prefer_parks=False)
    assert len(alternatives)==1  # Reversed copies are not alternatives.
    a=alternatives[0]
    assert a.audit.unknown_m==pytest.approx(target)
    assert a.audit.checked_m==a.audit.rejected_m==0
    assert not a.audit.passed
    assert '100%' in a.summary and 'Repeated paths: 0 m' in a.summary
    assert 'unverified' in a.result.route.description


def test_returns_several_distinct_loops_with_per_route_statistics():
    import math
    # Three separate diamond loops sharing only the requested start.
    elements=[]
    for j,angle in enumerate((0,2.1,4.2)):
        def point(x,y):
            return (x*math.cos(angle)-y*math.sin(angle),x*math.sin(angle)+y*math.cos(angle))
        points=[(0,0),point(.004,.003),point(.008,0),point(.004,-.003),(0,0)]
        elements.append({'type':'way','nodes':[0,j*3+1,j*3+2,j*3+3,0],
                         'geometry':[{'lat':a,'lon':b} for a,b in points],
                         'tags':{'highway':'footway'}})
    alternatives=generate_alternatives((0,0),2224,elements=elements,prefer_parks=False)
    assert len(alternatives)==3
    assert all(a.audit.passed for a in alternatives)
