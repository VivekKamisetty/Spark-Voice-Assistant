import subprocess
import threading

spark_process = None

def is_backend_running():
    return spark_process is not None and spark_process.poll() is None

def start_backend():
    global spark_process
    if spark_process and spark_process.poll() is None:
        print("[Backend] 🔄 Already running.")
        return

    print("[Backend] 🚀 Starting Spark backend...")
    spark_process = subprocess.Popen(['python3', 'spark_whisper_mic.py'])

    # 🔄 Watch for process exit and clean up
    def monitor():
        global spark_process
        spark_process.wait()
        print("[Backend] 💀 Backend exited.")
        spark_process = None

    threading.Thread(target=monitor, daemon=True).start()


def stop_backend():
    global spark_process
    if spark_process:
        print("[Backend] 🛑 Stopping Spark backend...")
        spark_process.terminate()
        spark_process = None