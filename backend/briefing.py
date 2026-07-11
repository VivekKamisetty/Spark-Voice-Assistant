"""Morning briefing (Phase 6 of docs/SPARK_V2_SPEC.md) — narrow, once-a-day
proactivity. On the first voice activity of a calendar day (or app launch
after MIN_BRIEFING_HOUR, whichever comes first), Spark opens with a short
spoken summary of today's calendar events, due/overdue reminders, and the
weather, instead of waiting to be asked.

Calendar/Reminders are queried directly via osascript rather than through
Claude's tool-use loop — this needs to run deterministically at startup, not
depend on the model deciding to call a tool. Weather is a single keyless
Open-Meteo call; the only Claude API call here is the final one-shot
composition of the spoken summary from that gathered data.
"""

import datetime
import json
import os
import subprocess

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

import protocol
import ws_server

load_dotenv()

CONFIG_PATH = os.path.expanduser("~/.spark/config.json")

_DEFAULT_CONFIG = {
    "briefing_enabled": True,
    "last_briefing_date": None,
    "location": None,
}

# "App launch after 5 a.m." per the spec — local time. Guards against a
# briefing firing on a very-early launch before the day's calendar/reminders
# are meaningfully settled.
MIN_BRIEFING_HOUR = 5

# AppleScript calendar/reminders queries can be slow with several
# calendars/lists — same rationale as claude_client.py's
# SHELL_COMMAND_TIMEOUT for osascript calls.
_OSASCRIPT_TIMEOUT = 45

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _read_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return dict(_DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT_CONFIG)
    return {**_DEFAULT_CONFIG, **data}


def _write_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _today_str() -> str:
    return datetime.date.today().isoformat()


def should_deliver_briefing(config: dict = None) -> bool:
    config = config if config is not None else _read_config()
    if not config.get("briefing_enabled", True):
        return False
    if config.get("last_briefing_date") == _today_str():
        return False
    if datetime.datetime.now().hour < MIN_BRIEFING_HOUR:
        return False
    return True


_CALENDAR_SCRIPT = '''
tell application "Calendar"
    set todayStart to current date
    set time of todayStart to 0
    set todayEnd to todayStart + 1 * days
    set eventList to {}
    repeat with cal in calendars
        try
            set calEvents to (every event of cal whose start date ≥ todayStart and start date < todayEnd)
            repeat with ev in calEvents
                set end of eventList to (summary of ev as string) & " at " & (time string of (start date of ev))
            end repeat
        end try
    end repeat
    return eventList
end tell
'''

_REMINDERS_SCRIPT = '''
tell application "Reminders"
    set todayEnd to current date
    set time of todayEnd to 0
    set todayEnd to todayEnd + 1 * days
    set reminderList to {}
    repeat with lst in lists
        try
            set dueReminders to (reminders of lst whose completed is false and due date is not missing value and due date < todayEnd)
            repeat with r in dueReminders
                set end of reminderList to (name of r as string)
            end repeat
        end try
    end repeat
    return reminderList
end tell
'''


def _run_osascript(script: str, permission_hint: str, empty_text: str, timeout_text: str, error_text: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return timeout_text

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # -1743 is macOS's AppleEvent "not authorized" error code — the same
        # class of gap as claude_client.py's screenshot-permission handling:
        # surface it as a fixable condition, not a silent/permanent failure.
        if "not allowed" in stderr.lower() or "-1743" in stderr:
            return permission_hint
        return error_text

    text = result.stdout.strip()
    return text if text else empty_text


def _get_todays_calendar_events() -> str:
    return _run_osascript(
        _CALENDAR_SCRIPT,
        permission_hint="Calendar access hasn't been granted yet (System Settings > Privacy & Security > Calendars).",
        empty_text="No events today.",
        timeout_text="Calendar lookup timed out.",
        error_text="Couldn't read the calendar.",
    )


def _get_due_reminders() -> str:
    return _run_osascript(
        _REMINDERS_SCRIPT,
        permission_hint="Reminders access hasn't been granted yet (System Settings > Privacy & Security > Reminders).",
        empty_text="No reminders due.",
        timeout_text="Reminders lookup timed out.",
        error_text="Couldn't read reminders.",
    )


def _fetch_ipapi_co():
    response = requests.get("https://ipapi.co/json/", timeout=5)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise ValueError(data.get("reason", "ipapi.co error"))
    return {
        "lat": data["latitude"],
        "lon": data["longitude"],
        "place": data.get("city") or data.get("region") or "your area",
    }


def _fetch_ip_api_com():
    # HTTP-only on the free tier (HTTPS requires a paid plan) — used only as
    # a fallback when ipapi.co is unavailable (found live: its free tier
    # rate-limits aggressively), and only ever returns approximate
    # city-level location, not anything sensitive.
    response = requests.get("http://ip-api.com/json/", timeout=5)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise ValueError(data.get("message", "ip-api.com error"))
    return {
        "lat": data["lat"],
        "lon": data["lon"],
        "place": data.get("city") or data.get("regionName") or "your area",
    }


def _get_location(config: dict):
    """Cached in config.json after the first successful lookup so briefings
    after the first don't re-hit either geolocation service. Returns None
    (rather than raising) if both providers fail — weather is a nice-to-have,
    not something that should ever block the rest of the briefing.
    """
    location = config.get("location")
    if location:
        return location

    location = None
    for fetch in (_fetch_ipapi_co, _fetch_ip_api_com):
        try:
            location = fetch()
            break
        except Exception as e:
            print(f"[Briefing] {fetch.__name__} lookup failed: {e}")
    if location is None:
        return None

    config["location"] = location
    _write_config(config)
    return location


# WMO weather interpretation codes (Open-Meteo's `weather_code` field) —
# only the common ones; anything unmapped just omits the condition phrase
# rather than guessing.
_WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    95: "thunderstorms", 96: "thunderstorms", 99: "severe thunderstorms",
}


def _get_weather(config: dict) -> str:
    location = _get_location(config)
    if location is None:
        return "Weather unavailable."
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": location["lat"],
                "longitude": location["lon"],
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        current_temp = data["current"]["temperature_2m"]
        condition = _WEATHER_CODES.get(data["current"]["weather_code"], "")
        high = data["daily"]["temperature_2m_max"][0]
        low = data["daily"]["temperature_2m_min"][0]
        return (
            f"Currently {current_temp:.0f}°F"
            f"{' and ' + condition if condition else ''} in {location['place']}, "
            f"with a high of {high:.0f}°F and a low of {low:.0f}°F today."
        )
    except Exception as e:
        print(f"[Briefing] Weather lookup failed: {e}")
        return "Weather unavailable."


_BRIEFING_SYSTEM_PROMPT = (
    "You are composing a short spoken morning briefing for the user, to be "
    "read aloud by a voice assistant. Using the information below, write at "
    "most 4 natural, conversational sentences covering the weather, today's "
    "calendar events, and any due or overdue reminders. Skip a category "
    "entirely if there's nothing in it rather than saying \"no events\" -- "
    "only mention what's actually there. Do not use lists, headers, or "
    "markdown -- this is read aloud as plain speech."
)


def _compose_briefing_text(calendar_text: str, reminders_text: str, weather_text: str) -> str:
    response = _get_client().messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        system=_BRIEFING_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Weather: {weather_text}\n\n"
                f"Today's calendar events: {calendar_text}\n\n"
                f"Due/overdue reminders: {reminders_text}"
            ),
        }],
    )
    if response.stop_reason == "refusal":
        return ""
    return "".join(block.text for block in response.content if block.type == "text").strip()


def maybe_deliver_briefing(speak_fn=None) -> bool:
    """Delivers the once-a-day morning briefing if conditions are met
    (enabled, not yet given today, past MIN_BRIEFING_HOUR local). Returns
    True if a briefing was actually delivered.
    """
    config = _read_config()
    if not should_deliver_briefing(config):
        return False

    # Marked done immediately, before any of the gathering below (which
    # calls out to macOS apps and two external services and can fail in any
    # number of ways) -- a mid-gather crash or a denied permission should
    # not leave the briefing re-attempting on every subsequent turn for the
    # rest of the day. Best-effort, once per day, not best-effort-until-it-
    # works.
    config["last_briefing_date"] = _today_str()
    _write_config(config)

    try:
        calendar_text = _get_todays_calendar_events()
        reminders_text = _get_due_reminders()
        weather_text = _get_weather(config)
        text = _compose_briefing_text(calendar_text, reminders_text, weather_text)
    except Exception as e:
        print(f"[Briefing] Failed to compose morning briefing: {e}")
        return False

    if not text:
        return False

    # Broadcast before speaking, not after: speak_fn blocks until TTS
    # playback actually finishes (many seconds for a multi-sentence
    # briefing), and the panel should show the text as it starts being
    # spoken, not only once it's already fully done.
    ws_server.broadcast(protocol.briefing_message(text))
    if speak_fn:
        speak_fn(text)
    return True
