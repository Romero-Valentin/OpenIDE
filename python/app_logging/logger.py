import datetime
import os


class Logger:
    """Application logger — writes timestamped entries to a log file and stdout."""

    def __init__(self, logfile: str = "openide.log"):
        self.logfile = logfile
        os.makedirs(os.path.dirname(logfile) if os.path.dirname(logfile) else ".", exist_ok=True)
        self.log("Logger initialized")

    def log(self, message: str):
        timestamp = datetime.datetime.now().isoformat()
        entry = f"[{timestamp}] {message}"
        with open(self.logfile, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        print(entry)

    def log_action(self, action: str, details: str | None = None):
        msg = f"Action: {action}"
        if details:
            msg += f" | Details: {details}"
        self.log(msg)

    def log_input(self, input_type: str, details: str | None = None):
        msg = f"User input: {input_type}"
        if details:
            msg += f" | Details: {details}"
        self.log(msg)
