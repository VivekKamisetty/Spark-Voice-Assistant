import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def route_gpt_reply(prompt, chat_history, screenshot_enabled=False):
    visual_keywords = [
        "on my screen", "on screen", "this screen", "what's this error",
        "does this look", "what's wrong with this", "do these colors", "this ui",
        "this code", "screenshot", "screen"
    ]
    model = "gpt-3.5-turbo"

    normalized = prompt.lower()
    if any(keyword in normalized for keyword in visual_keywords):
        model = "gpt-4o"

    print(f"[Router] Using {model} for: '{prompt}'")

    messages = [{"role": "system", "content": "You are Spark, a helpful and concise voice assistant."}] + \
               chat_history + [{"role": "user", "content": prompt}]

    if screenshot_enabled:
        print("[Router] Screenshot flag is set, but capture not yet implemented.")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        return response.choices[0].message.content
    except Exception as e:
        print("OpenAI Error:", e)
        return "Sorry, there was a problem generating a response."
