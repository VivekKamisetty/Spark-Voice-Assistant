import json
import os
import shutil

def write_to_bubble(reply, path="../shared/spark_output.json"):
    # Ensure the shared directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Write the reply to the shared file
    data = {"reply": reply}
    with open(path, "w") as f:
        json.dump(data, f)

    # Copy to public folder for renderer.js to read
    shutil.copy(path, "../public/spark_output.json")
