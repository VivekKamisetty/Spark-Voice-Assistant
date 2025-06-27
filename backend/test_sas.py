from agent_system.intent_handler import handle_intent

# TEST PAYLOAD: List contents of your Desktop
test_input = {
    "intent": "list_directory",
    "agent": "filesystem",
    "payload": {
        "action": "list",
        "path": "/Users/kamisettyvivek/Desktop"  # Replace with your actual username path
    },
    "confirmation_required": False
}

result = handle_intent(test_input)
print("RESULT:")
print(result)

