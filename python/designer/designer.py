from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon
from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from math import hypot
import copy

# Port layout constants
PORT_SPACING = 20
PORT_MARKER_SIZE = 6
MODULE_MIN_W = 140
MODULE_MIN_H = 60
MODULE_PAD = 15


class DesignerWidget(QWidget):
    """Canvas widget for the structural FPGA designer."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(800, 600)
        self.modules = []
        self.signals = []
        self._undo_stack = []

        # Drawing state
        self.mode = "select"  # 'draw' or 'select'
        self.drawing_wire = False
        self.current_wire = []

        # Selection state
        self.selected_wire = None
        self.selected_node = None
        self._move_start_pos = None
        self._move_orig_coords = None

        # Viewport state
        self.zoom = 1.0
        self.offset_x = 0
        self.offset_y = 0

        # Smooth arrow-key movement
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._move_timer = QTimer()
        self._move_timer.setInterval(16)  # ~60 FPS
        self._move_timer.timeout.connect(self._move_workspace)
        self._move_direction = None
        self._move_step = 20

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _save_undo(self):
        self._undo_stack.append(
            (copy.deepcopy(self.modules), copy.deepcopy(self.signals))
        )
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def undo(self):
        if self._undo_stack:
            self.modules, self.signals = self._undo_stack.pop()
            self.update()

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _snap_to_grid(pos):
        """Snap (x, y) to nearest multiple of 100."""
        return (round(pos[0] / 100) * 100, round(pos[1] / 100) * 100)

    @staticmethod
    def _distance_to_segment(px, py, x1, y1, x2, y2):
        if (x1, y1) == (x2, y2):
            return hypot(px - x1, py - y1)
        dx, dy = x2 - x1, y2 - y1
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        return hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    # ------------------------------------------------------------------
    # Module helpers
    # ------------------------------------------------------------------

    def next_instance_name(self) -> str:
        """Return the next available instance name U_N (lowest N >= 0)."""
        used = set()
        for mod in self.modules:
            n = mod.get('name', '')
            if n.startswith('U_'):
                try:
                    used.add(int(n[2:]))
                except ValueError:
                    pass
        n = 0
        while n in used:
            n += 1
        return f'U_{n}'

    # ------------------------------------------------------------------
    # Optimal recenter
    # ------------------------------------------------------------------

    def optimal_recenter(self):
        """Adjust zoom and offset so all modules and wires fit in the viewport."""
        points = []
        for mod in self.modules:
            x = mod.get('x', 0)
            y = mod.get('y', 0)
            ports = mod.get('ports', [])
            h = max(MODULE_MIN_H, (max(
                sum(1 for p in ports if p.get('side') == 'left'),
                sum(1 for p in ports if p.get('side') == 'right'),
                1,
            ) + 1) * PORT_SPACING)
            points.append((x, y))
            points.append((x + MODULE_MIN_W, y + h))
        for sig in self.signals:
            for coord in sig.get('coordinates', []):
                points.append((coord[0], coord[1]))

        if not points:
            return

        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_x = max(p[0] for p in points)
        max_y = max(p[1] for p in points)

        content_w = max_x - min_x
        content_h = max_y - min_y
        margin = 50

        if content_w == 0 and content_h == 0:
            self.zoom = 1.0
            self.offset_x = self.width() / 2 - min_x
            self.offset_y = self.height() / 2 - min_y
        else:
            zoom_x = (self.width() - 2 * margin) / max(content_w, 1)
            zoom_y = (self.height() - 2 * margin) / max(content_h, 1)
            self.zoom = max(min(zoom_x, zoom_y, 10.0), 0.01)
            cx = (min_x + max_x) / 2
            cy = (min_y + max_y) / 2
            self.offset_x = self.width() / 2 - cx * self.zoom
            self.offset_y = self.height() / 2 - cy * self.zoom
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.zoom, self.zoom)

        self._draw_grid(painter)
        self._draw_modules(painter)
        self._draw_signals(painter)
        self._draw_current_wire(painter)

        painter.restore()

    def _visible_rect(self):
        """Return the workspace-coordinate bounding box currently visible."""
        w = self.width()
        h = self.height()
        left = -self.offset_x / self.zoom
        top = -self.offset_y / self.zoom
        right = left + w / self.zoom
        bottom = top + h / self.zoom
        return left, top, right, bottom

    def _draw_grid(self, painter):
        """Draw grid dots only within the visible viewport."""
        left, top, right, bottom = self._visible_rect()
        # Align to grid boundaries (multiples of 100)
        grid = 100
        x_start = int(left // grid) * grid
        y_start = int(top // grid) * grid
        x_end = int(right // grid + 1) * grid
        y_end = int(bottom // grid + 1) * grid

        radius = 1
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        gx = x_start
        while gx <= x_end:
            gy = y_start
            while gy <= y_end:
                painter.drawEllipse(gx - radius, gy - radius, radius * 2, radius * 2)
                gy += grid
            gx += grid

    def _draw_modules(self, painter):
        for mod in self.modules:
            x = mod.get('x', 0)
            y = mod.get('y', 0)
            ports = mod.get('ports', [])

            # Count ports per side to size the block
            sides = {'left': [], 'right': [], 'top': [], 'bottom': []}
            for p in ports:
                sides[p.get('side', 'left')].append(p)
            max_vertical = max(len(sides['left']), len(sides['right']), 1)
            max_horizontal = max(len(sides['top']), len(sides['bottom']), 0)
            w = max(MODULE_MIN_W, (max_horizontal + 1) * PORT_SPACING + 2 * MODULE_PAD)
            h = max(MODULE_MIN_H, (max_vertical + 1) * PORT_SPACING)

            rect = QRect(x, y, w, h)
            painter.setBrush(QColor(0, 200, 0))
            painter.setPen(QPen(Qt.black, 2))
            painter.drawRect(rect)

            # Draw instance name (top) and entity name (center)
            painter.setPen(QPen(Qt.black, 1))
            painter.drawText(rect, Qt.AlignCenter, mod.get('name', ''))
            entity = mod.get('entity', '')
            if entity:
                painter.drawText(
                    rect.left(), rect.top() - 4,
                    rect.width(), 14,
                    Qt.AlignCenter, entity,
                )

            # Draw ports per side
            for side, port_list in sides.items():
                for idx, port in enumerate(port_list):
                    self._draw_port(painter, rect, port, side, idx, len(port_list))

    def _draw_port(self, painter, rect, port, side, idx, count):
        direction = port.get('direction', 'in')
        name = port.get('name', '')
        s = PORT_MARKER_SIZE

        # Compute anchor point on the block edge
        if side == 'left':
            px = rect.left()
            py = rect.top() + PORT_SPACING + idx * PORT_SPACING
        elif side == 'right':
            px = rect.right()
            py = rect.top() + PORT_SPACING + idx * PORT_SPACING
        elif side == 'top':
            px = rect.left() + MODULE_PAD + idx * PORT_SPACING
            py = rect.top()
        else:  # bottom
            px = rect.left() + MODULE_PAD + idx * PORT_SPACING
            py = rect.bottom()

        painter.setPen(QPen(QColor(0, 0, 255), 2))
        painter.setBrush(QColor(0, 0, 255))

        if direction in ('in', 'out'):
            self._draw_arrow_port(painter, px, py, side, direction, s)
        else:
            self._draw_diamond_port(painter, px, py, side, s)

        # Draw port name
        painter.setPen(QPen(Qt.black, 1))
        painter.setBrush(Qt.NoBrush)
        if side == 'left':
            painter.drawText(px - 5 * len(name) - 8, py + 4, name)
        elif side == 'right':
            painter.drawText(px + s + 4, py + 4, name)
        elif side == 'top':
            painter.save()
            painter.translate(px, py - s - 2)
            painter.rotate(-90)
            painter.drawText(0, 4, name)
            painter.restore()
        else:
            painter.save()
            painter.translate(px, py + s + 12)
            painter.rotate(-90)
            painter.drawText(0, 4, name)
            painter.restore()

    @staticmethod
    def _draw_arrow_port(painter, px, py, side, direction, s):
        """Draw an arrowhead-only port marker (no tail)."""
        # Determine which way the arrow points
        # 'in' points toward the module, 'out' points away
        inward = (direction == 'in')
        if side == 'left':
            tip_x = px if inward else px - s
            base_x = px - s if inward else px
            painter.drawPolygon(QPolygon([
                QPoint(tip_x, py),
                QPoint(base_x, py - s // 2),
                QPoint(base_x, py + s // 2),
            ]))
        elif side == 'right':
            tip_x = px if inward else px + s
            base_x = px + s if inward else px
            painter.drawPolygon(QPolygon([
                QPoint(tip_x, py),
                QPoint(base_x, py - s // 2),
                QPoint(base_x, py + s // 2),
            ]))
        elif side == 'top':
            tip_y = py if inward else py - s
            base_y = py - s if inward else py
            painter.drawPolygon(QPolygon([
                QPoint(px, tip_y),
                QPoint(px - s // 2, base_y),
                QPoint(px + s // 2, base_y),
            ]))
        else:  # bottom
            tip_y = py if inward else py + s
            base_y = py + s if inward else py
            painter.drawPolygon(QPolygon([
                QPoint(px, tip_y),
                QPoint(px - s // 2, base_y),
                QPoint(px + s // 2, base_y),
            ]))

    @staticmethod
    def _draw_diamond_port(painter, px, py, side, s):
        """Draw a diamond marker for inout/buffer ports."""
        if side in ('left', 'right'):
            cx = px - s if side == 'left' else px + s
            painter.drawPolygon(QPolygon([
                QPoint(cx, py - s // 2),
                QPoint(cx - s // 2, py),
                QPoint(cx, py + s // 2),
                QPoint(cx + s // 2, py),
            ]))
        else:
            cy = py - s if side == 'top' else py + s
            painter.drawPolygon(QPolygon([
                QPoint(px - s // 2, cy),
                QPoint(px, cy - s // 2),
                QPoint(px + s // 2, cy),
                QPoint(px, cy + s // 2),
            ]))

    def _draw_signals(self, painter):
        painter.setPen(QPen(QColor(0, 0, 255), 3))
        for sig in self.signals:
            coords = sig.get('coordinates', [])
            for i in range(len(coords) - 1):
                painter.drawLine(
                    coords[i][0], coords[i][1],
                    coords[i + 1][0], coords[i + 1][1],
                )

    def _draw_current_wire(self, painter):
        if self.drawing_wire and len(self.current_wire) > 1:
            painter.setPen(QPen(QColor(0, 200, 200), 2, Qt.DashLine))
            for i in range(len(self.current_wire) - 1):
                painter.drawLine(
                    self.current_wire[i][0], self.current_wire[i][1],
                    self.current_wire[i + 1][0], self.current_wire[i + 1][1],
                )

    # ------------------------------------------------------------------
    # Viewport: zoom / pan
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        mouse_x = event.position().x()
        mouse_y = event.position().y()
        # Workspace point under cursor before zoom
        wx = (mouse_x - self.offset_x) / self.zoom
        wy = (mouse_y - self.offset_y) / self.zoom

        delta = event.angleDelta().y()
        old_zoom = self.zoom
        if delta > 0:
            self.zoom = min(self.zoom + 0.1, 10.0)
        else:
            self.zoom = max(self.zoom - 0.1, 0.01)

        # Adjust offset so the same workspace point stays under the cursor
        self.offset_x = mouse_x - wx * self.zoom
        self.offset_y = mouse_y - wy * self.zoom
        self.update()

    def _move_workspace(self):
        if self._move_direction == 'left':
            self.offset_x += self._move_step
        elif self._move_direction == 'right':
            self.offset_x -= self._move_step
        elif self._move_direction == 'up':
            self.offset_y += self._move_step
        elif self._move_direction == 'down':
            self.offset_y -= self._move_step
        self.update()

    def _transform_mouse(self, event):
        x = (event.position().x() - self.offset_x) / self.zoom
        y = (event.position().y() - self.offset_y) / self.zoom
        return (int(x), int(y))

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        # Escape cancels any ongoing action
        if event.key() == Qt.Key_Escape:
            self._cancel_action()
            return

        # Ctrl+Z — undo
        if event.key() == Qt.Key_Z and (event.modifiers() & Qt.ControlModifier):
            self.undo()
            return

        # Delete key — remove selected wire
        if event.key() == Qt.Key_Delete:
            self._delete_selected()
            return

        # Arrow keys — smooth workspace movement
        if event.isAutoRepeat():
            return
        direction_map = {
            Qt.Key_Left: 'left', Qt.Key_Right: 'right',
            Qt.Key_Up: 'up', Qt.Key_Down: 'down',
        }
        if event.key() in direction_map:
            self._move_direction = direction_map[event.key()]
            self._move_timer.start()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._move_timer.stop()
            self._move_direction = None

    def _cancel_action(self):
        self.drawing_wire = False
        self.current_wire = []
        self.selected_wire = None
        self.selected_node = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self.update()

    def _delete_selected(self):
        if self.mode == "select" and self.selected_wire is not None:
            self._save_undo()
            del self.signals[self.selected_wire]
            self.selected_wire = None
            self.selected_node = None
            self._move_start_pos = None
            self._move_orig_coords = None
            self.update()

    # ------------------------------------------------------------------
    # Mouse — drawing / selecting
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = self._snap_to_grid(self._transform_mouse(event))

        if self.mode == "draw":
            self._handle_draw_click(pos)
        elif self.mode == "select":
            self._handle_select_click(pos)

    def _handle_draw_click(self, pos):
        if not self.drawing_wire:
            self.drawing_wire = True
            self.current_wire = [pos]
        else:
            last = self.current_wire[-1]
            mid = self._make_90_degree_mid(last, pos)
            self.current_wire.append(self._snap_to_grid(mid))
            self.current_wire.append(pos)
        self.update()

    def _handle_select_click(self, pos):
        self.selected_wire = None
        self.selected_node = None

        # Try to select a node first
        min_dist = 15
        for idx, sig in enumerate(self.signals):
            for nidx, node in enumerate(sig.get('coordinates', [])):
                dist = hypot(pos[0] - node[0], pos[1] - node[1])
                if dist < min_dist:
                    min_dist = dist
                    self.selected_wire = idx
                    self.selected_node = nidx

        # Fall back to whole-wire selection
        if self.selected_wire is None:
            min_dist = 15
            for idx, sig in enumerate(self.signals):
                coords = sig.get('coordinates', [])
                for i in range(len(coords) - 1):
                    d = self._distance_to_segment(
                        pos[0], pos[1],
                        coords[i][0], coords[i][1],
                        coords[i + 1][0], coords[i + 1][1],
                    )
                    if d < min_dist:
                        min_dist = d
                        self.selected_wire = idx
                        self.selected_node = None

        # Store move origin
        self._move_start_pos = pos
        self._move_orig_coords = None
        if self.selected_wire is not None:
            if self.selected_node is not None:
                self._move_orig_coords = self.signals[self.selected_wire]['coordinates'][self.selected_node]
            else:
                self._move_orig_coords = list(self.signals[self.selected_wire]['coordinates'])
        self.update()

    def mouseReleaseEvent(self, event):
        if self.mode == "select" and self.selected_wire is not None:
            self._save_undo()
            self.selected_wire = None
            self.selected_node = None
            self._move_start_pos = None
            self._move_orig_coords = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        if self.drawing_wire and len(self.current_wire) > 1:
            self._save_undo()
            self.drawing_wire = False
            self.signals.append({'coordinates': self.current_wire})
            self.current_wire = []
            self.update()

    def mouseMoveEvent(self, event):
        pos = self._snap_to_grid(self._transform_mouse(event))

        if self.mode == "draw" and self.drawing_wire and self.current_wire:
            last = self.current_wire[-1]
            mid = self._snap_to_grid(self._make_90_degree_mid(last, pos))
            # Replace trailing preview segment
            self.current_wire = (
                self.current_wire[:-2] if len(self.current_wire) > 2 else self.current_wire
            )
            self.current_wire += [mid, pos]
            self.update()

        elif self.mode == "select" and self.selected_wire is not None and self._move_start_pos:
            if self.selected_node is not None:
                self.signals[self.selected_wire]['coordinates'][self.selected_node] = pos
            else:
                dx = pos[0] - self._move_start_pos[0]
                dy = pos[1] - self._move_start_pos[1]
                self.signals[self.selected_wire]['coordinates'] = [
                    (x + dx, y + dy) for (x, y) in self._move_orig_coords
                ]
            self.update()

    @staticmethod
    def _make_90_degree_mid(last, pos):
        if abs(pos[0] - last[0]) > abs(pos[1] - last[1]):
            return (pos[0], last[1])
        return (last[0], pos[1])
