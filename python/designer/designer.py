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

# Hit-testing radii in *screen pixels*.  Divided by the current zoom
# factor at runtime to obtain world-coordinate thresholds.
NODE_HIT_RADIUS_PX = 10   # must click very close to grab a single node
SEGMENT_HIT_RADIUS_PX = 12  # slightly larger for segment/wire body clicks


class DesignerWidget(QWidget):
    """Canvas widget for the structural FPGA designer."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(800, 600)
        self.modules = []
        self.signals = []
        self._undo_stack = []
        self._redo_stack = []

        # Drawing state
        self.mode = "select"  # 'draw' or 'select'
        self.drawing_wire = False
        self.current_wire = []

        # Selection state (multi-select)
        self.selected_modules = set()  # Indices of selected modules
        self.selected_wires = set()    # Indices of selected wires

        # Drag state for moving objects
        self._drag_type = None  # 'move_selection', 'node', 'rubber_band'
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None

        # Rubber-band selection rectangle
        self._rubber_band_start = None
        self._rubber_band_end = None

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
    # Undo / Redo
    #
    # Invariant: _save_undo() is ALWAYS called BEFORE the state is mutated.
    # This ensures the snapshot on the stack represents the state the user
    # can return to.  For drag operations the snapshot is taken when the
    # drag begins; if the user releases without actually moving, the
    # snapshot is discarded (_pop_undo_if_unchanged).
    # ------------------------------------------------------------------

    def _save_undo(self):
        """Snapshot the current (pre-mutation) state onto the undo stack.

        Must be called BEFORE any modification to self.modules / self.signals.
        Clears the redo stack because a new action invalidates the redo branch.
        """
        self._undo_stack.append(
            (copy.deepcopy(self.modules), copy.deepcopy(self.signals))
        )
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _pop_undo_if_unchanged(self):
        """Discard the last undo snapshot if the state hasn't changed.

        Called on mouse-release when a drag started (snapshot was taken)
        but the user did not actually move anything.
        """
        if not self._undo_stack:
            return
        prev_modules, prev_signals = self._undo_stack[-1]
        if prev_modules == self.modules and prev_signals == self.signals:
            self._undo_stack.pop()

    def undo(self):
        """Revert to the previous state.  The current state is pushed to redo."""
        if not self._undo_stack:
            return
        self._redo_stack.append(
            (copy.deepcopy(self.modules), copy.deepcopy(self.signals))
        )
        self.modules, self.signals = self._undo_stack.pop()
        self.selected_modules.clear()
        self.selected_wires.clear()
        self.update()

    def redo(self):
        """Re-apply the last undone action."""
        if not self._redo_stack:
            return
        self._undo_stack.append(
            (copy.deepcopy(self.modules), copy.deepcopy(self.signals))
        )
        self.modules, self.signals = self._redo_stack.pop()
        self.selected_modules.clear()
        self.selected_wires.clear()
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

    @staticmethod
    def _segment_intersects_rect(x1, y1, x2, y2, rect: QRect) -> bool:
        """Return True if the line segment (x1,y1)-(x2,y2) intersects rect.

        Uses Liang-Barsky clipping on the axis-aligned rectangle.
        """
        rl = rect.left()
        rr = rect.right()
        rt = rect.top()
        rb = rect.bottom()

        dx = x2 - x1
        dy = y2 - y1
        p = [-dx, dx, -dy, dy]
        q = [x1 - rl, rr - x1, y1 - rt, rb - y1]

        t0, t1 = 0.0, 1.0
        for pi, qi in zip(p, q):
            if pi == 0:
                if qi < 0:
                    return False
            else:
                t = qi / pi
                if pi < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 > t1:
                    return False
        return True

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

    def _module_rect(self, mod) -> QRect:
        """Compute the bounding rectangle for a module."""
        x = mod.get('x', 0)
        y = mod.get('y', 0)
        ports = mod.get('ports', [])
        sides = {'left': 0, 'right': 0, 'top': 0, 'bottom': 0}
        for p in ports:
            sides[p.get('side', 'left')] += 1
        max_vertical = max(sides['left'], sides['right'], 1)
        max_horizontal = max(sides['top'], sides['bottom'], 0)
        w = max(MODULE_MIN_W, (max_horizontal + 1) * PORT_SPACING + 2 * MODULE_PAD)
        h = max(MODULE_MIN_H, (max_vertical + 1) * PORT_SPACING)
        return QRect(x, y, w, h)

    def _hit_test_module(self, raw_pos) -> int | None:
        """Return the index of the topmost module whose rect contains *raw_pos*.

        *raw_pos* should be the unsnapped world-coordinate cursor position
        so that the test is pixel-accurate regardless of zoom.
        """
        pt = QPoint(int(raw_pos[0]), int(raw_pos[1]))
        for idx in range(len(self.modules) - 1, -1, -1):
            rect = self._module_rect(self.modules[idx])
            if rect.contains(pt):
                return idx
        return None

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
        self._draw_rubber_band(painter)

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
        for mod_idx, mod in enumerate(self.modules):
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
            # Highlight selected modules with blue boundaries
            if mod_idx in self.selected_modules:
                painter.setPen(QPen(QColor(0, 100, 255), 3))
            else:
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
        for sig_idx, sig in enumerate(self.signals):
            coords = sig.get('coordinates', [])
            # Selected wires: highlighted in blue with thicker lines
            if sig_idx in self.selected_wires:
                painter.setPen(QPen(QColor(0, 100, 255), 5))
            else:
                painter.setPen(QPen(QColor(0, 0, 255), 3))
            for i in range(len(coords) - 1):
                painter.drawLine(
                    coords[i][0], coords[i][1],
                    coords[i + 1][0], coords[i + 1][1],
                )
            # Draw signal name with slightly bigger font when selected
            if sig_idx in self.selected_wires and coords:
                name = sig.get('name', '')
                if name:
                    font = painter.font()
                    old_size = font.pointSize()
                    font.setPointSize(old_size + 2)
                    painter.setFont(font)
                    mid = len(coords) // 2
                    painter.drawText(coords[mid][0] + 5, coords[mid][1] - 5, name)
                    font.setPointSize(old_size)
                    painter.setFont(font)

    def _draw_current_wire(self, painter):
        if self.drawing_wire and len(self.current_wire) > 1:
            painter.setPen(QPen(QColor(0, 200, 200), 2, Qt.DashLine))
            for i in range(len(self.current_wire) - 1):
                painter.drawLine(
                    self.current_wire[i][0], self.current_wire[i][1],
                    self.current_wire[i + 1][0], self.current_wire[i + 1][1],
                )

    def _draw_rubber_band(self, painter):
        """Draw the selection rectangle during rubber-band selection."""
        if self._rubber_band_start and self._rubber_band_end:
            x1, y1 = self._rubber_band_start
            x2, y2 = self._rubber_band_end
            rect = QRect(
                int(min(x1, x2)), int(min(y1, y2)),
                int(abs(x2 - x1)), int(abs(y2 - y1)),
            )
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.DashLine))
            painter.setBrush(QColor(0, 120, 215, 40))
            painter.drawRect(rect)

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

        # Ctrl+E — redo
        if event.key() == Qt.Key_E and (event.modifiers() & Qt.ControlModifier):
            self.redo()
            return

        # Delete key — remove all selected objects
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
        self.selected_modules.clear()
        self.selected_wires.clear()
        self._drag_type = None
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None
        self._rubber_band_start = None
        self._rubber_band_end = None
        self.update()

    def _delete_selected(self):
        """Delete all currently selected objects (modules and wires)."""
        if self.mode != "select":
            return
        if not self.selected_modules and not self.selected_wires:
            return
        self._save_undo()
        # Remove selected wires (reverse order to preserve indices)
        for idx in sorted(self.selected_wires, reverse=True):
            if 0 <= idx < len(self.signals):
                del self.signals[idx]
        # Remove selected modules (reverse order to preserve indices)
        for idx in sorted(self.selected_modules, reverse=True):
            if 0 <= idx < len(self.modules):
                del self.modules[idx]
        self.selected_modules.clear()
        self.selected_wires.clear()
        self._drag_type = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self.update()

    # ------------------------------------------------------------------
    # Mouse — drawing / selecting
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        raw_pos = self._transform_mouse(event)
        pos = self._snap_to_grid(raw_pos)
        ctrl = bool(event.modifiers() & Qt.ControlModifier)

        if self.mode == "draw":
            self._handle_draw_click(pos)
        elif self.mode == "select":
            self._handle_select_click(pos, raw_pos, ctrl)

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

    def _handle_select_click(self, pos, raw_pos, ctrl=False):
        """Handle a click in select mode.

        Hit-testing is performed against *raw_pos* (unsnapped world coords)
        so that accuracy is independent of the snap grid.  Thresholds are
        expressed in screen pixels and converted to world units using the
        current zoom factor.

        *pos* (snapped) is only used for the starting position of a drag
        so that movement deltas stay on-grid.

        - Plain click on an object: select it exclusively (clears previous).
        - CTRL+click: toggle the object in/out of the current selection.
        - Click on empty space: start rubber-band selection.
        """
        # Use the unsnapped cursor for hit testing
        rx, ry = raw_pos

        hit_module = self._hit_test_module(raw_pos)
        hit_wire = None
        hit_node = None

        # --- 1. Try to hit a wire node (small radius) ---
        node_threshold = NODE_HIT_RADIUS_PX / max(self.zoom, 0.01)
        best_node_dist = node_threshold
        for idx, sig in enumerate(self.signals):
            for nidx, node in enumerate(sig.get('coordinates', [])):
                dist = hypot(rx - node[0], ry - node[1])
                if dist < best_node_dist:
                    best_node_dist = dist
                    hit_wire = idx
                    hit_node = nidx

        # --- 2. Try to hit a wire segment (slightly larger radius) ---
        seg_threshold = SEGMENT_HIT_RADIUS_PX / max(self.zoom, 0.01)
        best_seg_dist = seg_threshold
        hit_seg_wire = None
        for idx, sig in enumerate(self.signals):
            coords = sig.get('coordinates', [])
            for i in range(len(coords) - 1):
                d = self._distance_to_segment(
                    rx, ry,
                    coords[i][0], coords[i][1],
                    coords[i + 1][0], coords[i + 1][1],
                )
                if d < best_seg_dist:
                    best_seg_dist = d
                    hit_seg_wire = idx

        # If a segment was hit but no node was, prefer the segment.
        # This prevents accidentally grabbing a corner node when the
        # user intended to drag the whole wire.
        if hit_wire is None and hit_seg_wire is not None:
            hit_wire = hit_seg_wire
            hit_node = None

        # --- Decide action based on what was hit ---
        if hit_module is not None:
            if ctrl:
                # CTRL+click: toggle this module in/out of the selection
                if hit_module in self.selected_modules:
                    self.selected_modules.discard(hit_module)
                else:
                    self.selected_modules.add(hit_module)
                self._drag_type = None
            else:
                # Plain click on a module
                if hit_module not in self.selected_modules:
                    self.selected_modules.clear()
                    self.selected_wires.clear()
                    self.selected_modules.add(hit_module)
                # Snapshot state BEFORE any movement occurs
                self._save_undo()
                self._drag_type = 'move_selection'
                self._move_start_pos = pos
                self._save_move_origins()

        elif hit_wire is not None and hit_node is not None:
            if ctrl:
                # CTRL+click on a node: toggle the wire in the selection
                if hit_wire in self.selected_wires:
                    self.selected_wires.discard(hit_wire)
                else:
                    self.selected_wires.add(hit_wire)
                self._drag_type = None
            else:
                # Plain click on a wire node — drag that single node only
                self.selected_modules.clear()
                self.selected_wires.clear()
                self.selected_wires.add(hit_wire)
                # Snapshot state BEFORE any movement occurs
                self._save_undo()
                self._drag_type = 'node'
                self._drag_wire_idx = hit_wire
                self._drag_node_idx = hit_node
                self._move_start_pos = pos
                self._move_orig_coords = tuple(
                    self.signals[hit_wire]['coordinates'][hit_node]
                )

        elif hit_wire is not None:
            if ctrl:
                # CTRL+click on a wire segment: toggle in selection
                if hit_wire in self.selected_wires:
                    self.selected_wires.discard(hit_wire)
                else:
                    self.selected_wires.add(hit_wire)
                self._drag_type = None
            else:
                # Plain click on a wire segment — move all selected together
                if hit_wire not in self.selected_wires:
                    self.selected_modules.clear()
                    self.selected_wires.clear()
                    self.selected_wires.add(hit_wire)
                # Snapshot state BEFORE any movement occurs
                self._save_undo()
                self._drag_type = 'move_selection'
                self._move_start_pos = pos
                self._save_move_origins()

        else:
            # Clicked on empty space — start rubber-band selection
            if not ctrl:
                self.selected_modules.clear()
                self.selected_wires.clear()
            self._drag_type = 'rubber_band'
            self._rubber_band_start = raw_pos
            self._rubber_band_end = raw_pos

        self.update()

    def _save_move_origins(self):
        """Snapshot the original positions of every selected object before a move."""
        self._move_orig_module_positions = {
            idx: (self.modules[idx]['x'], self.modules[idx]['y'])
            for idx in self.selected_modules
        }
        self._move_orig_wire_positions = {
            idx: list(self.signals[idx]['coordinates'])
            for idx in self.selected_wires
        }

    def mouseReleaseEvent(self, event):
        if self.mode != "select" or self._drag_type is None:
            return

        if self._drag_type == 'rubber_band':
            # Finalize rubber-band: select all objects within the rectangle
            self._finalize_rubber_band()
        elif self._drag_type in ('move_selection', 'node'):
            # Undo snapshot was taken at drag-start.  If the user released
            # without actually moving anything, discard it to keep the
            # undo stack clean.
            self._pop_undo_if_unchanged()

        # Reset drag state (keep selection visible)
        self._drag_type = None
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None
        self._rubber_band_start = None
        self._rubber_band_end = None
        self.update()

    def _finalize_rubber_band(self):
        """Select all modules and wires that intersect the rubber-band rect."""
        if not self._rubber_band_start or not self._rubber_band_end:
            return
        x1, y1 = self._rubber_band_start
        x2, y2 = self._rubber_band_end
        sel_rect = QRect(
            int(min(x1, x2)), int(min(y1, y2)),
            int(abs(x2 - x1)), int(abs(y2 - y1)),
        )
        self.selected_modules.clear()
        self.selected_wires.clear()

        # Select modules whose bounding rect intersects the selection rect
        for idx, mod in enumerate(self.modules):
            if sel_rect.intersects(self._module_rect(mod)):
                self.selected_modules.add(idx)

        # Select wires whose bounding box or any segment intersects the rect
        for idx, sig in enumerate(self.signals):
            coords = sig.get('coordinates', [])
            selected = False
            # Check if any vertex is inside the rectangle
            for coord in coords:
                if sel_rect.contains(QPoint(int(coord[0]), int(coord[1]))):
                    selected = True
                    break
            # Check if any segment intersects the rectangle (partial overlap)
            if not selected:
                for i in range(len(coords) - 1):
                    if self._segment_intersects_rect(
                        coords[i][0], coords[i][1],
                        coords[i + 1][0], coords[i + 1][1],
                        sel_rect,
                    ):
                        selected = True
                        break
            if selected:
                self.selected_wires.add(idx)

    def mouseDoubleClickEvent(self, event):
        if self.drawing_wire and len(self.current_wire) > 1:
            # Snapshot BEFORE adding the new wire
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

        elif self.mode == "select" and self._drag_type is not None:
            if self._drag_type == 'rubber_band':
                # Update rubber-band rectangle with unsnapped coords
                raw = self._transform_mouse(event)
                self._rubber_band_end = raw
            elif self._drag_type == 'node' and self._drag_wire_idx is not None:
                # Move a single wire node
                self.signals[self._drag_wire_idx]['coordinates'][self._drag_node_idx] = pos
            elif self._drag_type == 'move_selection':
                # Move all selected modules and wires together
                dx = pos[0] - self._move_start_pos[0]
                dy = pos[1] - self._move_start_pos[1]
                if self._move_orig_module_positions:
                    for idx, (ox, oy) in self._move_orig_module_positions.items():
                        self.modules[idx]['x'] = ox + dx
                        self.modules[idx]['y'] = oy + dy
                if self._move_orig_wire_positions:
                    for idx, orig_coords in self._move_orig_wire_positions.items():
                        self.signals[idx]['coordinates'] = [
                            (x + dx, y + dy) for (x, y) in orig_coords
                        ]
            self.update()

    @staticmethod
    def _make_90_degree_mid(last, pos):
        if abs(pos[0] - last[0]) > abs(pos[1] - last[1]):
            return (pos[0], last[1])
        return (last[0], pos[1])
