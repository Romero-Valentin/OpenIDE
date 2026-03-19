from app_logging.logger import Logger


class SignalManager:
    """Manages the collection of signal wires in the project."""

    def __init__(self, logger: Logger):
        self._logger = logger
        self.signals: list[dict] = []
        self._logger.log_action("SignalManager initialized")

    def add_signal(self, signal: dict):
        self.signals.append(signal)
        self._logger.log_action("add_signal", str(signal))

    def remove_signal(self, index: int):
        if 0 <= index < len(self.signals):
            removed = self.signals.pop(index)
            self._logger.log_action("remove_signal", str(removed))

    def to_list(self) -> list[dict]:
        return self.signals

    def load(self, signals: list[dict]):
        self.signals = signals
        self._logger.log_action("signals_loaded", f"{len(signals)} signals")
