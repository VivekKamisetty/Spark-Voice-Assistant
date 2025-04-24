import time
from gpt_client import get_gpt_reply
from tts import speak
from bridge import write_to_bubble
from whisper_handler import run_whisper_binary

def main():
    chat_history = []
    print("[Spark] Starting up...")
    proc = run_whisper_binary()

    while True:
        line = proc.stdout.readline()
        if not line:
            continue

        line = line.decode("utf-8").strip()
        if not line or line in ["...", "*", ".", "[Start speaking]"]:
            continue

        print(f"[User] {line}")
        chat_history.append({"role": "user", "content": line})

        reply = get_gpt_reply(line, chat_history)
        print(f"[GPT] {reply}")

        chat_history.append({"role": "assistant", "content": reply})

        speak(reply)
        write_to_bubble(reply)

        if len(chat_history) > 20:
            chat_history = chat_history[-18:]

if __name__ == "__main__":
    main()
