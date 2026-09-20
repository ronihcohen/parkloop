"""Persistent routing extracts and an optional, explicitly selected offline map."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .core import haversine_m, RoutingError


offline_path = None
_loaded = None


def cache_dir():
    from PySide6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'routing-maps'


def offline_elements():
    global _loaded
    path = os.environ.get('PARKLOOP_OFFLINE_MAP') or offline_path
    if not path:
        return None
    try:
        source = Path(path)
        key = (str(source), source.stat().st_mtime_ns)
        if _loaded and _loaded[0] == key:
            return _loaded[1]
        data = json.loads(source.read_text())
        elements = data.get('elements') if isinstance(data, dict) else data
        if not isinstance(elements, list) or not elements or not all(isinstance(el, dict) for el in elements):
            raise ValueError('Expected a nonempty Overpass elements list.')
        if not any(el.get('type') == 'way' and el.get('geometry') and el.get('nodes') for el in elements):
            raise ValueError('The map needs ways with node IDs and geometry.')
        _loaded = (key, elements)
        return elements
    except (OSError, ValueError, TypeError) as exc:
        raise RoutingError(f'Offline map could not be loaded: {exc}. Select another map or switch to downloaded maps.') from exc


def cached_elements(center, radius, provider):
    folder = cache_dir()
    if not folder.exists(): return None
    for path in sorted(folder.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text())
            if (data['provider'] == provider and
                    haversine_m(*center, *data['center']) + radius <= data['radius'] + 0.1):
                return data['elements']
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def save_elements(center, radius, provider, elements):
    folder = cache_dir()
    key = hashlib.sha256(json.dumps([center,radius,provider]).encode()).hexdigest()[:24]
    temporary = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=folder, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(dict(center=center,radius=radius,provider=provider,elements=elements),stream)
        temporary.replace(folder / f'{key}.json')
    except OSError:
        # A read-only/full disk must not discard a successfully downloaded map.
        pass
    finally:
        if temporary and temporary.exists(): temporary.unlink()
