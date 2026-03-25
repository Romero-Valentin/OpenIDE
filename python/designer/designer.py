from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon, QFont, QFontMetrics
from PySide6.QtCore import Qt, QRect, QPoint, QTimer, QRectF
from math import hypot
import collections
import copy
import heapq
import time

# ---------------------------------------------------------------------------
# Grid & layout constants
# ---------------------------------------------------------------------------
GRID_VISIBLE = 100          # visible dot grid spacing
GRID_MODULE = 100           # module position snap
GRID_MODULE_SIZE = 100      # module size snap (width / height)
GRID_PORT = 100             # port absolute-position snap
GRID_LABEL = 25             # label offset snap

DEFAULT_MODULE_W = 300
DEFAULT_MODULE_H = 500
DEFAULT_MODULE_COLOR = [0, 200, 0]   # RGB (green)

PORT_MARKER_SIZE = 24
WORLD_FONT_SIZE = 48         # font size in world-coordinate units

# Hit-testing radii (screen pixels — divided by zoom at runtime)
NODE_HIT_RADIUS_PX = 10
SEGMENT_HIT_RADIUS_PX = 12
EDGE_HIT_RADIUS_PX = 8     # module-edge resize handle
LABEL_HIT_PADDING = 4       # extra padding around text bounding boxes

DEFAULT_ZOOM = 0.25         # initial zoom level (farther out than 1.0)


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
        self.selected_modules = set()
        self.selected_wires = set()
        self.selected_ports = set()   # set of (mod_idx, port_idx) tuples

        # Drag state for moving objects
        self._drag_type = None  # 'move_selection','node','rubber_band','resize_edge','move_label','move_port_label','move_port'
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None

        # Rubber-band selection rectangle
        self._rubber_band_start = None
        self._rubber_band_end = None

        # Resize state
        self._resize_mod_idx = None      # module being resized
        self._resize_edge = None         # 'left' | 'right' | 'top' | 'bottom'
        self._resize_start_raw = None    # cursor position at resize start
        self._resize_orig = None         # snapshot of x, y, width, height before resize

        # Label-drag state
        self._label_mod_idx = None       # module index
        self._label_key = None           # 'name_offset' | 'entity_offset'
        self._label_port_idx = None      # port index (for port-label drag)
        self._label_drag_start = None    # raw cursor at drag start
        self._label_orig_offset = None   # original [ox, oy] before drag

        # Port-drag state (repositioning a port to a different edge)
        self._port_drag_mod_idx = None
        self._port_drag_orig = None        # {port_idx: (abs_x, abs_y)} at drag start
        self._port_drag_primary_idx = None # port_idx of the clicked port

        # Viewport state
        self.zoom = DEFAULT_ZOOM
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
        self._active_pan_key = None   # Qt key constant currently held for panning
        self._save_callback = None    # set by MainWindow for Ctrl+S delegation

        # World-coordinate font used for all in-canvas text.
        # Created once and reused by paintEvent and hit-testing methods.
        self._world_font = QFont(self.font())
        self._world_font.setPixelSize(WORLD_FONT_SIZE)
        self._world_fm = QFontMetrics(self._world_font)

        # Optional callback invoked whenever the design state changes.
        # MainWindow sets this to keep the save-icon in sync.
        self.on_design_changed = None

        # Clipboard for copy/paste (deep-copied module/wire dicts)
        self._clipboard = None   # {'modules': [...], 'signals': [...]}

        # Paint-time overlay — toggled via Options dialog
        self.show_fps = False
        self._paint_ms = 0.0             # last paintEvent duration in ms
        self._paint_history: collections.deque[float] = collections.deque(maxlen=100)
        self._paint_p1 = 0.0            # 1 % low  (2nd-worst of last 100)
        self._paint_p01 = 0.0           # 0.1 % low (worst of last 100)

        # Keybindings — set by MainWindow after construction.
        # If None, no keyboard actions are processed.
        self.keybindings = None

    # ------------------------------------------------------------------
    # Undo / Redo
    #
    # Invariant: _save_undo() is ALWAYS called BEFORE the state is mutated.
    # This ensures the snapshot on the stack represents the state the user
    # can return to.  For drag operations the snapshot is taken when the
    # drag begins; if the user releases without actually moving, the
    # snapshot is discarded (_pop_undo_if_unchanged).
    # ------------------------------------------------------------------

    def _notify_design_changed(self):
        """Invoke the external callback (if set) after a state change."""
        if self.on_design_changed:
            self.on_design_changed()

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
        else:
            # The drag actually mutated the design — notify now that the
            # move is finalised (the earlier _save_undo call fired before
            # the position change, so its notification saw clean state).
            self._notify_design_changed()

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
        self.selected_ports.clear()
        self.update()
        self._notify_design_changed()

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
        self.selected_ports.clear()
        self.update()
        self._notify_design_changed()

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _snap_to_grid(pos):
        """Snap (x, y) to nearest multiple of GRID_VISIBLE (for wires)."""
        g = GRID_VISIBLE
        return (round(pos[0] / g) * g, round(pos[1] / g) * g)

    @staticmethod
    def _snap_module(val):
        """Snap a single value to the module grid (GRID_MODULE)."""
        return round(val / GRID_MODULE) * GRID_MODULE

    @staticmethod
    def _snap_module_size(val):
        """Snap a single value to the module-size grid (GRID_MODULE_SIZE)."""
        return round(val / GRID_MODULE_SIZE) * GRID_MODULE_SIZE

    @staticmethod
    def _snap_label(val):
        """Snap a single value to the label grid (GRID_LABEL)."""
        return round(val / GRID_LABEL) * GRID_LABEL

    @staticmethod
    def _valid_port_slots(start, end, grid=GRID_PORT):
        """Return sorted multiples of *grid* strictly inside the open interval (start, end)."""
        first = int((start // grid + 1) * grid)
        slots = []
        v = first
        while v < end:
            slots.append(v)
            v += grid
        return slots

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

    @staticmethod
    def _module_rect(mod) -> QRect:
        """Bounding rectangle (for painting / contains-tests)."""
        return QRect(
            mod.get('x', 0), mod.get('y', 0),
            mod.get('width', DEFAULT_MODULE_W),
            mod.get('height', DEFAULT_MODULE_H),
        )

    @staticmethod
    def _module_edges(mod):
        """Return (left, top, right, bottom) — true edge coordinates.

        Unlike QRect.right() which returns x+w-1, this returns x+w.
        """
        x = mod.get('x', 0)
        y = mod.get('y', 0)
        return x, y, x + mod.get('width', DEFAULT_MODULE_W), y + mod.get('height', DEFAULT_MODULE_H)

    def _compute_port_positions(self, mod):
        """Return a list of (px, py) for every port, in port-list order.

        Each port stores its own 'pos' — the offset along its assigned
        edge, snapped to GRID_PORT.  The absolute position is derived
        from the module origin plus the port's side and pos.
        """
        left, top, right, bottom = self._module_edges(mod)
        positions = []
        for port in mod.get('ports', []):
            side = port.get('side', 'left')
            pos = port.get('pos', GRID_PORT)
            if side == 'left':
                positions.append((left, top + pos))
            elif side == 'right':
                positions.append((right, top + pos))
            elif side == 'top':
                positions.append((left + pos, top))
            else:  # bottom
                positions.append((left + pos, bottom))
        return positions

    def _ensure_port_positions(self, mod):
        """Assign a default 'pos' to every port that does not already have one.

        Uses sequential GRID_PORT slots on each side, avoiding slots
        already occupied by ports that do have an explicit position.
        Called after import and after loading a project for backward
        compatibility with files that pre-date explicit port positions.
        """
        left, top, right, bottom = self._module_edges(mod)
        w = right - left
        h = bottom - top

        sides = {'left': [], 'right': [], 'top': [], 'bottom': []}
        for i, p in enumerate(mod.get('ports', [])):
            sides[p.get('side', 'left')].append(i)

        for side, indices in sides.items():
            slots = self._valid_port_slots(
                0, h if side in ('left', 'right') else w)
            occupied = set()
            needs_pos = []
            for port_idx in indices:
                p = mod['ports'][port_idx]
                if 'pos' in p:
                    occupied.add(p['pos'])
                else:
                    needs_pos.append(port_idx)
            available = [s for s in slots if s not in occupied]
            for i, port_idx in enumerate(needs_pos):
                if i < len(available):
                    mod['ports'][port_idx]['pos'] = available[i]
                elif slots:
                    mod['ports'][port_idx]['pos'] = slots[-1]
                else:
                    mod['ports'][port_idx]['pos'] = GRID_PORT

    def _min_module_size(self, mod):
        """Return (min_w, min_h) ensuring every port keeps a valid slot,
        port-name labels do not overlap, and block names fit."""
        ports = mod.get('ports', [])
        fm = self._world_fm
        inset = GRID_LABEL     # port-label inward offset (25)
        extra_pad = 50         # breathing room

        sides = {'left': [], 'right': [], 'top': [], 'bottom': []}
        for p in ports:
            sides[p.get('side', 'left')].append(p)

        # Port-count constraint: (count + 1) * GRID_PORT per axis
        need_v = max(len(sides['left']), len(sides['right']))
        need_h = max(len(sides['top']), len(sides['bottom']))
        count_min_h = (need_v + 1) * GRID_PORT
        count_min_w = (need_h + 1) * GRID_PORT

        # Position constraint: largest stored pos must stay inside the boundary
        max_v_pos = max(
            (p.get('pos', GRID_PORT) for side_name in ('left', 'right')
             for p in sides[side_name]),
            default=0,
        )
        max_h_pos = max(
            (p.get('pos', GRID_PORT) for side_name in ('top', 'bottom')
             for p in sides[side_name]),
            default=0,
        )
        pos_min_h = (max_v_pos + GRID_PORT) if max_v_pos > 0 else 0
        pos_min_w = (max_h_pos + GRID_PORT) if max_h_pos > 0 else 0

        # Width = longest-left-port-name + longest-right-port-name
        #       + max(entity-name, instance-name) + 50  (extra_pad)
        left_tw = max((fm.horizontalAdvance(p.get('name', ''))
                       for p in sides['left']), default=0)
        right_tw = max((fm.horizontalAdvance(p.get('name', ''))
                        for p in sides['right']), default=0)
        inst_tw = fm.horizontalAdvance(mod.get('name', ''))
        ent_tw = fm.horizontalAdvance(mod.get('entity', ''))
        text_raw_w = (inset + left_tw + inset
                      + max(inst_tw, ent_tw)
                      + inset + right_tw + inset
                      + extra_pad)

        # Text-height constraint: top/bottom port names
        top_th = (fm.height() + inset) if sides['top'] else 0
        bot_th = (fm.height() + inset) if sides['bottom'] else 0
        name_min_h = (top_th + WORLD_FONT_SIZE + bot_th) if (top_th or bot_th) else 0

        # Snap UP so the minimum is never less than the requirement
        g = GRID_MODULE_SIZE
        raw_w = max(count_min_w, pos_min_w, text_raw_w, g)
        raw_h = max(count_min_h, pos_min_h, name_min_h, g)
        min_w = (int(raw_w) + g - 1) // g * g
        min_h = (int(raw_h) + g - 1) // g * g
        return min_w, min_h

    def _hit_test_module(self, raw_pos) -> int | None:
        """Return index of the topmost module whose rect contains *raw_pos*."""
        pt = QPoint(int(raw_pos[0]), int(raw_pos[1]))
        for idx in range(len(self.modules) - 1, -1, -1):
            if self._module_rect(self.modules[idx]).contains(pt):
                return idx
        return None

    def _hit_test_edge(self, raw_pos):
        """Return (module_idx, edge_name) if cursor is on a module edge, else (None, None)."""
        threshold = EDGE_HIT_RADIUS_PX / max(self.zoom, 0.01)
        rx, ry = raw_pos
        for idx in range(len(self.modules) - 1, -1, -1):
            left, top, right, bottom = self._module_edges(self.modules[idx])
            # Quick rejection — must be near the module
            if not (left - threshold <= rx <= right + threshold and
                    top - threshold <= ry <= bottom + threshold):
                continue
            if abs(rx - left) < threshold and top - threshold <= ry <= bottom + threshold:
                return idx, 'left'
            if abs(rx - right) < threshold and top - threshold <= ry <= bottom + threshold:
                return idx, 'right'
            if abs(ry - top) < threshold and left - threshold <= rx <= right + threshold:
                return idx, 'top'
            if abs(ry - bottom) < threshold and left - threshold <= rx <= right + threshold:
                return idx, 'bottom'
        return None, None

    def _hit_test_module_label(self, raw_pos):
        """Return (module_idx, 'name_offset'|'entity_offset') or (None, None)."""
        fm = self._world_fm
        rx, ry = raw_pos
        pad = LABEL_HIT_PADDING / max(self.zoom, 0.01)
        for idx in range(len(self.modules) - 1, -1, -1):
            mod = self.modules[idx]
            rect = self._module_rect(mod)
            # Instance name — default: centred inside block
            name = mod.get('name', '')
            if name:
                off = mod.get('name_offset', [0, 0])
                tw = fm.horizontalAdvance(name)
                th = fm.height()
                cx = rect.center().x() + off[0] - tw / 2
                cy = rect.center().y() + off[1] - th / 2
                if QRectF(cx - pad, cy - pad, tw + 2 * pad, th + 2 * pad).contains(rx, ry):
                    return idx, 'name_offset'
            # Entity name — default: centred above block
            entity = mod.get('entity', '')
            if entity:
                off = mod.get('entity_offset', [0, 0])
                tw = fm.horizontalAdvance(entity)
                th = fm.height()
                cx = rect.center().x() + off[0] - tw / 2
                cy = rect.top() - 4 + off[1] - th
                if QRectF(cx - pad, cy - pad, tw + 2 * pad, th + 2 * pad).contains(rx, ry):
                    return idx, 'entity_offset'
        return None, None

    def _hit_test_port_label(self, raw_pos):
        """Return (module_idx, port_idx) if a port name label is hit, else (None, None)."""
        fm = self._world_fm
        rx, ry = raw_pos
        pad = LABEL_HIT_PADDING / max(self.zoom, 0.01)
        for mod_idx in range(len(self.modules) - 1, -1, -1):
            mod = self.modules[mod_idx]
            positions = self._compute_port_positions(mod)
            rect = self._module_rect(mod)
            for port_idx, port in enumerate(mod.get('ports', [])):
                pname = port.get('name', '')
                if not pname or positions[port_idx] is None:
                    continue
                px, py = positions[port_idx]
                side = port.get('side', 'left')
                loff = port.get('label_offset', [0, 0])
                lx, ly = self._default_port_label_pos(px, py, side, pname, fm)
                lx += loff[0]
                ly += loff[1]
                tw, th = fm.horizontalAdvance(pname), fm.height()
                if QRectF(lx - pad, ly - pad, tw + 2 * pad, th + 2 * pad).contains(rx, ry):
                    return mod_idx, port_idx
        return None, None

    @staticmethod
    def _default_port_label_pos(px, py, side, name, fm):
        """Top-left of the port label text (inside the block by default).

        Labels are offset by GRID_LABEL (25 units) toward the block
        interior for readability.
        """
        tw = fm.horizontalAdvance(name)
        th = fm.height()
        inset = GRID_LABEL  # 25-unit inward offset
        if side == 'left':
            return px + inset, py - th / 2
        elif side == 'right':
            return px - inset - tw, py - th / 2
        elif side == 'top':
            return px - tw / 2, py + inset
        else:  # bottom
            return px - tw / 2, py - inset - th

    def _hit_test_port_marker(self, raw_pos):
        """Return (module_idx, port_idx) if a port marker is hit, else (None, None)."""
        threshold = max(PORT_MARKER_SIZE, NODE_HIT_RADIUS_PX / max(self.zoom, 0.01))
        rx, ry = raw_pos
        for mod_idx in range(len(self.modules) - 1, -1, -1):
            mod = self.modules[mod_idx]
            positions = self._compute_port_positions(mod)
            for port_idx in range(len(mod.get('ports', []))):
                if positions[port_idx] is None:
                    continue
                px, py = positions[port_idx]
                if hypot(rx - px, ry - py) < threshold:
                    return mod_idx, port_idx
        return None, None

    @staticmethod
    def _closest_module_side(mod, rx, ry):
        """Return 'left'|'right'|'top'|'bottom' — the module edge closest to (rx, ry)."""
        x = mod.get('x', 0)
        y = mod.get('y', 0)
        w = mod.get('width', DEFAULT_MODULE_W)
        h = mod.get('height', DEFAULT_MODULE_H)
        hw, hh = w / 2, h / 2
        dx = (rx - (x + hw)) / max(hw, 1)
        dy = (ry - (y + hh)) / max(hh, 1)
        if abs(dx) > abs(dy):
            return 'right' if dx > 0 else 'left'
        return 'bottom' if dy > 0 else 'top'

    def _snap_to_boundary(self, mod, x, y):
        """Return (side, pos) for the closest valid port slot on the module boundary.

        'side' is 'left'|'right'|'top'|'bottom'.  'pos' is the offset
        from the module's top edge (for left/right) or left edge (for
        top/bottom), snapped to GRID_PORT.
        """
        left, top, right, bottom = self._module_edges(mod)
        w = right - left
        h = bottom - top
        side = self._closest_module_side(mod, x, y)
        if side in ('left', 'right'):
            slots = self._valid_port_slots(0, h)
            raw = y - top
        else:
            slots = self._valid_port_slots(0, w)
            raw = x - left
        if not slots:
            return side, GRID_PORT
        best = min(slots, key=lambda s: abs(s - raw))
        return side, best

    # ------------------------------------------------------------------
    # Optimal recenter
    # ------------------------------------------------------------------

    def optimal_recenter(self):
        """Adjust zoom and offset so all modules and wires fit in the viewport."""
        points = []
        for mod in self.modules:
            l, t, r, b = self._module_edges(mod)
            points += [(l, t), (r, b)]
        for sig in self.signals:
            for coord in sig.get('coordinates', []):
                points.append((coord[0], coord[1]))
        if not points:
            return
        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_x = max(p[0] for p in points)
        max_y = max(p[1] for p in points)
        cw, ch = max_x - min_x, max_y - min_y
        margin = 50
        if cw == 0 and ch == 0:
            self.zoom = DEFAULT_ZOOM
            self.offset_x = self.width() / 2 - min_x
            self.offset_y = self.height() / 2 - min_y
        else:
            zx = (self.width() - 2 * margin) / max(cw, 1)
            zy = (self.height() - 2 * margin) / max(ch, 1)
            self.zoom = max(min(zx, zy, 10.0), 0.01)
            cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
            self.offset_x = self.width() / 2 - cx * self.zoom
            self.offset_y = self.height() / 2 - cy * self.zoom
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        t0 = time.perf_counter()

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

        # --- Paint-time overlay (screen-space, bottom-right) ---
        if self.show_fps:
            self._paint_ms = (time.perf_counter() - t0) * 1000
            self._paint_history.append(self._paint_ms)
            top2 = heapq.nlargest(2, self._paint_history)
            self._paint_p01 = top2[0]
            self._paint_p1 = top2[1] if len(top2) > 1 else top2[0]
            text = (f"{self._paint_ms:.1f} ms  |  "
                    f"1%: {self._paint_p1:.1f} ms  |  "
                    f"0.1%: {self._paint_p01:.1f} ms")
            font = painter.font()
            font.setPixelSize(13)
            painter.setFont(font)
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(text) + 12
            th = fm.height() + 6
            rx = self.width() - tw - 6
            ry = self.height() - th - 6
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.drawRoundedRect(rx, ry, tw, th, 4, 4)
            painter.setPen(QColor(0, 255, 0))
            painter.drawText(rx + 6, ry + fm.ascent() + 3, text)

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
        painter.setFont(self._world_font)
        fm = self._world_fm
        for mod_idx, mod in enumerate(self.modules):
            rect = self._module_rect(mod)
            color = mod.get('color', DEFAULT_MODULE_COLOR)
            painter.setBrush(QColor(*color))

            # Highlight selected modules with blue boundary
            if mod_idx in self.selected_modules:
                painter.setPen(QPen(QColor(0, 100, 255), 6))
            else:
                painter.setPen(QPen(Qt.black, 2))
            painter.drawRect(rect)

            # Instance name (centred inside, plus user offset)
            name = mod.get('name', '')
            if name:
                off = mod.get('name_offset', [0, 0])
                painter.setPen(QPen(Qt.black, 1))
                tw = fm.horizontalAdvance(name)
                th = fm.height()
                tx = rect.center().x() + off[0] - tw / 2
                ty = rect.center().y() + off[1] + th / 4
                painter.drawText(int(tx), int(ty), name)

            # Entity name (centred above block, plus user offset)
            entity = mod.get('entity', '')
            if entity:
                off = mod.get('entity_offset', [0, 0])
                painter.setPen(QPen(Qt.black, 1))
                tw = fm.horizontalAdvance(entity)
                tx = rect.center().x() + off[0] - tw / 2
                ty = rect.top() - 4 + off[1]
                painter.drawText(int(tx), int(ty), entity)

            # Draw ports
            positions = self._compute_port_positions(mod)
            for port_idx, port in enumerate(mod.get('ports', [])):
                if positions[port_idx] is None:
                    continue
                px, py = positions[port_idx]
                side = port.get('side', 'left')
                direction = port.get('direction', 'in')
                pname = port.get('name', '')

                # Port marker (highlighted when selected)
                is_port_selected = (mod_idx, port_idx) in self.selected_ports
                if is_port_selected:
                    painter.setPen(QPen(QColor(0, 200, 255), 3))
                    painter.setBrush(QColor(0, 200, 255))
                else:
                    painter.setPen(QPen(QColor(0, 0, 255), 2))
                    painter.setBrush(QColor(0, 0, 255))
                if direction in ('in', 'out'):
                    self._draw_arrow_port(painter, px, py, side, direction, PORT_MARKER_SIZE)
                else:
                    self._draw_diamond_port(painter, px, py, side, PORT_MARKER_SIZE)

                # Port label (inside block by default, plus user offset)
                if pname:
                    loff = port.get('label_offset', [0, 0])
                    lx, ly = self._default_port_label_pos(px, py, side, pname, fm)
                    lx += loff[0]
                    ly += loff[1]
                    if is_port_selected:
                        painter.setPen(QPen(QColor(0, 200, 255), 1))
                    else:
                        painter.setPen(QPen(Qt.black, 1))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawText(int(lx), int(ly + fm.ascent()), pname)

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
        """Draw a diamond marker for inout/buffer ports, touching the module edge."""
        hs = s // 2
        if side in ('left', 'right'):
            cx = px - hs if side == 'left' else px + hs
            painter.drawPolygon(QPolygon([
                QPoint(cx, py - hs),
                QPoint(cx - hs, py),
                QPoint(cx, py + hs),
                QPoint(cx + hs, py),
            ]))
        else:
            cy = py - hs if side == 'top' else py + hs
            painter.drawPolygon(QPolygon([
                QPoint(px - hs, cy),
                QPoint(px, cy - hs),
                QPoint(px + hs, cy),
                QPoint(px, cy + hs),
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
        # Multiplicative zoom: each scroll step scales by a fixed ratio so
        # the zoom feels equally progressive at every magnification level.
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.zoom = max(min(self.zoom * factor, 10.0), 0.01)

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
        if self.keybindings is None:
            return
        action = self.keybindings.action_for_event(
            event.key(), event.modifiers())

        if action == 'cancel':
            self._cancel_action()
            return
        if action == 'undo':
            self.undo()
            return
        if action == 'redo':
            self.redo()
            return
        if action == 'copy':
            self._copy_selected()
            return
        if action == 'paste':
            self._paste_clipboard()
            return
        if action == 'delete':
            self._delete_selected()
            return
        if action == 'save':
            # Delegate to MainWindow via callback
            if self._save_callback:
                self._save_callback()
            return

        # Pan keys — smooth, hold-to-move behaviour
        pan_actions = {
            'pan_left': 'left', 'pan_right': 'right',
            'pan_up': 'up', 'pan_down': 'down',
        }
        if action in pan_actions:
            if event.isAutoRepeat():
                return
            self._move_direction = pan_actions[action]
            self._active_pan_key = event.key()
            self._move_timer.start()
            return

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        # Stop panning when the key that started the pan is released
        if (hasattr(self, '_active_pan_key')
                and event.key() == self._active_pan_key):
            self._move_timer.stop()
            self._move_direction = None
            self._active_pan_key = None

    def _cancel_action(self):
        self.drawing_wire = False
        self.current_wire = []
        self.selected_modules.clear()
        self.selected_wires.clear()
        self.selected_ports.clear()
        self._drag_type = None
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None
        self._rubber_band_start = None
        self._rubber_band_end = None
        self._resize_mod_idx = None
        self._resize_edge = None
        self._resize_start_raw = None
        self._resize_orig = None
        self._label_mod_idx = None
        self._label_key = None
        self._label_port_idx = None
        self._label_drag_start = None
        self._label_orig_offset = None
        self._port_drag_mod_idx = None
        self._port_drag_orig = None
        self._port_drag_primary_idx = None
        self.update()

    # ------------------------------------------------------------------
    # Copy / Paste
    # ------------------------------------------------------------------

    def _next_name_for(self, base: str) -> str:
        """Return *base_N* with N being the lowest unused integer.

        Strips any existing ``_N`` numeric suffix from *base* first so
        that copying a copy doesn't stack suffixes (e.g. ``U_0_1_2``).
        """
        # Strip trailing _N if present
        parts = base.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            stem = parts[0]
        else:
            stem = base

        used: set[int] = set()
        for mod in self.modules:
            name = mod.get('name', '')
            if name.startswith(stem + '_'):
                suffix = name[len(stem) + 1:]
                if suffix.isdigit():
                    used.add(int(suffix))
        n = 0
        while n in used:
            n += 1
        return f'{stem}_{n}'

    def _copy_selected(self):
        """Copy every selected module and wire into the internal clipboard."""
        if not self.selected_modules and not self.selected_wires:
            return
        self._clipboard = {
            'modules': [copy.deepcopy(self.modules[i])
                        for i in sorted(self.selected_modules)
                        if 0 <= i < len(self.modules)],
            'signals': [copy.deepcopy(self.signals[i])
                        for i in sorted(self.selected_wires)
                        if 0 <= i < len(self.signals)],
        }

    def _paste_clipboard(self):
        """Paste the clipboard contents centred on the current viewport.

        Duplicated modules receive unique names via ``_next_name_for``.
        All pasted objects become the new selection.
        """
        if not self._clipboard:
            return
        mods = copy.deepcopy(self._clipboard['modules'])
        sigs = copy.deepcopy(self._clipboard['signals'])
        if not mods and not sigs:
            return

        # --- compute bounding box of copied objects ----------------------
        all_xs: list[int] = []
        all_ys: list[int] = []
        for m in mods:
            mx, my = m.get('x', 0), m.get('y', 0)
            all_xs += [mx, mx + m.get('width', DEFAULT_MODULE_W)]
            all_ys += [my, my + m.get('height', DEFAULT_MODULE_H)]
        for s in sigs:
            for pt in s.get('coordinates', []):
                all_xs.append(pt[0] if isinstance(pt, (list, tuple)) else pt)
                all_ys.append(pt[1] if isinstance(pt, (list, tuple)) else pt)
        if not all_xs or not all_ys:
            return

        bbox_cx = (min(all_xs) + max(all_xs)) / 2
        bbox_cy = (min(all_ys) + max(all_ys)) / 2

        # --- viewport centre (snapped to module grid) --------------------
        left, top, right, bottom = self._visible_rect()
        view_cx = self._snap_module((left + right) / 2)
        view_cy = self._snap_module((top + bottom) / 2)

        dx = int(view_cx - bbox_cx)
        dy = int(view_cy - bbox_cy)

        # --- apply offset & assign unique names --------------------------
        self._save_undo()

        new_mod_start = len(self.modules)
        for m in mods:
            m['x'] = m.get('x', 0) + dx
            m['y'] = m.get('y', 0) + dy
            m['name'] = self._next_name_for(m.get('name', 'U_0'))
            self.modules.append(m)

        new_sig_start = len(self.signals)
        for s in sigs:
            s['coordinates'] = [
                (pt[0] + dx, pt[1] + dy)
                for pt in s.get('coordinates', [])
            ]
            self.signals.append(s)

        # --- select only the pasted objects ------------------------------
        self.selected_modules = set(range(new_mod_start, len(self.modules)))
        self.selected_wires = set(range(new_sig_start, len(self.signals)))
        self.selected_ports.clear()

        self._notify_design_changed()
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
        self.selected_ports.clear()
        self._drag_type = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._notify_design_changed()
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

        Hit-testing priority (most specific first):
          1. Port markers  (select / reposition port)
          2. Module edges  (resize)
          3. Port labels   (move port-name text)
          4. Module labels (instance name / entity name)
          5. Wire nodes
          6. Wire segments
          7. Module body   (move)
          8. Empty space   (rubber-band)
        """
        rx, ry = raw_pos

        # --- 1. Port markers ---
        pm_mod, pm_port = self._hit_test_port_marker(raw_pos)
        if pm_mod is not None:
            key = (pm_mod, pm_port)
            if ctrl:
                if key in self.selected_ports:
                    self.selected_ports.discard(key)
                else:
                    self.selected_ports.add(key)
                self._drag_type = None
            else:
                if key not in self.selected_ports:
                    self.selected_modules.clear()
                    self.selected_wires.clear()
                    self.selected_ports.clear()
                    self.selected_ports.add(key)
                self._save_undo()
                self._drag_type = 'move_port'
                self._port_drag_mod_idx = pm_mod
                # Record original absolute positions for delta computation
                positions = self._compute_port_positions(self.modules[pm_mod])
                self._port_drag_orig = {}
                for midx, pidx in self.selected_ports:
                    if midx == pm_mod and pidx < len(positions):
                        self._port_drag_orig[pidx] = positions[pidx]
                self._port_drag_primary_idx = pm_port
            self.update()
            return

        # Non-CTRL click outside a port marker clears port selection
        if not ctrl:
            self.selected_ports.clear()

        # --- 2. Module edges (resize) ---
        edge_mod, edge_name = self._hit_test_edge(raw_pos)
        if edge_mod is not None:
            mod = self.modules[edge_mod]
            self._save_undo()
            self._drag_type = 'resize_edge'
            self._resize_mod_idx = edge_mod
            self._resize_edge = edge_name
            self._resize_start_raw = raw_pos
            self._resize_orig = {
                'x': mod['x'], 'y': mod['y'],
                'width': mod.get('width', DEFAULT_MODULE_W),
                'height': mod.get('height', DEFAULT_MODULE_H),
            }
            self.update()
            return

        # --- 3. Port label ---
        pl_mod, pl_port = self._hit_test_port_label(raw_pos)
        if pl_mod is not None:
            port = self.modules[pl_mod]['ports'][pl_port]
            self._save_undo()
            self._drag_type = 'move_port_label'
            self._label_mod_idx = pl_mod
            self._label_port_idx = pl_port
            self._label_drag_start = raw_pos
            self._label_orig_offset = list(port.get('label_offset', [0, 0]))
            self.update()
            return

        # --- 4. Module labels ---
        ml_mod, ml_key = self._hit_test_module_label(raw_pos)
        if ml_mod is not None:
            self._save_undo()
            self._drag_type = 'move_label'
            self._label_mod_idx = ml_mod
            self._label_key = ml_key
            self._label_drag_start = raw_pos
            self._label_orig_offset = list(self.modules[ml_mod].get(ml_key, [0, 0]))
            self.update()
            return

        # --- 5 & 6. Wire nodes / segments ---
        hit_wire = None
        hit_node = None

        node_threshold = NODE_HIT_RADIUS_PX / max(self.zoom, 0.01)
        best_node_dist = node_threshold
        for idx, sig in enumerate(self.signals):
            for nidx, node in enumerate(sig.get('coordinates', [])):
                dist = hypot(rx - node[0], ry - node[1])
                if dist < best_node_dist:
                    best_node_dist = dist
                    hit_wire = idx
                    hit_node = nidx

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

        if hit_wire is None and hit_seg_wire is not None:
            hit_wire = hit_seg_wire
            hit_node = None

        # --- 6. Module body ---
        hit_module = self._hit_test_module(raw_pos)

        # --- Decide action ---
        if hit_wire is not None and hit_node is not None:
            if ctrl:
                if hit_wire in self.selected_wires:
                    self.selected_wires.discard(hit_wire)
                else:
                    self.selected_wires.add(hit_wire)
                self._drag_type = None
            else:
                self.selected_modules.clear()
                self.selected_wires.clear()
                self.selected_wires.add(hit_wire)
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
                if hit_wire in self.selected_wires:
                    self.selected_wires.discard(hit_wire)
                else:
                    self.selected_wires.add(hit_wire)
                self._drag_type = None
            else:
                if hit_wire not in self.selected_wires:
                    self.selected_modules.clear()
                    self.selected_wires.clear()
                    self.selected_wires.add(hit_wire)
                self._save_undo()
                self._drag_type = 'move_selection'
                self._move_start_pos = pos
                self._save_move_origins()

        elif hit_module is not None:
            if ctrl:
                if hit_module in self.selected_modules:
                    self.selected_modules.discard(hit_module)
                else:
                    self.selected_modules.add(hit_module)
                self._drag_type = None
            else:
                if hit_module not in self.selected_modules:
                    self.selected_modules.clear()
                    self.selected_wires.clear()
                    self.selected_modules.add(hit_module)
                self._save_undo()
                self._drag_type = 'move_selection'
                self._move_start_pos = pos
                self._save_move_origins()

        else:
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
            self._finalize_rubber_band()
        elif self._drag_type in ('move_selection', 'node', 'resize_edge',
                                  'move_label', 'move_port_label', 'move_port'):
            self._pop_undo_if_unchanged()

        # Reset all drag state (keep selection)
        self._drag_type = None
        self._drag_wire_idx = None
        self._drag_node_idx = None
        self._move_start_pos = None
        self._move_orig_coords = None
        self._move_orig_module_positions = None
        self._move_orig_wire_positions = None
        self._rubber_band_start = None
        self._rubber_band_end = None
        self._resize_mod_idx = None
        self._resize_edge = None
        self._resize_start_raw = None
        self._resize_orig = None
        self._label_mod_idx = None
        self._label_key = None
        self._label_port_idx = None
        self._label_drag_start = None
        self._label_orig_offset = None
        self._port_drag_mod_idx = None
        self._port_drag_orig = None
        self._port_drag_primary_idx = None
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
        self.selected_ports.clear()

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

        # Select ports whose markers fall inside the selection rect
        for mod_idx, mod in enumerate(self.modules):
            positions = self._compute_port_positions(mod)
            for port_idx in range(len(mod.get('ports', []))):
                if positions[port_idx] is None:
                    continue
                px, py = positions[port_idx]
                if sel_rect.contains(QPoint(int(px), int(py))):
                    self.selected_ports.add((mod_idx, port_idx))

    def mouseDoubleClickEvent(self, event):
        if self.drawing_wire and len(self.current_wire) > 1:
            # Snapshot BEFORE adding the new wire
            self._save_undo()
            self.drawing_wire = False
            self.signals.append({'coordinates': self.current_wire})
            self.current_wire = []
            self._notify_design_changed()
            self.update()

    def mouseMoveEvent(self, event):
        raw_pos = self._transform_mouse(event)
        pos = self._snap_to_grid(raw_pos)

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
                self._rubber_band_end = raw_pos

            elif self._drag_type == 'node' and self._drag_wire_idx is not None:
                self.signals[self._drag_wire_idx]['coordinates'][self._drag_node_idx] = pos

            elif self._drag_type == 'move_selection':
                dx = pos[0] - self._move_start_pos[0]
                dy = pos[1] - self._move_start_pos[1]
                # Snap the movement delta to GRID_MODULE for module positions
                snap_dx = self._snap_module(dx)
                snap_dy = self._snap_module(dy)
                if self._move_orig_module_positions:
                    for idx, (ox, oy) in self._move_orig_module_positions.items():
                        self.modules[idx]['x'] = ox + snap_dx
                        self.modules[idx]['y'] = oy + snap_dy
                if self._move_orig_wire_positions:
                    for idx, orig_coords in self._move_orig_wire_positions.items():
                        self.signals[idx]['coordinates'] = [
                            (x + dx, y + dy) for (x, y) in orig_coords
                        ]

            elif self._drag_type == 'resize_edge':
                self._apply_resize(raw_pos)

            elif self._drag_type == 'move_label':
                self._apply_label_drag(raw_pos)

            elif self._drag_type == 'move_port_label':
                self._apply_port_label_drag(raw_pos)

            elif self._drag_type == 'move_port':
                self._apply_port_reposition(raw_pos)

            self.update()

    # ------------------------------------------------------------------
    # Resize helpers
    # ------------------------------------------------------------------

    def _apply_resize(self, raw_pos):
        """Resize the module edge being dragged, clamped to min size.

        Width and height snap to GRID_MODULE_SIZE (100-unit grid).
        """
        mod = self.modules[self._resize_mod_idx]
        orig = self._resize_orig
        min_w, min_h = self._min_module_size(mod)
        rx, ry = raw_pos

        if self._resize_edge == 'right':
            new_w = self._snap_module_size(rx - orig['x'])
            mod['width'] = max(new_w, min_w)
        elif self._resize_edge == 'left':
            right = orig['x'] + orig['width']
            new_w = max(self._snap_module_size(right - rx), min_w)
            mod['width'] = new_w
            mod['x'] = right - new_w
        elif self._resize_edge == 'bottom':
            new_h = self._snap_module_size(ry - orig['y'])
            mod['height'] = max(new_h, min_h)
        elif self._resize_edge == 'top':
            bottom = orig['y'] + orig['height']
            new_h = max(self._snap_module_size(bottom - ry), min_h)
            mod['height'] = new_h
            mod['y'] = bottom - new_h

    # ------------------------------------------------------------------
    # Label drag helpers
    # ------------------------------------------------------------------

    def _apply_label_drag(self, raw_pos):
        """Update the module label offset based on cursor movement.

        The offset snaps to GRID_LABEL (25-unit grid).
        """
        dx = raw_pos[0] - self._label_drag_start[0]
        dy = raw_pos[1] - self._label_drag_start[1]
        self.modules[self._label_mod_idx][self._label_key] = [
            self._snap_label(self._label_orig_offset[0] + dx),
            self._snap_label(self._label_orig_offset[1] + dy),
        ]

    def _apply_port_label_drag(self, raw_pos):
        """Update the port label offset based on cursor movement.

        The offset snaps to GRID_LABEL (25-unit grid).
        """
        dx = raw_pos[0] - self._label_drag_start[0]
        dy = raw_pos[1] - self._label_drag_start[1]
        port = self.modules[self._label_mod_idx]['ports'][self._label_port_idx]
        port['label_offset'] = [
            self._snap_label(self._label_orig_offset[0] + dx),
            self._snap_label(self._label_orig_offset[1] + dy),
        ]

    def _apply_port_reposition(self, raw_pos):
        """Move selected ports to the boundary position under the cursor.

        Each port independently snaps to the closest valid GRID_PORT
        slot on the module boundary.  When multiple ports are selected,
        their relative spacing is preserved by applying the same delta
        computed from the primary (clicked) port's movement.

        The move is rejected entirely if any proposed position collides
        with a stationary port or with another proposed position.
        """
        if not self._port_drag_orig:
            return
        mod = self.modules[self._port_drag_mod_idx]
        moving = set(self._port_drag_orig.keys())
        primary = self._port_drag_primary_idx

        # Where would the primary port go?
        target_side, target_pos = self._snap_to_boundary(
            mod, raw_pos[0], raw_pos[1])
        left, top, right, bottom = self._module_edges(mod)

        # Compute absolute target position of the primary port
        if target_side in ('left', 'right'):
            tgt_x = left if target_side == 'left' else right
            tgt_y = top + target_pos
        else:
            tgt_x = left + target_pos
            tgt_y = top if target_side == 'top' else bottom

        delta_x = tgt_x - self._port_drag_orig[primary][0]
        delta_y = tgt_y - self._port_drag_orig[primary][1]

        # Compute proposed (side, pos) for every moving port
        proposals = {}
        for pidx in moving:
            ox, oy = self._port_drag_orig[pidx]
            new_side, new_pos = self._snap_to_boundary(
                mod, ox + delta_x, oy + delta_y)
            proposals[pidx] = (new_side, new_pos)

        # Collision check: no proposed slot may overlap a stationary port
        # or another proposed slot.
        occupied = set()
        for i, p in enumerate(mod['ports']):
            if i not in moving:
                occupied.add((p.get('side', 'left'), p.get('pos', GRID_PORT)))

        for pidx, (new_side, new_pos) in proposals.items():
            if (new_side, new_pos) in occupied:
                return  # collision — abort entire move
            occupied.add((new_side, new_pos))

        # All clear — apply
        for pidx, (new_side, new_pos) in proposals.items():
            mod['ports'][pidx]['side'] = new_side
            mod['ports'][pidx]['pos'] = new_pos

    @staticmethod
    def _make_90_degree_mid(last, pos):
        if abs(pos[0] - last[0]) > abs(pos[1] - last[1]):
            return (pos[0], last[1])
        return (last[0], pos[1])
