import datetime
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import briefing


class _FakeDate:
    @staticmethod
    def today():
        return datetime.date(2026, 7, 12)


class _FakeDateTime:
    @staticmethod
    def now():
        return datetime.datetime(2026, 7, 12, 8, 0)


class _FakeDatetimeModule:
    date = _FakeDate
    datetime = _FakeDateTime


def _freeze_time(monkeypatch, hour=8):
    class _DT:
        @staticmethod
        def now():
            return datetime.datetime(2026, 7, 12, hour, 0)

    class _Mod:
        date = _FakeDate
        datetime = _DT

    monkeypatch.setattr(briefing, "datetime", _Mod)


def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing, "CONFIG_PATH", str(tmp_path / "config.json"))


# --- config read/write -------------------------------------------------------


def test_read_config_returns_defaults_when_missing(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    config = briefing._read_config()
    assert config == briefing._DEFAULT_CONFIG


def test_write_then_read_config_roundtrips(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    briefing._write_config({"briefing_enabled": False, "last_briefing_date": "2026-07-11", "location": None})
    config = briefing._read_config()
    assert config["briefing_enabled"] is False
    assert config["last_briefing_date"] == "2026-07-11"


def test_read_config_survives_corrupt_file(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    os.makedirs(tmp_path, exist_ok=True)
    with open(briefing.CONFIG_PATH, "w") as f:
        f.write("not valid json{{{")
    assert briefing._read_config() == briefing._DEFAULT_CONFIG


# --- should_deliver_briefing --------------------------------------------------


def test_should_deliver_when_enabled_not_yet_briefed_and_past_hour(monkeypatch):
    _freeze_time(monkeypatch, hour=8)
    config = {"briefing_enabled": True, "last_briefing_date": None, "location": None}
    assert briefing.should_deliver_briefing(config) is True


def test_should_not_deliver_when_disabled(monkeypatch):
    _freeze_time(monkeypatch, hour=8)
    config = {"briefing_enabled": False, "last_briefing_date": None, "location": None}
    assert briefing.should_deliver_briefing(config) is False


def test_should_not_deliver_when_already_briefed_today(monkeypatch):
    _freeze_time(monkeypatch, hour=8)
    config = {"briefing_enabled": True, "last_briefing_date": "2026-07-12", "location": None}
    assert briefing.should_deliver_briefing(config) is False


def test_should_deliver_when_last_briefing_was_a_different_day(monkeypatch):
    _freeze_time(monkeypatch, hour=8)
    config = {"briefing_enabled": True, "last_briefing_date": "2026-07-11", "location": None}
    assert briefing.should_deliver_briefing(config) is True


def test_should_not_deliver_before_min_hour(monkeypatch):
    _freeze_time(monkeypatch, hour=3)
    config = {"briefing_enabled": True, "last_briefing_date": None, "location": None}
    assert briefing.should_deliver_briefing(config) is False


# --- osascript wrapper ---------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_osascript_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="Standup at 9:30am"),
    )
    result = briefing._run_osascript("script", "denied", "empty", "timeout", "error")
    assert result == "Standup at 9:30am"


def test_run_osascript_returns_empty_text_for_blank_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="   "))
    result = briefing._run_osascript("script", "denied", "empty", "timeout", "error")
    assert result == "empty"


def test_run_osascript_detects_permission_denial(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=1, stderr="Not authorized (-1743)"),
    )
    result = briefing._run_osascript("script", "denied", "empty", "timeout", "error")
    assert result == "denied"


def test_run_osascript_handles_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=45)

    monkeypatch.setattr(subprocess, "run", _raise)
    result = briefing._run_osascript("script", "denied", "empty", "timeout", "error")
    assert result == "timeout"


def test_run_osascript_generic_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=1, stderr="something else went wrong"),
    )
    result = briefing._run_osascript("script", "denied", "empty", "timeout", "error")
    assert result == "error"


# --- calendar (per-calendar, parallel) -------------------------------------------


def test_get_calendar_names_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="Work, Home, Holidays"),
    )
    names, error = briefing._get_calendar_names()
    assert names == ["Work", "Home", "Holidays"]
    assert error is None


def test_get_calendar_names_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="   "))
    names, error = briefing._get_calendar_names()
    assert names == []
    assert error is None


def test_get_calendar_names_permission_denied(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=1, stderr="Not authorized (-1743)"),
    )
    names, error = briefing._get_calendar_names()
    assert names is None
    assert "Calendars" in error


def test_get_calendar_names_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=10)

    monkeypatch.setattr(subprocess, "run", _raise)
    names, error = briefing._get_calendar_names()
    assert names is None
    assert error == "Calendar lookup timed out."


def test_events_for_one_calendar_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _FakeCompletedProcess(returncode=0, stdout="Standup at 9:30 AM"),
    )
    assert briefing._events_for_one_calendar("Work") == "Standup at 9:30 AM"


def test_events_for_one_calendar_timeout_returns_empty_not_raises(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=15)

    monkeypatch.setattr(subprocess, "run", _raise)
    # A single pathological calendar (e.g. a holiday subscription with years
    # of recurring entries -- found live) must degrade to "no events from
    # this one" rather than raising and taking down the whole lookup.
    assert briefing._events_for_one_calendar("Holidays in India") == ""


def test_events_for_one_calendar_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(returncode=1, stderr="boom"))
    assert briefing._events_for_one_calendar("Work") == ""


def test_get_todays_calendar_events_combines_calendars_and_skips_slow_ones(monkeypatch):
    monkeypatch.setattr(briefing, "_get_calendar_names", lambda: (["Work", "Holidays in India", "Home"], None))

    def _fake_events(name, timeout=None):
        return {
            "Work": "Standup at 9:30 AM",
            "Holidays in India": "",  # simulates a timeout/skip
            "Home": "Dentist at 2:00 PM",
        }[name]

    monkeypatch.setattr(briefing, "_events_for_one_calendar", _fake_events)

    text = briefing._get_todays_calendar_events()

    assert "Standup at 9:30 AM" in text
    assert "Dentist at 2:00 PM" in text
    # The timed-out calendar simply contributes nothing -- not an error for
    # the whole lookup, unlike the old single combined-query approach.
    assert "Holidays" not in text


def test_get_todays_calendar_events_no_events_anywhere(monkeypatch):
    monkeypatch.setattr(briefing, "_get_calendar_names", lambda: (["Work", "Home"], None))
    monkeypatch.setattr(briefing, "_events_for_one_calendar", lambda name, timeout=None: "")

    assert briefing._get_todays_calendar_events() == "No events today."


def test_get_todays_calendar_events_no_calendars_at_all(monkeypatch):
    monkeypatch.setattr(briefing, "_get_calendar_names", lambda: ([], None))

    assert briefing._get_todays_calendar_events() == "No events today."


def test_get_todays_calendar_events_propagates_names_error(monkeypatch):
    monkeypatch.setattr(
        briefing, "_get_calendar_names",
        lambda: (None, "Calendar access hasn't been granted yet (System Settings > Privacy & Security > Calendars)."),
    )

    assert briefing._get_todays_calendar_events() == (
        "Calendar access hasn't been granted yet (System Settings > Privacy & Security > Calendars)."
    )


def test_get_todays_calendar_events_processes_sequentially_not_concurrently(monkeypatch):
    # Found live: concurrent per-calendar queries actively hurt (Calendar.app
    # appears to serialize Apple Events internally, so concurrent client
    # requests contend with each other and even normally-fast calendars
    # started timing out). This locks in "sequential" as a regression guard.
    monkeypatch.setattr(briefing, "_get_calendar_names", lambda: (["A", "B", "C"], None))
    order = []

    def _fake_events(name, timeout=None):
        order.append(name)
        return f"event from {name}"

    monkeypatch.setattr(briefing, "_events_for_one_calendar", _fake_events)

    briefing._get_todays_calendar_events()

    assert order == ["A", "B", "C"]  # strictly in order, one at a time


def test_get_todays_calendar_events_stops_once_time_budget_exhausted(monkeypatch):
    monkeypatch.setattr(briefing, "_get_calendar_names", lambda: (["Fast", "Slow", "NeverReached"], None))
    monkeypatch.setattr(briefing, "_CALENDAR_TOTAL_BUDGET", 0.05)

    def _fake_events(name, timeout=None):
        if name == "Slow":
            time.sleep(0.1)  # pushes elapsed time past the budget
        return f"event from {name}"

    monkeypatch.setattr(briefing, "_events_for_one_calendar", _fake_events)

    text = briefing._get_todays_calendar_events()

    assert "Fast" in text
    assert "Slow" in text
    # Budget was exhausted by the time "Slow" finished -- "NeverReached" is
    # never even attempted, not just timed out.
    assert "NeverReached" not in text


# --- location / weather --------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_get_location_caches_after_first_lookup(monkeypatch):
    import requests

    calls = []

    def _fake_get(url, **kwargs):
        calls.append(url)
        return _FakeHTTPResponse({"latitude": 42.0, "longitude": -71.0, "city": "Boston"})

    monkeypatch.setattr(requests, "get", _fake_get)
    config = {"briefing_enabled": True, "last_briefing_date": None, "location": None}
    monkeypatch.setattr(briefing, "_write_config", lambda c: None)

    first = briefing._get_location(config)
    second = briefing._get_location(config)

    assert first == {"lat": 42.0, "lon": -71.0, "place": "Boston"}
    assert second == first
    assert len(calls) == 1  # second call served from the now-populated config, no new request


def test_get_location_returns_none_on_failure(monkeypatch):
    import requests

    def _raise(*a, **kw):
        raise ConnectionError("no network")

    monkeypatch.setattr(requests, "get", _raise)
    config = {"briefing_enabled": True, "last_briefing_date": None, "location": None}
    assert briefing._get_location(config) is None


def test_get_weather_formats_current_and_forecast(monkeypatch):
    import requests

    monkeypatch.setattr(briefing, "_get_location", lambda config: {"lat": 42.0, "lon": -71.0, "place": "Boston"})

    def _fake_get(url, **kwargs):
        return _FakeHTTPResponse({
            "current": {"temperature_2m": 68.4, "weather_code": 1},
            "daily": {"temperature_2m_max": [72.1], "temperature_2m_min": [55.6]},
        })

    monkeypatch.setattr(requests, "get", _fake_get)
    text = briefing._get_weather({})
    assert "68" in text
    assert "mostly clear" in text
    assert "Boston" in text
    assert "72" in text and "56" in text


def test_get_weather_unavailable_when_location_missing(monkeypatch):
    monkeypatch.setattr(briefing, "_get_location", lambda config: None)
    assert briefing._get_weather({}) == "Weather unavailable."


def test_get_weather_unavailable_on_request_failure(monkeypatch):
    import requests

    monkeypatch.setattr(briefing, "_get_location", lambda config: {"lat": 1, "lon": 1, "place": "X"})
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    assert briefing._get_weather({}) == "Weather unavailable."


# --- composition ---------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


def _fake_client(response):
    class _FakeMessages:
        def create(self, **kwargs):
            return response

    class _FakeClient:
        messages = _FakeMessages()

    return _FakeClient()


def test_compose_briefing_text_returns_stripped_text(monkeypatch):
    monkeypatch.setattr(briefing, "_get_client", lambda: _fake_client(_FakeResponse("  Good morning!  ")))
    text = briefing._compose_briefing_text("no events", "no reminders", "sunny")
    assert text == "Good morning!"


def test_compose_briefing_text_empty_on_refusal(monkeypatch):
    monkeypatch.setattr(briefing, "_get_client", lambda: _fake_client(_FakeResponse("", stop_reason="refusal")))
    text = briefing._compose_briefing_text("no events", "no reminders", "sunny")
    assert text == ""


# --- _gather_briefing_inputs (concurrent) ---------------------------------------


def test_gather_briefing_inputs_runs_all_three_and_returns_in_order(monkeypatch):
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: "Standup at 9:30am")
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "No reminders due.")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "Sunny, 70F.")

    calendar_text, reminders_text, weather_text = briefing._gather_briefing_inputs({})

    assert calendar_text == "Standup at 9:30am"
    assert reminders_text == "No reminders due."
    assert weather_text == "Sunny, 70F."


def test_gather_briefing_inputs_propagates_a_failure_from_any_one(monkeypatch):
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "y")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "z")

    try:
        briefing._gather_briefing_inputs({})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass


# --- prepare_briefing_text / deliver_prepared_briefing (split API) --------------


def test_prepare_briefing_text_noop_when_conditions_not_met(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=3)  # before MIN_BRIEFING_HOUR
    calls = []
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: calls.append("calendar") or "x")

    text = briefing.prepare_briefing_text()

    assert text == ""
    assert calls == []  # never even started gathering
    # No side effects on config either -- nothing was attempted.
    assert briefing._read_config()["last_briefing_date"] is None


def test_prepare_briefing_text_success(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: "Standup at 9:30am")
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "No reminders due.")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "Sunny, 70F.")
    monkeypatch.setattr(briefing, "_compose_briefing_text", lambda c, r, w: "Good morning!")

    text = briefing.prepare_briefing_text()

    assert text == "Good morning!"
    # Preparing does not mark the day as briefed or broadcast/speak anything
    # -- that's deliver_prepared_briefing's job, so a prefetched-but-not-yet-
    # delivered text doesn't silently consume today's one briefing.
    assert briefing._read_config()["last_briefing_date"] is None


def test_prepare_briefing_text_empty_on_gathering_failure_and_does_not_mark_done(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: (_ for _ in ()).throw(RuntimeError("osascript exploded")))
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "y")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "z")

    text = briefing.prepare_briefing_text()

    assert text == ""
    # Not marked as attempted -- a gather failure allows a retry later today
    # via the fallback trigger, rather than permanently skipping the day.
    assert briefing._read_config()["last_briefing_date"] is None


def test_deliver_prepared_briefing_success(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    broadcasts = []
    monkeypatch.setattr(briefing.ws_server, "broadcast", lambda msg: broadcasts.append(msg))
    spoken = []

    delivered = briefing.deliver_prepared_briefing("Good morning!", speak_fn=lambda t: spoken.append(t))

    assert delivered is True
    assert spoken == ["Good morning!"]
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "briefing"
    assert broadcasts[0]["text"] == "Good morning!"
    assert briefing._read_config()["last_briefing_date"] == "2026-07-12"


def test_deliver_prepared_briefing_skips_if_already_delivered(monkeypatch, tmp_path):
    # Guards the prefetch-thread race: text prepared early shouldn't be
    # delivered twice if the other trigger site already delivered today's
    # briefing in the meantime.
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    briefing._write_config({"briefing_enabled": True, "last_briefing_date": "2026-07-12", "location": None})
    spoken = []

    delivered = briefing.deliver_prepared_briefing("Good morning!", speak_fn=lambda t: spoken.append(t))

    assert delivered is False
    assert spoken == []


# --- maybe_deliver_briefing (all-in-one convenience wrapper) --------------------


def test_maybe_deliver_briefing_noop_when_conditions_not_met(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=3)  # before MIN_BRIEFING_HOUR
    calls = []
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: calls.append("calendar") or "x")

    delivered = briefing.maybe_deliver_briefing(speak_fn=lambda t: calls.append(("speak", t)))

    assert delivered is False
    assert calls == []  # never even started gathering


def test_maybe_deliver_briefing_full_success(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: "Standup at 9:30am")
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "No reminders due.")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "Sunny, 70F.")
    monkeypatch.setattr(briefing, "_compose_briefing_text", lambda c, r, w: "Good morning! You have standup at 9:30, it's sunny and 70.")

    broadcasts = []
    monkeypatch.setattr(briefing.ws_server, "broadcast", lambda msg: broadcasts.append(msg))
    spoken = []

    delivered = briefing.maybe_deliver_briefing(speak_fn=lambda t: spoken.append(t))

    assert delivered is True
    assert spoken == ["Good morning! You have standup at 9:30, it's sunny and 70."]
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "briefing"
    # Marked done for today so a second call in the same run is a no-op.
    assert briefing._read_config()["last_briefing_date"] == "2026-07-12"


def test_maybe_deliver_briefing_does_not_mark_done_if_gathering_fails(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: (_ for _ in ()).throw(RuntimeError("osascript exploded")))
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "y")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "z")

    delivered = briefing.maybe_deliver_briefing(speak_fn=lambda t: None)

    assert delivered is False
    # Not marked as attempted -- allows a retry later today rather than
    # permanently skipping it (see prepare_briefing_text's own test above).
    assert briefing._read_config()["last_briefing_date"] is None


def test_maybe_deliver_briefing_second_call_same_day_is_noop(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _freeze_time(monkeypatch, hour=8)
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: "x")
    monkeypatch.setattr(briefing, "_get_due_reminders", lambda: "y")
    monkeypatch.setattr(briefing, "_get_weather", lambda config: "z")
    monkeypatch.setattr(briefing, "_compose_briefing_text", lambda c, r, w: "Morning!")
    monkeypatch.setattr(briefing.ws_server, "broadcast", lambda msg: None)

    first = briefing.maybe_deliver_briefing(speak_fn=lambda t: None)
    calls = []
    monkeypatch.setattr(briefing, "_get_todays_calendar_events", lambda: calls.append(1) or "x")
    second = briefing.maybe_deliver_briefing(speak_fn=lambda t: None)

    assert first is True
    assert second is False
    assert calls == []
