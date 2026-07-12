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

import concurrent.futures
import datetime
import json
import os
import subprocess
import time

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


_CALENDAR_NAMES_SCRIPT = 'tell application "Calendar" to return (name of every calendar)'

# Bound on a single calendar's own event query. Found live: querying all
# calendars in one combined "whose start date >= X" AppleScript query (the
# original approach) reliably took the full _OSASCRIPT_TIMEOUT (45s) because
# two holiday-subscription calendars alone accounted for ~28s of it --
# Calendar.app's "whose" date-range filter isn't indexed, so it has to
# evaluate/expand every occurrence of every recurring event (holiday
# calendars are the classic pathological case: years of annually-recurring
# all-day entries) to decide whether any single occurrence falls in today's
# range. Querying each calendar separately bounds one slow calendar's cost
# to its own timeout rather than the whole lookup.
#
# Deliberately sequential, not concurrent, despite that being the first fix
# attempted here: querying calendars via a ThreadPoolExecutor made things
# *worse*, not better -- found live that even 2 concurrent osascript calls
# against Calendar.app caused a calendar that normally answers in ~8s to
# time out entirely. Calendar.app appears to serialize its own Apple Events
# handling internally, so concurrent client requests contend with each other
# rather than actually running in parallel. _CALENDAR_TOTAL_BUDGET below is
# what actually bounds worst-case wall-clock time here, not concurrency.
_PER_CALENDAR_TIMEOUT = 10

# Hard ceiling on the whole calendar section of the briefing, regardless of
# how many calendars exist or how many are individually slow -- once this
# much time has been spent, remaining not-yet-queried calendars are skipped
# (same "contributes nothing this time" degradation as a single timed-out
# calendar) rather than trying all of them unconditionally.
_CALENDAR_TOTAL_BUDGET = 20

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


def _get_calendar_names():
    """(names, error_text). names is None if the calendar list itself
    couldn't be fetched (permission/timeout/other failure) -- check
    error_text in that case, a plain list (possibly empty) otherwise. This
    call alone is fast (no date-range "whose" filter involved), so it uses a
    short fixed timeout rather than _PER_CALENDAR_TIMEOUT.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", _CALENDAR_NAMES_SCRIPT],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None, "Calendar lookup timed out."

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not allowed" in stderr.lower() or "-1743" in stderr:
            return None, "Calendar access hasn't been granted yet (System Settings > Privacy & Security > Calendars)."
        return None, "Couldn't read the calendar."

    text = result.stdout.strip()
    if not text:
        return [], None
    return [name.strip() for name in text.split(",") if name.strip()], None


def _events_for_one_calendar(name: str, timeout: float = _PER_CALENDAR_TIMEOUT) -> str:
    """Today's events for a single named calendar, as osascript's natural
    comma-joined list-to-string coercion (possibly empty). Failures
    (including a timeout) return "" rather than propagating -- one
    problem calendar should degrade to "its events are missing", not take
    down the whole calendar section of the briefing. timeout is overridable
    so _get_todays_calendar_events can clamp it to whatever's left of the
    overall budget for the last calendar or two it attempts.
    """
    escaped = name.replace('"', '\\"')
    script = f'''
tell application "Calendar"
    set todayStart to current date
    set time of todayStart to 0
    set todayEnd to todayStart + 1 * days
    set eventList to {{}}
    set calEvents to (every event of calendar "{escaped}" whose start date ≥ todayStart and start date < todayEnd)
    repeat with ev in calEvents
        set end of eventList to (summary of ev as string) & " at " & (time string of (start date of ev))
    end repeat
    return eventList
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[Briefing] Calendar '{name}' timed out, skipping its events.")
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _get_todays_calendar_events() -> str:
    names, error = _get_calendar_names()
    if names is None:
        return error
    if not names:
        return "No events today."

    # Holiday-subscription calendars are the observed pathological case
    # (years of annually-recurring all-day entries make Calendar.app's
    # "whose" date filter extremely slow to evaluate) -- found live that
    # they happened to sit early enough in Calendar.app's own ordering that
    # the time budget below got spent on them before even reaching fast,
    # actually-relevant calendars like Work or Home. Trying non-holiday-
    # named calendars first means the budget is spent on calendars that are
    # both faster and more likely to matter for a daily briefing before it's
    # spent on ones that are neither. `sorted` is stable, so the relative
    # order within each group still matches Calendar.app's own ordering.
    names = sorted(names, key=lambda n: "holiday" in n.lower())

    results = []
    deadline = time.monotonic() + _CALENDAR_TOTAL_BUDGET
    for name in names:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            skipped = names[len(results):]
            print(f"[Briefing] Calendar time budget exhausted, skipping: {skipped}")
            break
        # Clamped to whatever's actually left so the last calendar attempted
        # can't blow past _CALENDAR_TOTAL_BUDGET on its own -- without this,
        # a calendar started just before the deadline could still run for
        # its full _PER_CALENDAR_TIMEOUT regardless of how little budget
        # remained.
        results.append(_events_for_one_calendar(name, timeout=min(_PER_CALENDAR_TIMEOUT, remaining)))

    events_text = ", ".join(text for text in results if text)
    return events_text if events_text else "No events today."


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


def _gather_briefing_inputs(config: dict):
    """Runs the calendar, reminders, and weather lookups concurrently —
    they're independent of each other, so running them one after another
    (as this originally did) only adds unnecessary wall-clock time, and the
    calendar lookup in particular can be slow (up to _OSASCRIPT_TIMEOUT).
    Returns (calendar_text, reminders_text, weather_text).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        calendar_future = executor.submit(_get_todays_calendar_events)
        reminders_future = executor.submit(_get_due_reminders)
        weather_future = executor.submit(_get_weather, config)
        return calendar_future.result(), reminders_future.result(), weather_future.result()


def prepare_briefing_text() -> str:
    """Gathers and composes the briefing text if conditions are met (see
    should_deliver_briefing), WITHOUT marking it delivered, broadcasting, or
    speaking it. Returns "" if conditions aren't met or gathering/
    composition failed — never None, so callers can treat the return value
    as a plain truthy/falsy check.

    Deliberately separate from delivery: call this as early as possible
    (e.g. from a background thread started right at process launch, before
    Whisper/Kokoro model loading and VAD calibration) so the gathering —
    two osascript calls, a weather API call, and one Claude call, all
    network/IO-bound — overlaps with the rest of app startup instead of
    adding to the wait after the app is already listening and the user
    could start talking. See spark_whisper_mic.py's module-level prefetch
    thread and its use of deliver_prepared_briefing below.
    """
    config = _read_config()
    if not should_deliver_briefing(config):
        return ""
    try:
        calendar_text, reminders_text, weather_text = _gather_briefing_inputs(config)
        text = _compose_briefing_text(calendar_text, reminders_text, weather_text)
    except Exception as e:
        print(f"[Briefing] Failed to compose morning briefing: {e}")
        return ""
    return text


def deliver_prepared_briefing(text: str, speak_fn=None) -> bool:
    """Marks today's briefing delivered, broadcasts it, and speaks it. Call
    with the (non-empty) result of prepare_briefing_text() — skip entirely
    if that returned "". Re-checks should_deliver_briefing first so text
    prepared early doesn't get delivered twice if something else already
    delivered today's briefing in the meantime (the two trigger sites in
    spark_whisper_mic.py can otherwise race).
    """
    config = _read_config()
    if not should_deliver_briefing(config):
        return False

    # Marked done before broadcasting/speaking (not after) so a failure in
    # either doesn't leave the briefing re-attempting later today — matches
    # prepare_briefing_text's own best-effort-once, not
    # best-effort-until-it-works, stance on its half of the work.
    config["last_briefing_date"] = _today_str()
    _write_config(config)

    # Broadcast before speaking, not after: speak_fn blocks until TTS
    # playback actually finishes (many seconds for a multi-sentence
    # briefing), and the panel should show the text as it starts being
    # spoken, not only once it's already fully done.
    ws_server.broadcast(protocol.briefing_message(text))
    if speak_fn:
        speak_fn(text)
    return True


def maybe_deliver_briefing(speak_fn=None) -> bool:
    """All-in-one convenience entry point: gather, compose, mark delivered,
    broadcast, and speak, in one call. Used by the "first real utterance of
    the day" fallback trigger, where there's no earlier point to prefetch
    from. For the app-launch trigger, prefer prepare_briefing_text() (called
    as early as possible) + deliver_prepared_briefing(), so the gathering
    overlaps with other startup work instead of stacking after it.
    """
    text = prepare_briefing_text()
    if not text:
        return False
    return deliver_prepared_briefing(text, speak_fn=speak_fn)
