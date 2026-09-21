"""Native Qt map, visible-viewport OSM tiles, and interactive track editing."""
import math
import os
import time
from pathlib import Path
from PySide6.QtCore import Qt, QPointF, QRectF, QUrl, Signal, QStandardPaths
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QWidget


MAP_MODES = ("light", "dark")
ARROW_SPACING_PX = 110.0
ARROW_SIZE_PX = 9.0


def arrow_placements(screen_pts, spacing: float = ARROW_SPACING_PX):
    """Positions + angles (radians) for direction chevrons along a polyline.

    Walks the screen-space polyline and drops an arrow every ``spacing``
    pixels so the travel direction is visible at any zoom.
    """
    placements = []
    if len(screen_pts) < 2:
        return placements
    carried = 0.0
    for a, b in zip(screen_pts, screen_pts[1:]):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        seg_len = math.hypot(dx, dy)
        if seg_len <= 0:
            continue
        angle = math.atan2(dy, dx)
        travelled = spacing - carried
        while travelled < seg_len:
            t = travelled / seg_len
            placements.append((QPointF(a.x() + dx * t, a.y() + dy * t), angle))
            travelled += spacing
        carried = (carried + seg_len) % spacing
    return placements


def _map_colors(mode: str = "light"):
    mode = str(mode).lower() if mode else "light"
    if mode not in MAP_MODES:
        mode = "light"
    try:
        from .theme import load_omarchy_colors, map_palette
        return map_palette(load_omarchy_colors(), mode=mode)
    except Exception:
        from .theme import map_palette as _fallback_palette
        return _fallback_palette(None, mode=mode)


class MapView(QWidget):
    clicked = Signal(float, float)
    moved = Signal(int, int, float, float)
    deleted = Signal(int, int)
    inserted = Signal(int, int, float, float)
    deleted_many = Signal(object)
    selection_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.zoom = 14
        self.center = (32.100, 34.811)
        self.route = None
        self.reference = None
        self.start = None
        self.edit = True
        self.pick_start = False
        self.select_mode = False
        self.selected = set()
        self.busy = False
        self.network = QNetworkAccessManager(self)
        self.tiles, self.pending, self.failed = {}, set(), {}
        import hashlib
        source = os.environ.get('PARKLOOP_TILE_URL', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
        self.cache = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / 'tiles' / hashlib.sha256(source.encode()).hexdigest()[:12]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.drag = None
        self.drag_position = None
        self.down = None
        self.old_center = None
        self.rubber_start = None
        self.rubber_current = None
        self.fitted_points = None
        self.map_mode = os.environ.get('PARKLOOP_MAP_MODE', 'light').lower()
        if self.map_mode not in MAP_MODES:
            self.map_mode = 'light'

    def set_map_mode(self, mode: str):
        """Set the map backdrop (``'light'`` or ``'dark'``)."""
        mode = str(mode).lower()
        if mode not in MAP_MODES:
            raise ValueError(f"map mode must be one of {MAP_MODES}")
        if mode != self.map_mode:
            self.map_mode = mode
            self.update()
        return self.map_mode

    def world(self, point):
        lat, lon = point
        n = 256 * 2**self.zoom
        lat = max(-85.05, min(85.05, lat))
        return QPointF((lon+180)/360*n, (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)

    def coord(self, p):
        n = 256 * 2**self.zoom
        return (math.degrees(math.atan(math.sinh(math.pi*(1-2*p.y()/n)))), (p.x()/n*360-180+180)%360-180)

    def screen(self, point):
        return self.world(point)-self.world(self.center)+QPointF(self.width()/2, self.height()/2)

    def at(self, pos):
        return self.coord(pos + self.world(self.center)-QPointF(self.width()/2, self.height()/2))

    def fit(self, points):
        if not points: return
        self.fitted_points = points
        self.center = ((min(p[0] for p in points)+max(p[0] for p in points))/2,
                       (min(p[1] for p in points)+max(p[1] for p in points))/2)
        for zoom in range(17, 2, -1):
            self.zoom = zoom
            pixels = [self.screen(p) for p in points]
            if max(p.x() for p in pixels)-min(p.x() for p in pixels) < self.width()-100 and max(p.y() for p in pixels)-min(p.y() for p in pixels) < self.height()-100: break
        self.update()

    def resizeEvent(self, event):
        if self.fitted_points:
            self.fit(self.fitted_points)
        super().resizeEvent(event)

    def load_tile(self, key):
        if key in self.pending or time.time()-self.failed.get(key, 0) < 60: return
        path = self.cache / ('-'.join(map(str, key))+'.png')
        if path.exists() and time.time()-path.stat().st_mtime < 7*86400:
            self.tiles[key] = QPixmap(str(path))
            return
        if len(self.pending) >= 8: return
        self.pending.add(key)
        z, x, y = key
        template = os.environ.get('PARKLOOP_TILE_URL', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
        req = QNetworkRequest(QUrl(template.format(z=z, x=x, y=y)))
        req.setRawHeader(b'User-Agent', b'ParkLoop/0.1 desktop GPX editor')
        req.setTransferTimeout(15000)
        reply = self.network.get(req)
        def finished():
            self.pending.discard(key)
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    if len(self.tiles) > 200: self.tiles.clear()
                    self.tiles[key] = pixmap
                    try: path.write_bytes(data)
                    except OSError: pass
            else: self.failed[key] = time.time()
            reply.deleteLater()
            self.update()
        reply.finished.connect(finished)

    def paintEvent(self, event):
        mc = _map_colors(self.map_mode)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(mc["background"]))
        origin = self.world(self.center)-QPointF(self.width()/2, self.height()/2)
        n = 2**self.zoom
        for x in range(math.floor(origin.x()/256), math.ceil((origin.x()+self.width())/256)):
            for y in range(math.floor(origin.y()/256), math.ceil((origin.y()+self.height())/256)):
                if not 0 <= y < n: continue
                key = self.zoom, x%n, y
                self.load_tile(key) if key not in self.tiles else None
                dest = QRectF(x*256-origin.x(), y*256-origin.y(), 256, 256)
                if key in self.tiles: p.drawPixmap(dest, self.tiles[key], QRectF(0,0,256,256))
                else:
                    p.setPen(QColor(mc["grid"])); p.drawRect(dest)
        if mc.get("tile_dim"):
            dim = QColor(mc["background"]); dim.setAlpha(int(mc["tile_dim"]))
            p.fillRect(self.rect(), dim)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for route, color, width in ((self.reference, mc["reference"], 3), (self.route, mc["route"], 5)):
            if not route: continue
            for i, segment in enumerate(route.segments):
                if not segment: continue
                screen_pts = [self.screen(pt) for pt in segment]
                path = QPainterPath(screen_pts[0])
                for pos in screen_pts[1:]: path.lineTo(pos)
                p.setPen(QPen(QColor('white'), width+3)); p.drawPath(path)
                p.setPen(QPen(QColor(color), width)); p.drawPath(path)
                if route is self.route:
                    self._paint_direction_arrows(p, screen_pts, color)
                if self.edit and route is self.route:
                    # Paint only distinct visible handles; hit testing still uses every point.
                    last = QPointF(-100,-100)
                    for j, pt in enumerate(segment):
                        pos = self.screen(pt)
                        if (pos-last).manhattanLength() > 15:
                            selected = (i, j) in self.selected
                            p.setPen(QPen(QColor(mc["route"] if selected else color), 2)); p.setBrush(QColor(mc["start"] if selected else mc["handle_fill"])); p.drawEllipse(pos, 6 if selected else 4, 6 if selected else 4); last = pos
        if self.start:
            pos = self.drag_position if self.drag == (0,0) and self.drag_position is not None else self.screen(self.start)
            p.setPen(QPen(QColor(mc["handle_fill"]), 3)); p.setBrush(QColor(mc["start"])); p.drawEllipse(pos, 9, 9)
        if self.drag is not None and self.drag_position is not None and self.route:
            i,j = self.drag
            segment = self.route.segments[i]
            # A dashed preview is distinct from the path calculated on release.
            p.setPen(QPen(QColor(mc['route']), 2, Qt.PenStyle.DashLine))
            for neighbour in (j-1,j+1):
                if 0 <= neighbour < len(segment):
                    p.drawLine(self.screen(segment[neighbour]), self.drag_position)
            p.setPen(QPen(QColor(mc['route']), 3))
            p.setBrush(QColor(mc['handle_fill']))
            p.drawEllipse(self.drag_position, 9, 9)
        if self.rubber_start is not None and self.rubber_current is not None:
            rect = QRectF(self.rubber_start, self.rubber_current).normalized()
            if rect.width() > 4 or rect.height() > 4:
                fill = QColor(mc['route']); fill.setAlpha(36)
                p.fillRect(rect, fill)
                p.setPen(QPen(QColor(mc['route']), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)
        p.fillRect(QRectF(self.width()-242,self.height()-28,242,28), QColor(mc["attribution_bg"]))
        p.setPen(QColor(mc["attribution_fg"])); p.drawText(self.width()-231,self.height()-10,'© OpenStreetMap contributors · ODbL')
        if self.failed:
            p.fillRect(QRectF(12,12,320,30), QColor(mc["notice_bg"]))
            p.setPen(QColor(mc["notice_fg"]))
            p.drawText(22,32,'Some map tiles unavailable. Route editing still works.')
        p.end()

    @staticmethod
    def _paint_direction_arrows(p, screen_pts, color):
        size = ARROW_SIZE_PX
        for pos, angle in arrow_placements(screen_pts):
            dx, dy = math.cos(angle), math.sin(angle)
            nx, ny = -dy, dx
            tip = QPointF(pos.x() + dx * size, pos.y() + dy * size)
            left = QPointF(pos.x() - dx * size * 0.7 + nx * size * 0.6,
                           pos.y() - dy * size * 0.7 + ny * size * 0.6)
            right = QPointF(pos.x() - dx * size * 0.7 - nx * size * 0.6,
                            pos.y() - dy * size * 0.7 - ny * size * 0.6)
            p.setPen(QPen(QColor('white'), 2))
            p.setBrush(QBrush(QColor(color)))
            p.drawPolygon(QPolygonF([tip, left, right]))

    def nearest(self, pos):
        best, distance = None, 11
        # The large start marker must win over nearby or overlapping track points.
        if self.route and self.route.segments and self.route.segments[0]:
            if (self.screen(self.route.segments[0][0])-pos).manhattanLength()<14:
                return (0,0)
        if self.route:
            for i, segment in enumerate(self.route.segments):
                for j, point in enumerate(segment):
                    d = (self.screen(point)-pos).manhattanLength()
                    if d < distance: best, distance = (i,j), d
        return best

    def points_in_rect(self, rect):
        """Route-point keys whose screen position falls inside ``rect``."""
        found = set()
        if not self.route or rect is None:
            return found
        rect = QRectF(rect).normalized()
        for i, segment in enumerate(self.route.segments):
            for j, point in enumerate(segment):
                if rect.contains(self.screen(point)):
                    found.add((i, j))
        return found

    def mousePressEvent(self, e):
        self.fitted_points = None
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.down = e.position()
        self.drag_position = None
        self.old_center = self.world(self.center)
        self.rubber_start = None
        self.rubber_current = None
        if e.button() == Qt.MouseButton.RightButton:
            hit = self.nearest(e.position()) if self.edit and not self.busy and not self.pick_start else None
            if hit is not None:
                if hit in self.selected and len(self.selected) > 1:
                    self.deleted_many.emit(set(self.selected))
                    self.selected.clear()
                    self.selection_changed.emit(set())
                    self.update()
                else:
                    self.deleted.emit(*hit)
            self.down = self.drag = None
            return
        selecting = self.select_mode or bool(e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if selecting and self.edit and not self.busy and not self.pick_start:
            # Ctrl/Cmd-drag (or drag in Select-points mode) draws a selection
            # rectangle instead of moving a point or panning the map.
            self.drag = None
            self.rubber_start = QPointF(e.position())
            self.rubber_current = QPointF(e.position())
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        self.drag = self.nearest(e.position()) if self.edit and not self.busy and not self.pick_start and not selecting else None

    def mouseMoveEvent(self, e):
        if self.down is None: return
        if self.rubber_start is not None:
            self.rubber_current = QPointF(e.position())
            self.update()
            return
        if self.drag is None:
            self.center = self.coord(self.old_center-(e.position()-self.down))
        elif not self.busy:
            self.drag_position = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.down is None: return
        if self.rubber_start is not None:
            rect = QRectF(self.rubber_start, e.position()).normalized()
            if rect.width() > 4 or rect.height() > 4:
                enclosed = self.points_in_rect(rect)
                if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    # Shift-drag adds to the existing selection so groups can
                    # be built up with several rectangles.
                    self.selected |= enclosed
                else:
                    self.selected = enclosed
                self.selection_changed.emit(set(self.selected))
            elif not self.busy:
                lat, lon = self.at(e.position())
                if self.pick_start:
                    self.clicked.emit(lat,lon)
                elif self.edit:
                    point=self.nearest(e.position())
                    if point is not None:
                        if point in self.selected:self.selected.remove(point)
                        else:self.selected.add(point)
                        self.selection_changed.emit(set(self.selected))
            self.down = self.drag = None
            self.drag_position = None
            self.rubber_start = None
            self.rubber_current = None
            self.unsetCursor()
            if self.select_mode:
                self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            return
        delta = (e.position()-self.down).manhattanLength()
        if not self.busy:
            lat, lon = self.at(e.position())
            if self.drag and delta > 3: self.moved.emit(*self.drag, lat, lon)
            elif delta < 4:
                if self.pick_start:
                    self.clicked.emit(lat,lon)
                elif self.edit and (self.select_mode or e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)):
                    point=self.nearest(e.position())
                    if point is not None:
                        if point in self.selected:self.selected.remove(point)
                        else:self.selected.add(point)
                        self.selection_changed.emit(set(self.selected)); self.update()
                elif self.edit and e.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.route:
                    best, dist = None, 20
                    for i, segment in enumerate(self.route.segments):
                        for j, (a,b) in enumerate(zip(segment, segment[1:])):
                            a,b = self.screen(a),self.screen(b); v=b-a; w=e.position()-a
                            t=max(0,min(1,(w.x()*v.x()+w.y()*v.y())/max(1,v.x()**2+v.y()**2)))
                            d=(a+v*t-e.position()).manhattanLength()
                            if d < dist: best,dist=(i,j+1),d
                    if best: self.inserted.emit(*best,lat,lon)
                elif self.drag is None: self.clicked.emit(lat,lon)
        self.down = self.drag = None
        self.drag_position = None
        self.unsetCursor()
        self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected and not self.busy:
            self.deleted_many.emit(set(self.selected)); self.selected.clear(); self.selection_changed.emit(set()); self.update(); e.accept(); return
        if e.key() == Qt.Key.Key_Escape:
            if self.rubber_start is not None:
                self.rubber_start = None; self.rubber_current = None
                self.down = self.drag = None; self.drag_position = None
                self.unsetCursor()
                if self.select_mode:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                self.update(); e.accept(); return
            if self.selected:
                self.selected.clear(); self.selection_changed.emit(set()); self.update(); e.accept(); return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        self.fitted_points = None
        before = self.at(e.position())
        self.zoom = max(3,min(19,self.zoom+(1 if e.angleDelta().y()>0 else -1)))
        after = self.at(e.position())
        self.center = (self.center[0]+before[0]-after[0],self.center[1]+before[1]-after[1])
        self.update()
