# Placeholder for file handling and project management

import json

class DataManager:
    def __init__(self):
        print("Data manager initialized.")

    def save_project(self, project_data, filename):
        with open(filename, "w") as f:
            json.dump(project_data, f, indent=2)
        print(f"Project saved to {filename}")

    def load_project(self, filename):
        with open(filename, "r") as f:
            data = json.load(f)
        print(f"Project loaded from {filename}")
        return data
