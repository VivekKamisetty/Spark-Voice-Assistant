import subprocess

def run_whisper_binary():
    whisper_path = "../whisper/build/bin/whisper-stream"
    model_path = "../whisper/models/ggml-base.en.bin"

    proc = subprocess.Popen([
        whisper_path,
        "-m", model_path,
        "-t", "4",
        "--no-gpu",
        "-c", "1",
        "--step", "5000",
        "--keep", "100",
        "--vad-thold", "0.6",
        "--language", "en",
        "--max-tokens", "32",
        "--print-special"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return proc
