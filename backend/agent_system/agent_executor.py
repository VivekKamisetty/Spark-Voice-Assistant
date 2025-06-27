from agent_system.agents.filesystem import FileSystemAgent
from agent_system.agents.calendar_agent import CalendarAgent

AGENT_MAP = {
    "filesystem": FileSystemAgent(),
    "calendar": CalendarAgent(),
}

def execute_agent_action(agent_name, payload):
    agent = AGENT_MAP.get(agent_name)
    if not agent:
        return {"status": "error", "message": f"No agent found for '{agent_name}'"}
    try:
        return agent.execute(payload)
    except Exception as e:
        return {"status": "error", "message": str(e)}
