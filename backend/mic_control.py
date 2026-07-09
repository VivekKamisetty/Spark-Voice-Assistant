"""Shared microphone mute state (Phase 4) — lives in its own module so
confirmation_gate.py and claude_client.py can both reach it without a
circular import through spark_whisper_mic.py (which already imports
route_claude_reply from claude_client.py).

should_listen gates whether mic_listener's capture loop pays attention to
anything at all; mute/unmute additionally drive the actual macOS input
volume, since Spark has no echo cancellation and would otherwise hear its
own TTS output as if it were the user talking.
"""

import subprocess

should_listen = True


def mute_microphone():
    global should_listen
    should_listen = False
    subprocess.run(['osascript', '-e', 'set volume input volume 0'])


def unmute_microphone():
    global should_listen
    should_listen = True
    subprocess.run(['osascript', '-e', 'set volume input volume 100'])
