from agent_system.intent_handler import handle_intent

# TEST PAYLOAD: Create a fake calendar event
test_input = {
    "intent": "create_event",
    "agent": "calendar",
    "payload": {
        "title": "Team Sync",
        "time": "2025-06-12T14:00:00"
    },
    "confirmation_required": False
}

result = handle_intent(test_input)
print("RESULT:")
print(result)

