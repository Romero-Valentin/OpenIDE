import sys
from PySide6.QtWidgets import QApplication
from app_logging.logger import Logger
from ui.main_window import MainWindow

if __name__ == "__main__":
    logger = Logger("openide.log")
    logger.log("Application starting")
    qt_app = QApplication(sys.argv)
    window = MainWindow(logger=logger)
    window.show()
    logger.log("Main window displayed")
    qt_app.exec()
    logger.log("Application closed")
