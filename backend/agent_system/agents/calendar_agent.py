from agent_system.agent_base import Agent

class CalendarAgent(Agent):
    def __init__(self):
        super().__init__("calendar")

    def execute(self, payload):
        # Placeholder for real calendar logic
        title = payload.get("title", "Untitled Event")
        time = payload.get("time", "Unknown Time")
        return {"status": "success", "message": f"Event '{title}' scheduled at {time}"}
