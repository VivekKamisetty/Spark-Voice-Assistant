import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_gpt_reply(prompt, chat_history):
    messages = [{"role": "system", "content": "You are Spark, a helpful and concise voice assistant."}] + \
               chat_history + [{"role": "user", "content": prompt}]

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # or "gpt-4", "gpt-4o", etc.
            messages=messages
        )
        return response.choices[0].message.content
    except Exception as e:
        print("OpenAI Error:", e)
        return "Sorry, there was a problem generating a response."