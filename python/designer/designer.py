# Placeholder for designer logic
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, QRect, QTimer

class DesignerWidget(QWidget):
    mode = "draw"  # Modes: 'draw', 'select'
    selected_wire = None
    selected_node = None
    def _distance_to_segment(self, px, py, x1, y1, x2, y2):
        # Return minimum distance from point (px,py) to segment (x1,y1)-(x2,y2)
        from math import hypot
        if (x1, y1) == (x2, y2):
            return hypot(px-x1, py-y1)
        dx, dy = x2-x1, y2-y1
        t = max(0, min(1, ((px-x1)*dx + (py-y1)*dy) / (dx*dx + dy*dy)))
        proj_x = x1 + t*dx
        proj_y = y1 + t*dy
        return hypot(px-proj_x, py-proj_y)

    def _save_undo(self):
        # Save current state for undo
        import copy
        if not hasattr(self, '_undo_stack'):
            self._undo_stack = []
        self._undo_stack.append((copy.deepcopy(self.modules), copy.deepcopy(self.signals)))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def undo(self):
        if hasattr(self, '_undo_stack') and self._undo_stack:
            self.modules, self.signals = self._undo_stack.pop()
            self.update()

    def _snap_to_grid(self, pos):
        # Snap position to nearest multiple of 100
        x = round(pos[0] / 100) * 100
        y = round(pos[1] / 100) * 100
        return (x, y)
    def __init__(self):
        super().__init__()
        self.setMinimumSize(800, 600)
        self.modules = []
        self.signals = []
        self.drawing_wire = False
        self.current_wire = []
        self.start_wire_pos = None
        self.setMouseTracking(True)
        self.zoom = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.setFocusPolicy(Qt.StrongFocus)
        self._move_timer = QTimer()
        self._move_timer.setInterval(16)  # ~60 FPS
        self._move_timer.timeout.connect(self._move_workspace)
        self._move_direction = None
        self._move_step = 20  # Slower step for smoother control

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.zoom, self.zoom)
        # Draw grid points (multiples of 100)
        grid_color = QColor(180, 180, 180)
        painter.setPen(QPen(grid_color, 1))
        for gx in range(-2000, 2000, 100):
            for gy in range(-2000, 2000, 100):
                painter.drawEllipse(gx-2, gy-2, 4, 4)
        # Draw modules as green blocks
        for idx, mod in enumerate(self.modules):
            rect = QRect(100 + idx*180, 100, 140, 60)
            painter.setBrush(QColor(0, 200, 0))
            painter.setPen(QPen(Qt.black, 2))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignCenter, mod['name'])
            port_y = rect.top() + 15
            for pidx, port in enumerate(mod['ports']):
                if pidx % 2 == 0:
                    painter.setPen(QPen(QColor(0, 0, 255), 2))
                    painter.drawLine(rect.left()-20, port_y, rect.left(), port_y)
                    from PySide6.QtCore import QPoint
                    points = [
                        QPoint(rect.left()-20, port_y-5),
                        QPoint(rect.left()-20, port_y+5),
                        QPoint(rect.left(), port_y)
                    ]
                    painter.drawPolygon(points)
                    painter.setPen(QPen(Qt.black, 1))
                    painter.drawText(rect.left()-60, port_y+5, port)
                else:
                    painter.setPen(QPen(QColor(0, 0, 255), 2))
                    painter.drawLine(rect.right(), port_y, rect.right()+20, port_y)
                    from PySide6.QtCore import QPoint
                    points = [
                        QPoint(rect.right()+20, port_y-5),
                        QPoint(rect.right()+20, port_y+5),
                        QPoint(rect.right(), port_y)
                    ]
                    painter.drawPolygon(points)
                    painter.setPen(QPen(Qt.black, 1))
                    painter.drawText(rect.right()+25, port_y+5, port)
                port_y += 15
        for sig in self.signals:
            painter.setPen(QPen(QColor(0, 0, 255), 3))
            coords = sig.get('coordinates', [])
            if len(coords) > 1:
                for i in range(len(coords)-1):
                    painter.drawLine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        if self.drawing_wire and len(self.current_wire) > 1:
            painter.setPen(QPen(QColor(0, 200, 200), 2, Qt.DashLine))
            for i in range(len(self.current_wire)-1):
                painter.drawLine(self.current_wire[i][0], self.current_wire[i][1], self.current_wire[i+1][0], self.current_wire[i+1][1])
        painter.restore()

    def wheelEvent(self, event):
        # Zoom in/out with mouse wheel
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom = min(self.zoom + 0.1, 3.0)
        else:
            self.zoom = max(self.zoom - 0.1, 0.3)
        self.update()

    def keyPressEvent(self, event):
        # Start smooth movement with arrow keys
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key_Left:
            self._move_direction = 'left'
            self._move_timer.start()
        elif event.key() == Qt.Key_Right:
            self._move_direction = 'right'
            self._move_timer.start()
        elif event.key() == Qt.Key_Up:
            self._move_direction = 'up'
            self._move_timer.start()
        elif event.key() == Qt.Key_Down:
            self._move_direction = 'down'
            self._move_timer.start()

    def keyReleaseEvent(self, event):
        # Stop movement when arrow key is released
        if event.isAutoRepeat():
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._move_timer.stop()
            self._move_direction = None

    def _move_workspace(self):
        # Move workspace in the current direction
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
        # Convert mouse position to workspace coordinates
        x = (event.x() - self.offset_x) / self.zoom
        y = (event.y() - self.offset_y) / self.zoom
        return (int(x), int(y))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = self._transform_mouse(event)
            pos = self._snap_to_grid(pos)
            if self.mode == "draw":
                if not self.drawing_wire:
                    self.drawing_wire = True
                    self.current_wire = [pos]
                else:
                    last = self.current_wire[-1]
                    if abs(pos[0] - last[0]) > abs(pos[1] - last[1]):
                        mid = (pos[0], last[1])
                    else:
                        mid = (last[0], pos[1])
                    mid = self._snap_to_grid(mid)
                    pos = self._snap_to_grid(pos)
                    self.current_wire.append(mid)
                    self.current_wire.append(pos)
                self.update()
            elif self.mode == "select":
                # Select node or wire
                self.selected_wire = None
                self.selected_node = None
                min_dist = 15
                for idx, sig in enumerate(self.signals):
                    for nidx, node in enumerate(sig.get('coordinates', [])):
                        dist = ((pos[0] - node[0])**2 + (pos[1] - node[1])**2)**0.5
                        if dist < min_dist:
                            min_dist = dist
                            self.selected_wire = idx
                            self.selected_node = nidx
                if self.selected_wire is None:
                    # Select closest wire segment
                    min_dist = 15
                    for idx, sig in enumerate(self.signals):
                        coords = sig.get('coordinates', [])
                        for i in range(len(coords)-1):
                            d = self._distance_to_segment(pos[0], pos[1], coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
                            if d < min_dist:
                                min_dist = d
                                self.selected_wire = idx
                                self.selected_node = None
                # Start move
                self._move_start_pos = pos
                self._move_orig_coords = None
                if self.selected_wire is not None:
                    if self.selected_node is not None:
                        self._move_orig_coords = self.signals[self.selected_wire]['coordinates'][self.selected_node]
                    else:
                        self._move_orig_coords = list(self.signals[self.selected_wire]['coordinates'])
                self.update()

    def mouseReleaseEvent(self, event):
        # Finalize move for node or wire
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
    def keyPressEvent(self, event):
        # Escape cancels any action
        if event.key() == Qt.Key_Escape:
            self.drawing_wire = False
            self.current_wire = []
            self.selected_wire = None
            self.selected_node = None
            self._move_start_pos = None
            self._move_orig_coords = None
            self.update()
            return
        # Ctrl+Z undo
        if event.key() == Qt.Key_Z and (event.modifiers() & Qt.ControlModifier):
            self.undo()
            return
        # SUPPR/Delete key
        if event.key() in (Qt.Key_Delete, Qt.Key_Super_L, Qt.Key_Super_R):
            if self.mode == "select" and self.selected_wire is not None:
                self._save_undo()
                del self.signals[self.selected_wire]
                self.selected_wire = None
                self.selected_node = None
                self._move_start_pos = None
                self._move_orig_coords = None
                self.update()
            return
        # Existing movement logic
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key_Left:
            self._move_direction = 'left'
            self._move_timer.start()
        elif event.key() == Qt.Key_Right:
            self._move_direction = 'right'
            self._move_timer.start()
        elif event.key() == Qt.Key_Up:
            self._move_direction = 'up'
            self._move_timer.start()
        elif event.key() == Qt.Key_Down:
            self._move_direction = 'down'
            self._move_timer.start()

    def mouseMoveEvent(self, event):
        pos = self._transform_mouse(event)
        pos = self._snap_to_grid(pos)
        if self.mode == "draw" and self.drawing_wire and self.current_wire:
            last = self.current_wire[-1]
            preview_wire = self.current_wire.copy()
            if abs(pos[0] - last[0]) > abs(pos[1] - last[1]):
                mid = (pos[0], last[1])
            else:
                mid = (last[0], pos[1])
            mid = self._snap_to_grid(mid)
            preview_wire.append(mid)
            preview_wire.append(pos)
            self.current_wire = self.current_wire[:-2] if len(self.current_wire) > 2 else self.current_wire
            self.current_wire += [mid, pos]
            self.update()
        elif self.mode == "select" and self.selected_wire is not None and self._move_start_pos is not None:
            if self.selected_node is not None:
                # Move node
                self.signals[self.selected_wire]['coordinates'][self.selected_node] = pos
            else:
                # Move whole wire
                orig_coords = self._move_orig_coords
                dx = pos[0] - self._move_start_pos[0]
                dy = pos[1] - self._move_start_pos[1]
                self.signals[self.selected_wire]['coordinates'] = [(x+dx, y+dy) for (x, y) in orig_coords]
            self.update()
