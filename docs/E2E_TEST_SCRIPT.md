# Spark End-to-End Test Script

Manual test pass covering everything shipped through Phase 4 (protocol v2,
SQLite persistence, Kokoro streaming TTS + tap-to-interrupt, orb UI, agentic
confirmation gates). Run in order — later sections assume the app is already
up and a session exists.

## Setup

```bash
cd /Users/kamisettyvivek/Documents/Spark/Spark-Voice-Assistant
pkill -9 -f "Electron" 2>/dev/null; pkill -9 -f "spark_whisper_mic.py" 2>/dev/null
npm run start
```

`main.js` auto-spawns the Python backend (`whisper-env/bin/python spark_whisper_mic.py`)
— you don't need to launch it separately. Watch the terminal for
`[Frontend]`/`[Spark]`/`[Claude]` log lines throughout; most assertions below
are "does the log line + UI agree."

Have a **Downloads folder with at least one file** and **Calendar/Reminders
with at least one item** for the tool-chaining and osascript tests below.

---

## 1. Launch & lifecycle

| # | Action | Expected |
|---|---|---|
| 1.1 | Cold launch | Orb window appears, compact size, state settles to idle (no crash in terminal log) |
| 1.2 | Quit the app (Cmd+Q or window close) | Terminal log shows the backend process actually exit — no orphaned `spark_whisper_mic.py` left behind. Confirm with `pgrep -fl spark_whisper_mic` after quitting: should be empty |

## 2. Basic voice loop

| # | Action | Expected |
|---|---|---|
| 2.1 | Say "What's today's date?" | Orb goes listening (green, mic-RMS-reactive) → thinking (blue, swirl) → speaking (yellow, TTS-RMS-reactive). Panel unfolds showing your transcript (sans-serif) and Spark's reply (serif) |
| 2.2 | Say something long enough to require Whisper to catch a full sentence (e.g. "Can you tell me a fun fact about space") | First audible word arrives quickly (~1.5s target from Phase 2) — reply isn't fully generated before speech starts |
| 2.3 | Ask a follow-up referring to the previous answer ("say that again slower") | Claude's reply shows it has the prior turn in context |

## 3. Orb + panel UI

| # | Action | Expected |
|---|---|---|
| 3.1 | Watch orb during idle | Slow breathing pulse, no color change |
| 3.2 | Watch orb while actually speaking into the mic at varying volume | Displacement/amplitude visibly tracks your volume in real time, not just on/off |
| 3.3 | Watch orb while Spark is speaking | Pulse tracks Spark's own TTS output amplitude |
| 3.4 | Ask a question, then stay silent | Panel auto-collapses after ~8s idle |
| 3.5 | Ask something that returns a list/table-shaped answer (e.g. "what files are in my Downloads folder") | Reply headline is short spoken-friendly text; `---DETAIL---` section renders as real markdown (table/list) in the panel, not read aloud verbatim |
| 3.6 | Click the **Copy** button after a reply | Spark's last reply is on the clipboard |
| 3.7 | Ask something that triggers a tool call | A muted tool-activity line appears while the tool runs ("Running: ...", "Searching for ...") and disappears when done |
| 3.8 | Type into the `#text-input` field instead of speaking, press Enter | Same rendering path as voice — appears in transcript, gets a reply |
| 3.9 | Tap the stop button (`#stop-button`) while Spark is mid-sentence | TTS stops immediately (tap-to-interrupt); note voice barge-in (talking over Spark without tapping) is explicitly out of scope per Phase 2 — don't test that as a bug |
| 3.10 | Resize / check vibrancy | Window shows real macOS vibrancy (blurred desktop visible behind the panel), rounded corners |

## 4. Session persistence (Phase 1)

| # | Action | Expected |
|---|---|---|
| 4.1 | Have a short exchange, e.g. tell Spark "my favorite color is teal" | Normal reply |
| 4.2 | Quit Spark fully, relaunch | New session starts |
| 4.3 | Ask "what's my favorite color?" or "what were we just talking about?" | Answers correctly from restored history (loaded from `~/.spark/spark.db`, last 20 messages) |
| 4.4 (optional) | Inspect DB directly: `sqlite3 ~/.spark/spark.db "select role,content from messages order by id desc limit 5;"` | Rows match what you just said/heard |

## 5. Confirmation gates (Phase 4) — the main new surface

Safe commands (from `risk_classifier.SAFE_COMMANDS`: `ls, pwd, cat, echo, date,
whoami, uname, hostname, ps, df, du, top, uptime, sw_vers, sysctl, find, grep,
head, tail, wc, file, stat, which, type, env, printenv, id, groups, ping`)
auto-run with **no** confirmation. Everything else — including any of those
same commands combined with `| ; & \` $ < >` — requires confirmation.

| # | Action | Expected |
|---|---|---|
| 5.1 | "What's the current date?" (→ `date`) | Runs instantly, no confirmation prompt, no chip |
| 5.2 | "Ping google.com once" (→ `ping`, safe base command) | Runs instantly, no confirmation |
| 5.3 | Ask for something that maps to an unlisted command, e.g. "what's my current battery percentage" (likely `pmset` or similar, not in the allowlist) | Spark speaks + panel shows a `confirmation_request` chip ("Should I run this: ...") styled high-risk/red-tinted. State goes to `awaiting_confirmation` (distinct orb color) |
| 5.4 | Resolve 5.3 by **clicking the "Yes" chip** | Command runs, result comes back, chip disappears (`confirmation_resolved`) |
| 5.5 | Repeat 5.3, this time resolve by **saying "Yes" (or "go ahead" / "sure" / "okay, go ahead")** out loud | Voice resolves it exactly like the chip — confirm mic is NOT near-silent during this window (Phase 4's mute-deadlock fix); a real spoken yes should transcribe correctly |
| 5.6 | Repeat 5.3, resolve by **saying "No"** | Command is NOT executed; Spark's tool result should reflect "not run — user did not confirm" |
| 5.7 | Repeat 5.3, then **say nothing for 30+ seconds** | Times out, auto-denies (same as declining), no command runs |
| 5.8 | Try a command that chains/redirects even though the base looks safe, e.g. "list my downloads folder and save the output to a file called out.txt" (→ `ls ... > out.txt`) or "run ls and then echo done" (`;`/`&&`) | Requires confirmation even though `ls`/`echo` alone are on the allowlist — this is the "smuggled risk via shell syntax" case from `risk_classifier.RISKY_SHELL_SYNTAX` |
| 5.9 | Ask Spark to read today's Calendar events (→ `osascript`, read-only) | Auto-runs without confirmation (narrower mutating-verb check for osascript, not a blanket requirement) |
| 5.10 | Ask Spark to **create** a Reminder or Calendar event (→ `osascript` with `make`/`set`/`save`) | Requires confirmation — mutating AppleScript verb detected |
| 5.11 | Say "clear history" | Confirmation flow triggers (same general gate, not the old hardcoded one) before anything is wiped; decline it once to confirm nothing is deleted, then confirm it and verify history is actually gone (repeat test 4.3's question afterward — should no longer know the fact) |
| 5.12 | While a confirmation is pending, say something unrelated, e.g. "thank you" or random ambient chatter | Ignored — logged as unrelated input, confirmation stays pending (not treated as an implicit decline). Verify by then actually answering "yes" afterward and having it still resolve correctly |
| 5.13 | Ask for something clearly destructive, e.g. "delete everything in my Downloads folder" | Triggers confirmation; decline it (say "no") and verify via `ls ~/Downloads` that nothing was actually deleted |

## 5b. Semantic memory (Phase 5)

| # | Action | Expected |
|---|---|---|
| 5b.1 | "Remember that my favorite color is teal" | `remember_this` tool runs with **no** confirmation prompt (memory writes are low-risk); Spark confirms verbally |
| 5b.2 | Later in the same session, ask "what color do I like best?" (different wording) | Answers correctly from the stored memory (semantic retrieval, not keyword match) |
| 5b.3 | "Forget my favorite color" | Triggers a confirmation (low-risk styled, not the red high-risk styling from §5) before deleting — decline once and verify `sqlite3 ~/.spark/spark.db "select content from memories;"` still shows it, then confirm and verify it's gone |
| 5b.4 | Mention a fact in passing without asking Spark to remember it (e.g. "I'm making soup for my roommate Priya, she's sick") | No `remember_this` tool call this turn — but quit the app cleanly afterward (Cmd+Q or SIGTERM) and check `sqlite3 ~/.spark/spark.db "select content from memories;"` — the fact should appear, picked up by the session-end extraction pass |
| 5b.5 | Restart the app, ask about a previously remembered fact in different wording | Answers correctly — this is Phase 5's actual acceptance test (persists across process restart, not just within a session) |
| 5b.6 | Have a long conversation (15+ turns) in one sitting | A periodic extraction pass should fire mid-session (check terminal log / DB for new memory rows) without waiting for app quit |

## 6. Multi-step tool chaining

| # | Action | Expected |
|---|---|---|
| 6.1 | "Find my resume and copy it to the Desktop" (adjust filename to something real on your machine) | Multiple tool calls chain (search_files → execute_shell_command for the copy, which will itself need confirmation per §5) without you having to prompt each step separately |
| 6.2 | Ask something that would require many back-and-forth tool attempts to fail (hard to force deliberately — optional) | After `MAX_TOOL_ITERATIONS = 8`, Spark gracefully says it needed more steps than expected rather than looping forever |

## 7. Screenshot / visual Q&A (if Screen Recording permission granted to the Electron/terminal process)

| # | Action | Expected |
|---|---|---|
| 7.1 | Have something on screen, ask "what's on my screen right now?" | Screenshot captured, Claude describes it accurately |
| 7.2 | If permission was never granted | Spark tells you to enable Screen Recording in System Settings rather than claiming it can never see the screen |

## 8. Regression sweep (quick)

- [ ] `open_application`: "open Safari" / "open Spotify" — runs with no confirmation gate (not shell-classified), opens correctly, or reports a clear failure if the app name doesn't resolve.
- [ ] Markdown/code rendering: ask for a short code snippet, verify syntax highlighting in the panel.
- [ ] `search_files`: "find all PDFs in my Documents folder" — returns real matches, capped list.
- [ ] Numbered-list replies render correctly (not corrupted at "1." — the `sentence_splitter.py` fix).

---

## Automated backend check (run first, cheap)

```bash
cd backend && ./whisper-env/bin/python -m pytest tests/ -q
```

Should be 42/42 passing before doing any of the manual voice pass above — if
this fails, fix it before burning time on live testing.

## Cleanup after testing

```bash
pkill -9 -f "Electron" 2>/dev/null
pkill -9 -f "spark_whisper_mic.py" 2>/dev/null
```

Delete any ad-hoc screenshots/log files left in the parent `Spark/` directory
(never inside the repo) — check with:
```bash
ls /Users/kamisettyvivek/Documents/Spark/*.png /tmp/spark_*.log 2>/dev/null
```
