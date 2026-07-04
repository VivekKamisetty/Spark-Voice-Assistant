# Spark v2 — Implementation Spec

Work through phases in order; each phase is independently shippable and has acceptance criteria. Do not start a phase until the previous one passes its criteria.

## Current architecture (context)

* Repo: `github.com/VivekKamisetty/Spark-Voice-Assistant`
* Backend: Python (`whisper-env` venv, Python 3.11). Pipeline: VAD → Whisper `small.en` (local) → keyword/LLM routing → Claude tool-use client (`backend/claude_client.py`) → pyttsx3 TTS. Self-transcription loop already fixed via `should_listen` flag + audio queue clearing.
* Frontend: Electron/Node.js. Floating always-on-top status bubble (green=listening, blue=thinking, yellow=speaking) + popup panel with markdown/code rendering, draggable/resizable with localStorage persistence. Backend auto-launches with the Electron app.
* Existing tools: `execute_shell_command` (shell/AppleScript), `search_files`, `open_application`, screen-capture visual Q&A triggered by phrases.
* **Communication: none currently — file polling.** The Python backend writes a single JSON snapshot (`public/spark_output.json`) via `bridge.py` on every state change; the Electron renderer polls that file every 500ms (`setInterval` + `fs.readFile`) and diffs it against the last-seen value. There is no live channel and no way for the frontend to send anything back to the backend today. **Phase 0 must build a real WebSocket transport from scratch on both ends**, then define the versioned protocol on top of it — this is a bigger phase than "add versioning to an existing socket."
* Known gaps: no persistence across restarts, no proactivity, hardcoded tool list, wake word module commented out in `run_spark.py`, hardcoded mic device name (`"MacBook Pro Microphone"`) with no config/fallback.

## Non-goals (do not build)

* Plugin/extensibility system
* Full computer use (synthetic clicks/keystrokes)
* Multi-model routing or local LLMs
* Windows/Linux support — macOS only

## Phase 0 — WebSocket protocol v2 (foundation for everything else)

Define a single versioned JSON message protocol; build the transport and implement it on both sides before adding features.

Backend → Frontend messages:

```json
{ "type": "state",      "value": "idle|listening|thinking|speaking|awaiting_confirmation" }
{ "type": "amplitude",  "source": "mic|tts", "rms": 0.0 }          // 20–30 Hz while active
{ "type": "transcript", "role": "user", "text": "...", "partial": true }
{ "type": "assistant_chunk", "text": "..." }                        // streamed reply tokens
{ "type": "assistant_done" }
{ "type": "tool_activity", "name": "search_files", "status": "running|done|error", "summary": "..." }
{ "type": "confirmation_request", "id": "uuid", "prompt": "...", "risk": "low|high", "options": ["Yes","No"] }
{ "type": "briefing", "text": "..." }
```

Frontend → Backend messages:

```json
{ "type": "confirmation_response", "id": "uuid", "choice": "Yes" }
{ "type": "interrupt" }              // user barge-in or clicked stop
{ "type": "text_input", "text": "..." }   // typed fallback input
```

Requirements:

* All messages include `"v": 2`. Unknown message types must be ignored gracefully on both sides.
* Amplitude: compute RMS from the same audio buffers VAD already consumes; throttle to ≤30 Hz; frontend lerps between values.

Acceptance: frontend state bubble driven entirely by `state` messages; amplitude messages visible in devtools console while speaking into the mic; no regression in existing voice loop.

## Phase 1 — Backend: session persistence (SQLite)

* Add `backend/store.py` using stdlib `sqlite3`, DB at `~/.spark/spark.db`.
* Tables:
  * `sessions(id, started_at, ended_at)`
  * `messages(id, session_id, role, content, created_at)` — persist every user/assistant turn and tool call summaries.
  * `memories(id, content, source_session_id, created_at, embedding BLOB)` — used in Phase 5, create schema now.
* On launch: start a new session, load the last N=20 messages across recent sessions into the rolling context window (keep existing 20-message cap).
* Add a voice command handler: "clear history" wipes messages after spoken confirmation (route through Phase 4 confirmation gate once it exists; simple yes/no until then).

Acceptance: quit and relaunch Spark; ask "what were we just talking about?" and it answers correctly from restored history.

## Phase 2 — Backend: neural TTS + streaming speech + barge-in

Replace pyttsx3. This unlocks both perceived quality and the frontend orb's speaking animation.

* Integrate Kokoro TTS (local, Apple Silicon-friendly). Wrap behind a `TTSEngine` interface (`speak_stream(text_iter)`, `stop()`) so the engine is swappable; keep a pyttsx3 fallback implementation behind the same interface.
* Streaming: as Claude's response streams, split on sentence boundaries and synthesize/play sentence-by-sentence so speech starts before generation finishes. Forward the same text chunks to the frontend as `assistant_chunk`.
* TTS amplitude: while playing synthesized audio, compute RMS of the output buffer and emit `amplitude {source: "tts"}` messages.
* Barge-in: while state=speaking, keep VAD active. If sustained user speech is detected (use a higher threshold + ~300 ms sustain to avoid echo false-positives), immediately `stop()` TTS, clear the audio queue (reuse existing `should_listen` machinery), transition to listening, and process the new utterance. Also handle an explicit `interrupt` message from the frontend.
* **Known risk (flagged during review):** voice barge-in with an open mic during speaker playback is the hardest problem in this spec — this project's prior debugging arc was specifically about Spark's mic hearing its own *muted-during-playback* TTS voice; Phase 2 removes that mute entirely. There's no proper echo cancellation available via `sounddevice` on macOS. Threshold/sustain heuristics alone may not hold up on speakers (vs. headphones). Decide before starting: scope the acceptance test to headphones, invest in real AEC, or treat voice barge-in as best-effort with the `interrupt` message (tap-to-interrupt) as the reliable fallback.

Acceptance: first audible word within ~1.5 s of Claude starting to respond on a long answer; speaking Spark can be interrupted mid-sentence by talking over it and it responds to the new utterance.

## Phase 3 — Frontend: the orb UI

Full redesign of the Electron renderer. Two visual states: compact orb, and expanded conversation panel that unfolds beneath the orb.

Window:

* `new BrowserWindow({ vibrancy: 'under-window', transparent: true, frame: false, roundedCorners: true, alwaysOnTop: true, visibleOnAllWorkspaces: true })`. Keep existing drag + position persistence.

Orb (centerpiece):

* Audio-reactive blob on a WebGL canvas: simplex-noise-displaced circle rendered with a fragment shader (raw WebGL or three.js; no full scene graph needed). Acceptable v1 fallback: layered 2D canvas circles with noise-perturbed radius.
* Driven by protocol messages: idle = slow breathing pulse; listening = displacement amplitude tied to mic RMS; thinking = slow internal swirl/rotation; speaking = pulse tied to TTS RMS; awaiting_confirmation = steady attention state.
* Color per state (keep current semantics: green listening, blue thinking, yellow speaking; pick a distinct color for awaiting_confirmation). Smooth-lerp both color and amplitude between messages — never snap.

Conversation panel:

* Unfolds beneath the orb when a user transcript begins; collapses after ~8 s of idle (configurable). Spring-physics transitions (Framer Motion if React, otherwise CSS spring easing), 200–400 ms.
* Live transcript: user words appear as spoken (render `partial` transcripts, replace with final); assistant reply streams from `assistant_chunk` with a blinking cursor while incomplete.
* Typography: sans-serif for the user, serif for Spark's replies. Keep existing markdown + syntax-highlighted code rendering and copy button.
* Tool activity line: while a tool runs, show a single muted line ("Searching files…") from `tool_activity` messages.
* Quick-action chips: when a `confirmation_request` arrives, render its options as tappable chips (high-risk styled red-tinted). Chip click sends `confirmation_response`; a spoken "yes/no" must resolve the same request (backend owns reconciliation — first response wins).
* Typed input fallback: small text field in the panel sending `text_input`.

Acceptance: orb visibly reacts to voice volume in real time while listening and to Spark's own speech while speaking; panel unfolds/collapses smoothly; chips and voice both resolve confirmations; window shows real macOS vibrancy (blurred desktop behind it).

## Phase 4 — Backend: agentic loop + confirmation gates

Refactor `claude_client.py` from single-shot tool calls into a proper agent loop.

* Loop: send request → if response contains tool_use blocks, execute tools, append tool_result blocks, re-call the API → repeat until a final text response or `max_iterations = 8`. Emit `tool_activity` messages around each execution.
* Use the Claude API with streaming and tool definitions (Anthropic Python SDK; pick current model per https://docs.claude.com/en/api/overview — verify model string in docs rather than hardcoding from memory).
* Risk classification for `execute_shell_command`: before executing, classify the command locally. **Consider an allowlist-first model instead of a blocklist** (raised during review): a blocklist of patterns (`rm`, `sudo`, redirection, `kill`, messaging `osascript`, `~/Library`/system paths = high risk) is inherently incomplete — things like `curl | sh`, `chmod`, `launchctl`, a forced `git push`, or `pip install` with a malicious post-install hook slip through unlisted. An allowlist (only known-safe/read-only-ish commands auto-run; everything else asks) is safer by default at the cost of more confirmation prompts. Decide which model before implementing.
* High-risk → emit `confirmation_request`, block until `confirmation_response` or 30 s timeout (timeout = deny). Low-risk → execute directly.
* Log every executed command + result summary to SQLite (`messages` with role `tool`).

Acceptance: "find my resume and copy it to the Desktop" completes as a multi-step chain; "delete everything in my Downloads folder" triggers a spoken + chip confirmation and does nothing on "no" or timeout.

## Phase 5 — Backend: semantic memory (RAG)

* Local embeddings via `sentence-transformers` (e.g. `all-MiniLM-L6-v2`) — nothing leaves the machine.
* Extraction: at session end (and every ~15 turns), send the recent transcript to Claude with a prompt to extract durable, user-specific facts as a JSON list (preferences, projects, recurring schedule, names). Deduplicate against existing memories via cosine similarity > 0.9 before insert. Store content + embedding in the `memories` table.
* Retrieval: on each user turn, embed the utterance, retrieve top-5 memories above similarity 0.35, inject into the system prompt under a "Known facts about the user" section.
* Tools: add `remember_this(content)` and `forget(query)` tool definitions so explicit voice commands ("remember that my standup is at 9:30") manage memory directly.
* Memory writes are low-risk; `forget` deletions require confirmation.

Acceptance: tell Spark a fact, restart the app, ask a question that requires that fact in different wording — it answers using it.

## Phase 6 — Backend: morning briefing (narrow proactivity)

* On first voice activity of a calendar day (or app launch after 5 a.m., whichever comes first), assemble a briefing: today's Calendar events + due Reminders (existing `osascript` paths), local weather via a keyless API such as Open-Meteo.
* One Claude call to compose a ≤4-sentence spoken summary; deliver via TTS + `briefing` message rendered in the panel.
* Settings flag in `~/.spark/config.json` to disable; never brief more than once per day.

Acceptance: first interaction of the day begins with a spoken briefing covering calendar, reminders, and weather; second interaction does not.

## Cross-cutting requirements

* Every phase: update README architecture section and add at least smoke-level tests for new backend modules (protocol serialization, risk classifier, memory retrieval).
* No new cloud dependencies for audio or memory — Whisper, Kokoro, and embeddings all run locally. The only network calls are the Claude API and weather.
* Keep the whisper-env Python 3.11 venv; pin new deps in `requirements.txt` (already stale as of this writing — it's missing `anthropic`, `python-dotenv`, and `pyttsx3`, which are already in use; fix this before adding Kokoro/sentence-transformers on top of it).
* Preserve existing fixes: `should_listen` anti-loop machinery, mic device handling (also fix the hardcoded mic device name — make it a config value with auto-detect fallback).

## Suggested working order recap

0. Protocol v2 (WebSocket transport + versioned messages) → 1. SQLite persistence → 2. Kokoro + streaming TTS + barge-in → 3. Orb frontend → 4. Agent loop + gates → 5. Semantic memory → 6. Morning briefing
