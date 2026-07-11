import os
import base64
import tempfile
import subprocess
import json
from anthropic import Anthropic
from dotenv import load_dotenv

import ws_server
import protocol
import confirmation_gate
import risk_classifier
import store
import memory
from sentence_splitter import split_into_sentences

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# AppleScript/osascript calls against Calendar or Reminders can legitimately
# take much longer than a typical shell command, especially with several
# calendars — 10s was killing valid in-progress calls, not just runaway ones.
SHELL_COMMAND_TIMEOUT = 45

# Caps the tool-use loop below so a persistently failing approach (wrong tool,
# bad permissions, etc.) fails gracefully after a bounded number of attempts
# instead of Claude silently improvising increasingly complex workarounds.
MAX_TOOL_ITERATIONS = 8

# Define tools that Claude can use
TOOLS = [
    {
        "name": "execute_shell_command",
        "description": "Execute a shell command and return the output. Use for running commands, checking system info, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g., 'date', 'pwd', 'ls -la')"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for files in a directory by name pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory to search in (default: home directory)"
                },
                "pattern": {
                    "type": "string",
                    "description": "File name pattern to search for (e.g., '*.pdf', 'notes*')"
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "open_application",
        "description": "Open an application or file on macOS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the application or path to open (e.g., 'Spotify', 'Safari', '/Applications/VS Code.app')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "remember_this",
        "description": "Save a durable fact about the user for future conversations (preferences, ongoing projects, recurring schedule, names of people/places). Use when the user explicitly asks you to remember something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember, phrased so it makes sense read alone with no other context later (e.g. \"The user's standup is at 9:30am\", not \"it's at 9:30\")."
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "forget",
        "description": "Delete a previously remembered fact about the user. Use when the user explicitly asks you to forget something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A description of the fact to forget, in the user's own words."
                }
            },
            "required": ["query"]
        }
    }
]


def execute_shell_command(command: str, on_sentence=None) -> str:
    """Execute a shell command and return output. Commands not confidently
    recognized as safe (risk_classifier.py) require confirmation first —
    declined or timed-out confirmations are not run at all. This is the gate
    that was missing entirely before Phase 4: earlier in this project, a
    misheard "yes" let Claude use this exact tool to kill Spark's own
    Electron process with zero confirmation.

    on_sentence, if given, is the same streaming callback used for regular
    replies (see route_claude_reply) — passing it through here rather than
    speaking separately means the confirmation prompt is fed through the one
    active TTS consumer instead of opening a second, concurrent one.
    """
    if risk_classifier.needs_confirmation(command):
        confirmed = confirmation_gate.request_confirmation(
            f"Should I run this: {command}",
            risk="high",
            options=["Yes", "No"],
            speak_fn=on_sentence,
        )
        if not confirmed:
            return f"Not run — the user did not confirm: {command}"

    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=SHELL_COMMAND_TIMEOUT)
        return result.stdout[:500] if result.stdout else result.stderr[:500]
    except subprocess.TimeoutExpired:
        return f"Command timed out after {SHELL_COMMAND_TIMEOUT} seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"


def search_files(pattern: str, directory: str = None) -> str:
    """Search for files matching a pattern."""
    try:
        if directory is None:
            directory = os.path.expanduser("~")
        
        search_cmd = f"find {directory} -name '{pattern}' -type f 2>/dev/null | head -20"
        result = subprocess.run(search_cmd, shell=True, capture_output=True, text=True, timeout=15)
        return result.stdout if result.stdout else "No files found."
    except Exception as e:
        return f"Error searching files: {str(e)}"


def open_application(app_name: str) -> str:
    """Open an application, file, or URL on macOS."""
    try:
        # URLs and paths get opened directly; anything else is looked up as an app name
        if app_name.startswith(("http://", "https://")) or app_name.startswith("/"):
            cmd = ["open", app_name]
        else:
            cmd = ["open", "-a", app_name]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "unknown error"
            return f"Failed to open {app_name}: {error_msg}"
        return f"Opened {app_name}."
    except Exception as e:
        return f"Error opening application: {str(e)}"


def remember_this(content: str, session_id) -> str:
    """Embed and store a durable fact. Opens its own short-lived DB
    connection since tool calls run on the streaming background thread, not
    spark_whisper_mic.py's main() thread that owns the shared connection
    (see store.py's docstring — sqlite3 connections aren't safe to share
    across threads). Memory writes are low-risk (Phase 5 spec) — no
    confirmation gate, unlike forget below.
    """
    conn = store.init_db()
    try:
        memory_id = memory.save_memory(conn, content, session_id)
    finally:
        conn.close()
    if memory_id is None:
        return f"Already remembered something similar to: {content}"
    return f"Remembered: {content}"


def forget(query: str, on_sentence=None) -> str:
    """Delete the best-matching remembered fact, after confirmation —
    deletions require the same confirmation gate as risky shell commands
    (Phase 4), even though memory writes themselves don't.
    """
    conn = store.init_db()
    try:
        match = memory.find_best_match(conn, query)
        if match is None:
            return f"Couldn't find anything remembered matching: {query}"
        memory_id, content, _ = match

        confirmed = confirmation_gate.request_confirmation(
            f"Should I forget this: {content}?",
            risk="low",
            options=["Yes", "No"],
            speak_fn=on_sentence,
        )
        if not confirmed:
            return f"Not forgotten — the user did not confirm: {content}"

        store.delete_memory(conn, memory_id)
        return f"Forgot: {content}"
    finally:
        conn.close()


def _tool_summary(tool_name: str, tool_input: dict) -> str:
    """Short human-readable description of a tool call, for tool_activity messages."""
    if tool_name == "execute_shell_command":
        return f"Running: {tool_input.get('command', '')}"[:100]
    elif tool_name == "search_files":
        return f"Searching for {tool_input.get('pattern', '')}"
    elif tool_name == "open_application":
        return f"Opening {tool_input.get('app_name', '')}"
    elif tool_name == "remember_this":
        return f"Remembering: {tool_input.get('content', '')}"[:100]
    elif tool_name == "forget":
        return f"Forgetting: {tool_input.get('query', '')}"[:100]
    return tool_name


def process_tool_call(tool_name: str, tool_input: dict, on_sentence=None, session_id=None) -> str:
    """Route tool calls to the appropriate handler."""
    if tool_name == "execute_shell_command":
        return execute_shell_command(tool_input.get("command", ""), on_sentence=on_sentence)
    elif tool_name == "search_files":
        return search_files(
            tool_input.get("pattern", ""),
            tool_input.get("directory")
        )
    elif tool_name == "open_application":
        return open_application(tool_input.get("app_name", ""))
    elif tool_name == "remember_this":
        return remember_this(tool_input.get("content", ""), session_id)
    elif tool_name == "forget":
        return forget(tool_input.get("query", ""), on_sentence=on_sentence)
    else:
        return f"Unknown tool: {tool_name}"


def route_claude_reply(prompt: str, chat_history: list, screenshot_enabled: bool = False, on_sentence=None, session_id=None) -> tuple:
    """
    Route a prompt through Claude with tool use.
    Returns (response_text, model_used, tools_called, tool_calls_log)

    tools_called is a flat list of tool names (kept as-is for existing
    callers); tool_calls_log is the richer per-call detail — name, summary,
    and actual result — needed to log real commands + results to SQLite
    rather than just which tool names were used (Phase 4).

    If on_sentence is given, it's called with each sentence of the reply as
    soon as it's available (streamed from the API), instead of only once
    with the complete text at the end — this is what lets the caller start
    speaking a long reply before the rest of it has finished generating.
    Tool-calling turns don't usually have text to stream (Claude is deciding
    to call a tool, not composing a spoken answer yet), but if Claude does
    say something before calling a tool, that gets streamed too.

    session_id is threaded through to the remember_this/forget tool handlers
    (for audit provenance on new memories) and used here to retrieve
    semantically relevant memories for this specific prompt (Phase 5).
    """
    model = "claude-sonnet-5"
    requires_image = False
    tools_called = []
    tool_calls_log = []

    # Check if screenshot is needed (simple heuristic for now)
    visual_keywords = [
        "on my screen", "screenshot", "what's on screen", "show me", 
        "what do you see", "what's this", "this ui", "this error"
    ]
    
    screenshot_permission_denied = False

    if screenshot_enabled and any(kw in prompt.lower() for kw in visual_keywords):
        requires_image = True
        print("[Router] 📸 Screenshot requested, capturing...")

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                screenshot_path = tmpfile.name
            result = subprocess.run(["screencapture", "-x", screenshot_path], capture_output=True)

            if result.returncode != 0 or os.path.getsize(screenshot_path) == 0:
                # screencapture exits non-zero (or writes an empty file) when
                # macOS hasn't granted this process Screen Recording
                # permission (System Settings > Privacy & Security > Screen
                # Recording) — that's a fixable permission gap, not a
                # permanent "I can't see your screen" limitation, so it gets
                # surfaced to Claude as its own condition rather than being
                # silently treated the same as any other capture failure.
                screenshot_permission_denied = True
                requires_image = False
                print(f"[Router] Screenshot permission likely denied (exit {result.returncode}): {result.stderr.decode(errors='replace').strip()}")
            else:
                with open(screenshot_path, "rb") as f:
                    image_bytes = f.read()
                    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
                print(f"[Router] 📸 Screenshot captured")
        except Exception as e:
            print(f"[Router] Screenshot failed: {e}")
            requires_image = False
        finally:
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)

    # Build messages
    messages = []

    # Add chat history, excluding the current turn (added separately below,
    # since chat_history already has it appended as the last entry by the caller)
    for msg in chat_history[:-1]:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Add current prompt
    if requires_image:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        })
    elif screenshot_permission_denied:
        messages.append({
            "role": "user",
            "content": (
                f"{prompt}\n\n"
                "(System note: a screenshot could not be captured because "
                "Spark hasn't been granted Screen Recording permission on "
                "this Mac. Tell the user to grant it in System Settings > "
                "Privacy & Security > Screen Recording, then ask again — "
                "don't imply you're permanently unable to see the screen.)"
            )
        })
    else:
        messages.append({
            "role": "user",
            "content": prompt
        })

    # System prompt
    system_prompt = (
        "You are Spark, a helpful voice assistant running on a MacBook. "
        "You can execute commands, search files, open applications, and "
        "remember or forget durable facts about the user. "
        "Keep responses concise and natural for voice output. "
        "If the user asks you to do something, use the available tools. "
        "If a tool call fails, explain it to the user naturally. "
        "For Calendar or Reminders, use built-in macOS AppleScript "
        "(osascript) directly rather than third-party command-line tools "
        "like icalBuddy — those may not be installed on this machine. "
        "AppleScript calendar queries can take a while with several "
        "calendars; that's expected, so run them as a normal foreground "
        "command and wait for the result rather than backgrounding the "
        "process or polling for it — the timeout is long enough to wait.\n\n"
        "Never narrate that you're about to use a tool ('Let me check...', "
        "'I'll look that up...') — the UI already shows a thinking "
        "indicator and a live tool-activity line while a tool runs, so a "
        "verbal announcement is redundant filler. Call the tool silently "
        "and speak only once you have the actual result.\n\n"
        "Structure every reply as a short spoken-friendly headline first: "
        "1-2 natural, conversational sentences that directly answer the "
        "question, the way you'd say it out loud — never a list, table, or "
        "string of exact numbers here, even a short one. Everything the "
        "user actually sees is the full reply either way — this split only "
        "controls what gets spoken aloud, since reading is faster than "
        "listening once the detail is already on screen. If there's more "
        "worth including — a list, table, code, elaboration, multiple "
        "facts — add a line containing exactly ---DETAIL--- right after "
        "the headline, then the rest; use a real markdown table (pipe "
        "syntax) instead of a numbered list when the data is naturally "
        "tabular (e.g. several items each with multiple attributes like "
        "name + size + percentage). If the whole answer already fits in "
        "that short headline, skip the marker and the detail section "
        "entirely."
    )

    # Phase 5: pull in whatever remembered facts are actually relevant to
    # this specific prompt (semantic match, not just "recent") — opens its
    # own short-lived connection since this runs on the tool-call/streaming
    # thread, not spark_whisper_mic.py's main() thread (see memory.py).
    memory_conn = store.init_db()
    try:
        relevant_memories = memory.retrieve_relevant(memory_conn, prompt)
    finally:
        memory_conn.close()
    if relevant_memories:
        system_prompt += (
            "\n\nKnown facts about the user, from previous conversations "
            "(only mention one if it's actually relevant to this request):\n"
            + "\n".join(f"- {fact}" for fact in relevant_memories)
        )

    def stream_call():
        """One streaming API call: yields sentences to on_sentence as they
        complete, and returns the final Message (same shape client.messages
        .create() would have returned, including full tool_use blocks) so
        the tool-use loop below is otherwise unchanged from the non-streaming
        version.
        """
        with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for sentence in split_into_sentences(stream.text_stream):
                full_text_pieces.append(sentence)
                if on_sentence:
                    on_sentence(sentence)
            return stream.get_final_message()

    full_text_pieces = []

    # Call Claude with tool use
    try:
        response = stream_call()

        # Process tool calls in a loop, capped so a persistently failing
        # approach fails gracefully instead of looping indefinitely.
        iterations = 0
        hit_iteration_cap = False
        while response.stop_reason == "tool_use":
            iterations += 1
            if iterations > MAX_TOOL_ITERATIONS:
                hit_iteration_cap = True
                break

            # Find tool use blocks
            tool_uses = [block for block in response.content if block.type == "tool_use"]

            if not tool_uses:
                break

            # Process each tool call
            tool_results = []
            for tool_use in tool_uses:
                tool_name = tool_use.name
                tool_input = tool_use.input
                tools_called.append(tool_name)
                summary = _tool_summary(tool_name, tool_input)

                print(f"[Claude] 🔧 Calling tool: {tool_name}")
                ws_server.broadcast(protocol.tool_activity_message(tool_name, "running", summary))
                tool_result = process_tool_call(tool_name, tool_input, on_sentence=on_sentence, session_id=session_id)
                print(f"[Claude] 📤 Tool result: {tool_result[:100]}...")
                ws_server.broadcast(protocol.tool_activity_message(tool_name, "done", summary))
                tool_calls_log.append({"name": tool_name, "summary": summary, "result": tool_result})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": tool_result
                })

            # Add assistant response and tool results to messages
            messages.append({
                "role": "assistant",
                "content": response.content
            })
            messages.append({
                "role": "user",
                "content": tool_results
            })

            # Call Claude again with tool results
            response = stream_call()

        if hit_iteration_cap:
            fallback = (
                "I'm having trouble completing this — it needed more steps than expected. "
                "Let me know if you'd like me to keep trying or take a different approach."
            )
            if on_sentence:
                on_sentence(fallback)
            return fallback, model, tools_called, tool_calls_log

        final_text = "".join(full_text_pieces)
        if not final_text:
            final_text = "I'm ready to help."
            if on_sentence:
                on_sentence(final_text)

        return final_text, model, tools_called, tool_calls_log

    except Exception as e:
        print(f"[Claude] Error: {e}")
        error_text = f"Sorry, there was a problem: {str(e)}"
        if on_sentence:
            on_sentence(error_text)
        return error_text, model, tools_called, tool_calls_log
