import os
import base64
import tempfile
import subprocess
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def contains_visual_keyword(prompt: str) -> bool:
    visual_keywords = [
        "on my screen", "on screen", "this screen", "what's this error",
        "does this look", "what's wrong with this", "do these colors", "this ui",
        "this code", "screenshot", "on the screen", "screen", "docs", "can you take a look"
    ]
    prompt_lower = prompt.lower()
    return any(keyword in prompt_lower for keyword in visual_keywords)


def gpt_screenshot_check(prompt: str) -> bool:
    system_prompt = (
        "You are Spark's context evaluator. Based on the user's spoken request, "
        "determine whether Spark should take a screenshot of the current screen to answer accurately. "
        "Return only 'TRUE' if the request refers to anything visual, UI, on-screen, or document-related. "
        "Otherwise, return only 'FALSE'."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            temperature=0,
            max_tokens=4,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        )

        reply = response.choices[0].message.content.strip().upper()
        print(f"[Spark] GPT screenshot decision: '{reply}'")

        return "TRUE" in reply

    except Exception as e:
        print(f"[Spark] GPT check failed: {e}")
        return False


def should_take_screenshot(prompt: str) -> bool:
    if contains_visual_keyword(prompt):
        print("[Spark] 🧠 Matched visual keyword — will capture screenshot.")
        return True

    print("[Spark] 🧠 No keyword match — deferring to GPT decision...")
    return gpt_screenshot_check(prompt)


def route_gpt_reply(prompt, chat_history, screenshot_enabled=False):
    model = "gpt-3.5-turbo"
    requires_image = False

    # Intelligence-based screenshot detection
    if screenshot_enabled:
        if should_take_screenshot(prompt):
            model = "gpt-4o"
            requires_image = True

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
            {
                "role": "system",
                "content": (
                    "You are Spark, a helpful assistant. Use the visual content and the user's prompt to craft a natural, human-like reply. "
                    "Do not mention that a screenshot or image was provided. Speak as if you already have visual awareness of the user's context."
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{image_b64}"
                    }},
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    else:
        messages = [
            {"role": "system", "content": "You are Spark, a helpful and concise voice assistant."}
        ] + chat_history + [{"role": "user", "content": prompt}]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        return response.choices[0].message.content, model

    except Exception as e:
        print("OpenAI Error:", e)
        return "Sorry, there was a problem generating a response.", model
