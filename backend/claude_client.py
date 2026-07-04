import os
import base64
import tempfile
import subprocess
import json
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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
    }
]


def execute_shell_command(command: str) -> str:
    """Execute a shell command and return output."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout[:500] if result.stdout else result.stderr[:500]
    except subprocess.TimeoutExpired:
        return "Command timed out after 10 seconds."
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


def process_tool_call(tool_name: str, tool_input: dict) -> str:
    """Route tool calls to the appropriate handler."""
    if tool_name == "execute_shell_command":
        return execute_shell_command(tool_input.get("command", ""))
    elif tool_name == "search_files":
        return search_files(
            tool_input.get("pattern", ""),
            tool_input.get("directory")
        )
    elif tool_name == "open_application":
        return open_application(tool_input.get("app_name", ""))
    else:
        return f"Unknown tool: {tool_name}"


def route_claude_reply(prompt: str, chat_history: list, screenshot_enabled: bool = False) -> tuple:
    """
    Route a prompt through Claude with tool use.
    Returns (response_text, model_used, tools_called)
    """
    model = "claude-sonnet-5"
    requires_image = False
    tools_called = []

    # Check if screenshot is needed (simple heuristic for now)
    visual_keywords = [
        "on my screen", "screenshot", "what's on screen", "show me", 
        "what do you see", "what's this", "this ui", "this error"
    ]
    
    if screenshot_enabled and any(kw in prompt.lower() for kw in visual_keywords):
        requires_image = True
        print("[Router] 📸 Screenshot requested, capturing...")
        
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                screenshot_path = tmpfile.name
            subprocess.run(["screencapture", "-x", screenshot_path])
            
            with open(screenshot_path, "rb") as f:
                image_bytes = f.read()
                image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            os.remove(screenshot_path)
            print(f"[Router] 📸 Screenshot captured")
        except Exception as e:
            print(f"[Router] Screenshot failed: {e}")
            requires_image = False

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
    else:
        messages.append({
            "role": "user",
            "content": prompt
        })

    # System prompt
    system_prompt = (
        "You are Spark, a helpful voice assistant running on a MacBook. "
        "You can execute commands, search files, and open applications. "
        "Keep responses concise and natural for voice output. "
        "If the user asks you to do something, use the available tools. "
        "If a tool call fails, explain it to the user naturally."
    )

    # Call Claude with tool use
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            messages=messages
        )

        # Process tool calls in a loop
        while response.stop_reason == "tool_use":
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
                
                print(f"[Claude] 🔧 Calling tool: {tool_name}")
                tool_result = process_tool_call(tool_name, tool_input)
                print(f"[Claude] 📤 Tool result: {tool_result[:100]}...")
                
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
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                tools=TOOLS,
                messages=messages
            )

        # Extract final text response
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        return final_text or "I'm ready to help.", model, tools_called

    except Exception as e:
        print(f"[Claude] Error: {e}")
        return f"Sorry, there was a problem: {str(e)}", model, tools_called
