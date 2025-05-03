import os
import base64
import tempfile
import subprocess
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def route_gpt_reply(prompt, chat_history, screenshot_enabled=False):
    visual_keywords = [
        "on my screen", "on screen", "this screen", "what's this error",
        "does this look", "what's wrong with this", "do these colors", "this ui",
        "this code", "screenshot", "on the screen", "screen", "docs", "Can you take a look"
    ]
    model = "gpt-3.5-turbo"
    requires_image = False

    normalized = prompt.lower()
    if any(keyword in normalized for keyword in visual_keywords):
        model = "gpt-4o"
        requires_image = screenshot_enabled

    print(f"[Router] Using {model} for: '{prompt}'")

    if requires_image:
        print("[Router] Capturing screenshot...")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            screenshot_path = tmpfile.name
        subprocess.run(["screencapture", "-x", screenshot_path])
        print(f"[Router] Screenshot saved to: {screenshot_path}")

        with open(screenshot_path, "rb") as f:
            image_bytes = f.read()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        os.remove(screenshot_path)
        print(f"[Router] Screenshot deleted: {screenshot_path}")

        messages = [
            {"role": "system", "content": "You are Spark, a helpful assistant that can interpret screenshots and user prompts."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{image_b64}"
                }},
                {"type": "text", "text": prompt}
            ]}
        ]
    else:
        messages = [{"role": "system", "content": "You are Spark, a helpful and concise voice assistant."}] + \
                   chat_history + [{"role": "user", "content": prompt}]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        return response.choices[0].message.content, model
    except Exception as e:
        print("OpenAI Error:", e)
        return "Sorry, there was a problem generating a response."