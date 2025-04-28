import json
import os

def write_to_bubble(reply):
    public_path = os.path.join(os.path.dirname(__file__), '..', 'public')
    output_file = os.path.join(public_path, 'spark_output.json')

    # Make sure the public folder exists
    os.makedirs(public_path, exist_ok=True)

    data = {"reply": reply}

    with open(output_file, "w") as f:
        json.dump(data, f)

    print(f"[Bridge] Wrote Spark reply to {output_file}")
