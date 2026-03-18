import sys
from ui.main_window import MainWindow

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    qt_app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    qt_app.exec()
