import os
import urllib.error

import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from parkloop.app import Window
from parkloop.core import RoutingError, fetch_json
from parkloop.mapview import MapView


def test_http_failure_diagnostic_records_each_attempt(monkeypatch):
    events=[]
    def fail(*args,**kwargs):
        raise urllib.error.HTTPError('https://example.test',504,'Gateway Timeout',{},None)
    monkeypatch.setattr('urllib.request.urlopen',fail)
    monkeypatch.setattr('time.sleep',lambda _:None)
    with pytest.raises(RoutingError,match='504'):
        fetch_json('https://example.test',data=b'x',retries=2,diagnostic=events.append)
    assert sum('HTTP attempt' in event and 'failed' not in event for event in events)==2
    assert sum('HTTPError: HTTP Error 504' in event for event in events)==2


def test_auto_route_failure_writes_reproducible_log(tmp_path,monkeypatch):
    QApplication.instance() or QApplication([])
    path=tmp_path/'last-auto-route.log'
    monkeypatch.setattr('parkloop.app.auto_route_log_path',lambda:path)
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    def fail(*args,diagnostic=None,**kwargs):
        diagnostic('Map cache miss: provider=https://example.test')
        diagnostic('HTTP attempt 1 failed: HTTPError: HTTP Error 504: Gateway Timeout')
        raise RoutingError('Map service unavailable: HTTP Error 504: Gateway Timeout')
    monkeypatch.setattr('parkloop.app.generate_alternatives',fail)
    w=Window()
    w.start=(47.600958,19.112548)
    w.distance.setValue(10)
    w.job=lambda work,callback:work()
    with pytest.raises(RoutingError,match='Debug log'):
        w.auto()
    contents=path.read_text()
    assert 'Start: 47.6009580, 19.1125480' in contents
    assert 'Target: 10000 m' in contents
    assert 'Map cache miss' in contents
    assert 'HTTP Error 504' in contents
    assert 'Traceback (most recent call last)' in contents
    assert 'FAILED: RoutingError' in contents
    w.close()
