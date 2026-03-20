"""Reusable toast notification widget.

A small overlay that appears in the top-right corner of its parent widget,
displays a message, and automatically fades out after a configurable duration.

Intended for brief, non-blocking feedback such as:
    - "Saved"
    - "Export complete"
    - "Generation successful"
    - Error / warning messages

Usage:
    from ui.toast import ToastNotification
    toast = ToastNotification(parent_widget)
    toast.show_message("Saved")                          # default info style, 1s
    toast.show_message("Error!", style="error", duration_ms=2000)
"""

from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve


# -----------------------------------------------------------------
# Style presets: (background, text-colour, border)
# -----------------------------------------------------------------
_STYLES = {
    "info":    ("#323232", "#ffffff", "#555555"),
    "success": ("#2e7d32", "#ffffff", "#4caf50"),
    "warning": ("#f57f17", "#000000", "#fbc02d"),
    "error":   ("#c62828", "#ffffff", "#ef5350"),
}

# Margins from the top-right corner of the parent
_MARGIN_RIGHT = 16
_MARGIN_TOP = 16


class ToastNotification(QLabel):
    """Self-positioning, auto-hiding overlay label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # No special window flags — stays as a child widget so that
        # move() works in parent-local coordinates.
        self.setAlignment(Qt.AlignCenter)
        self.hide()

        # Opacity effect for fade-out animation
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

        # Timer that triggers the fade-out when the display duration elapses
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._start_fade_out)

        # Fade-out animation
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_anim.setDuration(300)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InQuad)
        self._fade_anim.finished.connect(self.hide)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_message(self, text: str, *, style: str = "info", duration_ms: int = 1000):
        """Display *text* in the top-right corner of the parent.

        Parameters
        ----------
        text : str
            The message to display.
        style : str
            One of "info", "success", "warning", "error".
        duration_ms : int
            How long the toast stays fully visible before fading out.
        """
        bg, fg, border = _STYLES.get(style, _STYLES["info"])
        self.setStyleSheet(
            f"QLabel {{"
            f"  background-color: {bg};"
            f"  color: {fg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"  padding: 8px 18px;"
            f"  font-size: 13px;"
            f"  font-weight: bold;"
            f"}}"
        )
        self.setText(text)
        self.adjustSize()

        # Reset opacity and stop any running animation
        self._fade_anim.stop()
        self._opacity.setOpacity(1.0)

        # Position at the top-right corner of the parent
        self._reposition()
        self.show()
        self.raise_()

        # Start the dismiss countdown
        self._dismiss_timer.start(duration_ms)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reposition(self):
        """Place the toast in the top-right corner of the parent widget."""
        parent = self.parent()
        if parent is None:
            return
        x = parent.width() - self.width() - _MARGIN_RIGHT
        y = _MARGIN_TOP
        self.move(x, y)

    def _start_fade_out(self):
        """Begin the fade-out animation."""
        self._fade_anim.start()
