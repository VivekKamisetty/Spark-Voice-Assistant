import json
import os

def write_status(status, text="", show_popup=False, agent_result=None):
    public_path = os.path.join(os.path.dirname(__file__), '..', 'public')
    output_file = os.path.join(public_path, 'spark_output.json')

    os.makedirs(public_path, exist_ok=True)

    data = {
        "status": status,
        "text": text,
        "show_popup": show_popup
    }

    if agent_result:
        data["agent_result"] = agent_result

    with open(output_file, "w") as f:
        json.dump(data, f)

    print(f"[Bridge] Updated status to: {status}")
