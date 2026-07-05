"""WebSocket hub bridging the synchronous/threaded backend to the frontend.

The rest of spark_whisper_mic.py is synchronous (blocking Whisper calls,
threading.Thread, plain queue.Queue), but the `websockets` library is
asyncio-based. This module runs its own event loop in a background thread
so the rest of the backend never has to touch asyncio directly: call
start() once, then broadcast(message) from anywhere, and drain
incoming_queue for messages sent back from the frontend.
"""

import asyncio
import json
import queue
import threading

import websockets

from protocol import parse_incoming

HOST = "localhost"
PORT = 8765

incoming_queue: "queue.Queue[dict]" = queue.Queue()

_loop: asyncio.AbstractEventLoop | None = None
_clients: set = set()
_started = threading.Event()


async def _handler(websocket):
    _clients.add(websocket)
    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            parsed = parse_incoming(data)
            if parsed is not None:
                incoming_queue.put(parsed)
    except websockets.ConnectionClosed:
        pass
    finally:
        _clients.discard(websocket)


async def _broadcast_coro(message: dict):
    if not _clients:
        return
    data = json.dumps(message)
    dead = set()
    for client in list(_clients):
        try:
            await client.send(data)
        except websockets.ConnectionClosed:
            dead.add(client)
    _clients.difference_update(dead)


async def _run_server():
    global _loop
    _loop = asyncio.get_running_loop()
    async with websockets.serve(_handler, HOST, PORT):
        print(f"[WS] Listening on ws://{HOST}:{PORT}")
        _started.set()
        await asyncio.Future()  # run forever


def _thread_main():
    asyncio.run(_run_server())


def start():
    """Start the WebSocket server in a background thread. Safe to call once."""
    if _started.is_set():
        return
    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    _started.wait(timeout=5)


def broadcast(message: dict):
    """Thread-safe: send a message to all connected clients. No-op if the
    server hasn't started yet or no clients are connected (e.g. Electron
    hasn't opened the app yet) — callers don't need to check either case.
    """
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_coro(message), _loop)
