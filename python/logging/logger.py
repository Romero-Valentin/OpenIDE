# Placeholder for logging utilities
import datetime


class Logger:
    def __init__(self, logfile="openide.log"):
        self.logfile = logfile
        self.log("Logger initialized.")

    def log(self, message):
        timestamp = datetime.datetime.now().isoformat()
        entry = f"[{timestamp}] {message}\n"
        with open(self.logfile, "a") as f:
            f.write(entry)
        print(entry.strip())

    def log_action(self, action, details=None):
        msg = f"Action: {action}"
        if details:
            msg += f" | Details: {details}"
        self.log(msg)
