from pathlib import Path
import pytest
from parkloop.core import Route,write_gpx,read_gpx,haversine_m,validate_coords


def test_gpx_roundtrip_keeps_segment_breaks_and_elevations(tmp_path):
    r=Route('Run & river', [[(32,34),(32.001,34.001)],[(33,35),(33.001,35.001)]],[[4,5],[None,8]])
    p=tmp_path/'run.gpx';write_gpx(r,p);loaded=read_gpx(p)
    assert loaded.segments==r.segments
    assert loaded.elevations==r.elevations
    assert loaded.name==r.name
    assert loaded.distance_m==pytest.approx(r.distance_m)
    assert loaded.distance_m < 1000  # no artificial connection across separate tracks


@pytest.mark.parametrize('p',[(float('nan'),3),(91,3),(4,181),(4,float('inf'))])
def test_reject_invalid_coordinates(p):
    with pytest.raises(ValueError):validate_coords(*p)


def test_unqualified_gpx(tmp_path):
    p=tmp_path/'route.gpx';p.write_text('<gpx><rte><name>Example</name><rtept lat="32" lon="34"/><rtept lat="32.1" lon="34.1"/></rte></gpx>')
    assert read_gpx(p).name=='Example'


def test_invalid_track_rejected(tmp_path):
    p=tmp_path/'bad.gpx';p.write_text('<gpx><trk><trkseg><trkpt lat="nan" lon="34"/></trkseg></trk></gpx>')
    with pytest.raises(ValueError):read_gpx(p)
