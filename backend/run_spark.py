from wake_word_listener import wake_word_listener
import threading
import time

if __name__ == "__main__":
    print("[Run Spark] Bootstrapping Spark system...")
    threading.Thread(target=wake_word_listener, daemon=True).start()

    while True:
        time.sleep(1)
