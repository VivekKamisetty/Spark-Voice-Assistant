import os
from agent_system.agent_base import Agent

class FileSystemAgent(Agent):
    def __init__(self):
        super().__init__("filesystem")

    def execute(self, payload):
        action = payload.get("action")
        path = payload.get("path")

        if action == "list":
            if not os.path.isdir(path):
                return {"status": "error", "message": "Invalid directory path"}
            return {"status": "success", "content": os.listdir(path)}

        return {"status": "error", "message": "Unknown action"}
