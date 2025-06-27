from agent_system.agent_executor import execute_agent_action

def handle_intent(gpt_response_json):
    if gpt_response_json.get("confirmation_required", True):
        return {"status": "pending", "message": "Confirmation required"}
    
    agent_name = gpt_response_json.get("agent")
    payload = gpt_response_json.get("payload", {})
    return execute_agent_action(agent_name, payload)
