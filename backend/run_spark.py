import time
import subprocess
import threading
from wake_word_listener import wake_word_listener

print("[Spark] Bootstrapping Spark system...")

# Flag to prevent double launch
frontend_started = False

def launch_frontend():
    global frontend_started
    if not frontend_started:
        print("[Frontend] 🪟 Launching Electron UI...")
        subprocess.Popen(["npm", "run", "start"], cwd=".")
        frontend_started = True
    else:
        print("[Frontend] 🔄 Already launched.")

def on_wake_word_detected():
    launch_frontend()

# Run wake word listener in a thread
threading.Thread(target=wake_word_listener, args=(on_wake_word_detected,), daemon=True).start()

# Keep alive
while True:
    time.sleep(1)
