const { marked } = require("marked");
const hljs = require("highlight.js");
const { ipcRenderer } = require('electron');
const { createOrb, STATE_COLOR_HEX } = require('./orb.js');

marked.setOptions({
  highlight: (code) => hljs.highlightAuto(code).value
});

const orb = createOrb(document.getElementById('orb-canvas'));

const WINDOW_WIDTH = 380; // must match src/main.js's WINDOW_WIDTH

// --- WebSocket connection to the Python backend (protocol v2) ---
const WS_URL = 'ws://localhost:8765';
let reconnectDelay = 500;
const MAX_RECONNECT_DELAY = 5000;
let socket = null;

function connectSparkSocket() {
  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    console.log('[Spark UI] Connected to backend.');
    reconnectDelay = 500;
  };

  socket.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      console.error('[Spark UI] Malformed message from backend:', e);
      return;
    }
    handleSparkMessage(msg);
  };

  socket.onclose = () => {
    setTimeout(connectSparkSocket, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
  };

  socket.onerror = () => {
    socket.close();
  };
}

function sendToBackend(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

// --- Conversation state ---
let lastState = "";
let currentAssistantEl = null; // the <div class="message assistant"> currently streaming into
let currentUserEl = null;
let pendingConfirmationId = null;

function handleSparkMessage(msg) {
  switch (msg.type) {
    case 'state':
      if (msg.value !== lastState) {
        lastState = msg.value;
        orb.setState(msg.value);
        onStateChange(msg.value);
      }
      break;

    case 'amplitude':
      orb.setAmplitude(msg.bass, msg.mid, msg.high);
      break;

    case 'transcript':
      renderUserTranscript(msg.text, msg.partial);
      break;

    case 'assistant_chunk':
      appendAssistantChunk(msg.text, msg.index);
      break;

    case 'assistant_done':
      finalizeAssistantMessage();
      break;

    case 'speech_started':
      highlightSpokenSentence(msg.index);
      break;

    case 'tool_activity':
      updateToolActivity(msg);
      break;

    case 'confirmation_request':
      showConfirmationChips(msg);
      break;

    case 'confirmation_resolved':
      hideConfirmationChips(msg.id);
      break;

    case 'briefing':
      renderBriefing(msg.text);
      break;

    default:
      // Unknown message types are ignored gracefully, per protocol v2.
      break;
  }
}

function onStateChange(state) {
  // Deliberately not expanding here for every state — that would unfold the
  // panel on every idle "listening"/"calibrating" transition too. The panel
  // should only open when there's actually something to show: a transcript
  // starting (renderUserTranscript) or a confirmation prompt
  // (showConfirmationChips) already call expandPanel() themselves. It also
  // no longer auto-collapses on an idle timer (see collapsePanel) — once
  // open, it stays open until the user hides it themselves.

  // Tints the panel's top edge (see style.css) to match the orb's current
  // state color, so light appears to spill from the orb onto the glass
  // below it rather than the two looking like unrelated surfaces.
  document.documentElement.style.setProperty(
    '--state-glow-color',
    STATE_COLOR_HEX[state] || STATE_COLOR_HEX.idle
  );

  const stopButton = document.getElementById('stop-button');
  if (stopButton) stopButton.classList.toggle('hidden', state !== 'speaking');

  if (state === 'listening') {
    // A fresh turn is starting (or we've returned to rest) — clear the
    // in-progress streaming bubble so the next reply starts its own.
    currentAssistantEl = null;
    currentUserEl = null;
  }
}

// --- Transcript rendering ---

function getTranscriptContainer() {
  return document.getElementById('transcript');
}

function renderUserTranscript(text, partial) {
  const container = getTranscriptContainer();
  if (!currentUserEl) {
    // Only the latest exchange is ever shown — clear the previous turn now
    // that a new one is starting, rather than accumulating full history.
    container.innerHTML = '';
    currentAssistantEl = null;
    currentUserEl = document.createElement('div');
    currentUserEl.className = 'message user';
    container.appendChild(currentUserEl);
  }
  currentUserEl.textContent = text;
  currentUserEl.classList.toggle('partial', !!partial);
  if (!partial) {
    currentUserEl = null; // next transcript starts a new bubble
  }
  // Measuring content height for expandPanel() must happen after the new
  // element is in the DOM, not before — otherwise it measures the
  // pre-update (often near-empty) content and the panel never actually
  // grows to fit anything.
  expandPanel();
  scrollTranscriptToBottom();
}

// Tracks the highest sentence index that has actually been spoken so far in
// the current reply (see highlightSpokenSentence) — needed at finalize time
// to know which sentences to settle into "dimmed" vs. leave untouched (e.g.
// a ---DETAIL--- section that was never spoken in "brief" voice mode
// shouldn't dim just because the turn ended).
let highestSpokenIndex = -1;

function appendAssistantChunk(text, index) {
  if (!currentAssistantEl) {
    const container = getTranscriptContainer();
    currentAssistantEl = document.createElement('div');
    currentAssistantEl.className = 'message assistant';
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    currentAssistantEl.appendChild(cursor);
    container.appendChild(currentAssistantEl);
  }

  const cursor = currentAssistantEl.querySelector('.cursor');
  // A plain space between consecutive sentences: each one arrives already
  // trimmed of surrounding whitespace (see backend/sentence_splitter.py), so
  // without this they'd run together with no gap.
  if (currentAssistantEl.querySelector('.sentence')) {
    currentAssistantEl.insertBefore(document.createTextNode(' '), cursor);
  }

  // Each sentence gets its own element (rather than one re-parsed blob) so
  // highlightSpokenSentence can dim/glow individual sentences as TTS
  // actually plays through them. A div (not span) because marked.parse can
  // legitimately produce block content (lists, code blocks) for a chunk —
  // style.css makes it flow inline for the common plain-sentence case.
  const sentenceEl = document.createElement('div');
  sentenceEl.className = 'sentence';
  sentenceEl.dataset.index = String(index);
  sentenceEl.innerHTML = marked.parse(text);
  currentAssistantEl.insertBefore(sentenceEl, cursor);

  // Re-measure and grow the panel as the reply streams in and gets longer,
  // not just once when the user's own message first opened it.
  expandPanel();
  scrollTranscriptToBottom();
}

function highlightSpokenSentence(index) {
  if (!currentAssistantEl) return;
  highestSpokenIndex = index;
  currentAssistantEl.querySelectorAll('.sentence').forEach((el) => {
    const i = Number(el.dataset.index);
    el.classList.remove('sentence-active');
    if (i < index) {
      el.classList.add('sentence-dimmed');
    } else if (i === index) {
      el.classList.add('sentence-active');
    }
  });
}

function finalizeAssistantMessage() {
  if (currentAssistantEl) {
    const cursor = currentAssistantEl.querySelector('.cursor');
    if (cursor) cursor.remove();
    // Nothing is "currently being spoken" anymore once the turn is over —
    // settle every sentence that was actually spoken into dimmed rather than
    // leaving the last one stuck glowing. Sentences past highestSpokenIndex
    // (e.g. an unspoken ---DETAIL--- section) are left at full brightness.
    currentAssistantEl.querySelectorAll('.sentence').forEach((el) => {
      const i = Number(el.dataset.index);
      el.classList.remove('sentence-active');
      if (i <= highestSpokenIndex) {
        el.classList.add('sentence-dimmed');
      }
    });
  }
  currentAssistantEl = null;
  highestSpokenIndex = -1;
}

// Arrives as one complete message (not streamed sentence-by-sentence like
// assistant_chunk), and isn't a reply to anything the user said — rendered
// as its own labeled bubble rather than reusing appendAssistantChunk's
// streaming-cursor machinery, which assumes an in-progress reply.
//
// Prepended rather than clearing the container: the backend's other trigger
// for this (first real utterance of the day, not just app launch — see
// spark_whisper_mic.py) fires *after* that utterance's own transcript has
// already been rendered, so clearing here would silently wipe the user's
// just-shown message right before Spark's reply to it arrives. Prepending
// keeps the briefing visually first (it was spoken first) without erasing
// the exchange that triggered it.
function renderBriefing(text) {
  const container = getTranscriptContainer();
  currentAssistantEl = null;
  currentUserEl = null;

  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant briefing';

  const label = document.createElement('div');
  label.className = 'briefing-label';
  label.textContent = '🌅 Morning Briefing';
  wrapper.appendChild(label);

  const body = document.createElement('div');
  body.className = 'sentence';
  body.innerHTML = marked.parse(text);
  wrapper.appendChild(body);

  container.prepend(wrapper);
  expandPanel();
  // Deliberately scrollTranscriptToTop, not …ToBottom: the briefing is
  // prepended as the first thing in the panel, and on a shorter screen
  // (smaller maxPanelHeight — see expandPanel) a multi-sentence briefing can
  // be taller than the visible area. Scrolling to bottom (the normal
  // behavior for a growing reply) would scroll straight past the label and
  // opening sentences — found live from a real screenshot where exactly
  // that happened, showing only the tail end of the message with no label
  // visible at all.
  scrollTranscriptToTop();
}

function scrollTranscriptToBottom() {
  const scroll = document.getElementById('transcript-scroll');
  scroll.scrollTop = scroll.scrollHeight;
}

function scrollTranscriptToTop() {
  const scroll = document.getElementById('transcript-scroll');
  scroll.scrollTop = 0;
}

// --- Tool activity ---

function updateToolActivity(msg) {
  const el = document.getElementById('tool-activity');
  if (msg.status === 'running') {
    el.textContent = msg.summary || `Running ${msg.name}…`;
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

// --- Confirmation chips ---

function showConfirmationChips(msg) {
  pendingConfirmationId = msg.id;
  const container = document.getElementById('confirmation-chips');
  container.innerHTML = '';

  (msg.options || []).forEach((option) => {
    const chip = document.createElement('button');
    chip.className = 'chip' + (msg.risk === 'high' ? ' high-risk' : '');
    chip.textContent = option;
    chip.onclick = () => {
      sendToBackend({ type: 'confirmation_response', id: pendingConfirmationId, choice: option });
      container.classList.add('hidden');
      container.innerHTML = '';
      pendingConfirmationId = null;
    };
    container.appendChild(chip);
  });

  container.classList.remove('hidden');
  expandPanel();
}

function hideConfirmationChips(id) {
  // A confirmation can be resolved by voice (a spoken yes/no), not just a
  // chip click, so this has to be a separate handler the backend can
  // trigger — otherwise chips resolved by voice stay stuck on screen
  // forever since the click handler is the only other thing that hides them.
  if (id && id !== pendingConfirmationId) return;
  const container = document.getElementById('confirmation-chips');
  container.classList.add('hidden');
  container.innerHTML = '';
  pendingConfirmationId = null;
}

// --- Panel expand/collapse (spring physics height animation) ---

let panelHeight = 0;
let panelVelocity = 0;
let panelTargetHeight = 0;
let springRunning = false;

const SPRING_STIFFNESS = 210;
const SPRING_DAMPING = 26;

// The orb sits ORB_AREA_HEIGHT above the panel (orb-wrap height + its
// margin-top + the panel's own margin-top from style.css), and the window
// itself starts TOP_OFFSET down from the screen top (must match the `y` src
// main.js positions the window at) — both needed to know how much vertical
// room is actually left for the panel before it'd run off-screen.
const TOP_OFFSET = 40;
const ORB_AREA_HEIGHT = 160 + 12 + 10;
const BOTTOM_MARGIN = 30;

function maxPanelHeight() {
  const available = window.screen.availHeight - TOP_OFFSET - ORB_AREA_HEIGHT - BOTTOM_MARGIN;
  return Math.max(180, available);
}

function measureNaturalPanelHeight() {
  // panel.scrollHeight is the wrong thing to measure here: #transcript-scroll
  // is a flex child with its own `overflow-y: auto`, so once #panel's height
  // is set, the flex algorithm sizes that child to fit within it and any
  // extra transcript content is absorbed by ITS OWN internal scrolling —
  // it never inflates #panel's scrollHeight. That's why the panel used to
  // stay stuck small (showing only the tail of a long reply) no matter how
  // much text streamed in. #transcript-scroll's own scrollHeight is what
  // actually reflects the full, unclipped content height.
  const panelHeader = document.getElementById('panel-header');
  const transcriptScroll = document.getElementById('transcript-scroll');
  const chips = document.getElementById('confirmation-chips');
  const inputRow = document.getElementById('input-row');
  const chipsHeight = chips.classList.contains('hidden') ? 0 : chips.offsetHeight;
  return panelHeader.offsetHeight + transcriptScroll.scrollHeight + chipsHeight + inputRow.offsetHeight;
}

function expandPanel() {
  const panel = document.getElementById('panel');
  panel.classList.add('visible');
  // Capped by actual available screen space rather than a fixed number — a
  // fixed cap (previously 420px) clips a normal multi-paragraph reply well
  // before it runs out of real screen room to grow into.
  panelTargetHeight = Math.min(maxPanelHeight(), Math.max(180, measureNaturalPanelHeight() || 300));
  startSpring();
}

function collapsePanel() {
  panelTargetHeight = 0;
  startSpring();
}

function startSpring() {
  if (springRunning) return;
  springRunning = true;
  let lastTime = performance.now();

  function step(now) {
    const dt = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    const displacement = panelTargetHeight - panelHeight;
    const springForce = displacement * SPRING_STIFFNESS;
    const dampingForce = -panelVelocity * SPRING_DAMPING;
    const acceleration = springForce + dampingForce;
    panelVelocity += acceleration * dt;
    panelHeight += panelVelocity * dt;

    const panel = document.getElementById('panel');
    const settled = Math.abs(displacement) < 0.5 && Math.abs(panelVelocity) < 0.5;
    const clamped = settled ? panelTargetHeight : panelHeight;
    panel.style.height = `${Math.max(0, clamped)}px`;
    if (clamped <= 0.5) panel.classList.remove('visible');

    resizeWindowToContent();

    if (settled) {
      panelHeight = panelTargetHeight;
      panelVelocity = 0;
      springRunning = false;
      return;
    }
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function resizeWindowToContent() {
  // #app is `height: 100%` (bound to the window's own current size), so
  // neither its getBoundingClientRect().height nor its scrollHeight give
  // the actual content size — both just mirror #app's own (window-sized)
  // box, since content here is smaller than that box, not overflowing it.
  // Using either was a runaway feedback loop: resize to (current window
  // height + 4px buffer), which makes the window taller, which #app then
  // reports as its new "current" size next frame, forever, growing
  // +4px/frame for as long as the spring animation keeps running. Measuring
  // the actual content elements' own rendered bounds — independent of
  // #app's box — is the only way to break that loop.
  const orbWrap = document.getElementById('orb-wrap');
  const panel = document.getElementById('panel');
  const contentBottom = Math.max(
    orbWrap.getBoundingClientRect().bottom,
    panel.getBoundingClientRect().bottom
  );
  const height = Math.ceil(contentBottom) + 4;
  ipcRenderer.send('resize-window', { width: WINDOW_WIDTH, height });
}

// --- Stop / interrupt button ---

document.addEventListener('DOMContentLoaded', () => {
  const stopButton = document.getElementById('stop-button');
  stopButton.addEventListener('click', (e) => {
    e.stopPropagation(); // don't also trigger the orb's click-to-expand below
    sendToBackend({ type: 'interrupt' });
  });
});

// --- Hide / show panel (manual, no more idle auto-collapse) ---

document.addEventListener('DOMContentLoaded', () => {
  const hideButton = document.getElementById('hide-panel-button');
  hideButton.addEventListener('click', (e) => {
    e.stopPropagation();
    collapsePanel();
  });

  // Clicking the orb toggles the panel — hides it (including its background)
  // when open, so the orb can sit small and out of the way instead of the
  // panel occupying a big chunk of the screen by default; clicking again
  // brings it back. panelTargetHeight (not the 'visible' class, which only
  // clears once the collapse animation finishes) reflects current intent
  // even mid-animation.
  const orbCanvas = document.getElementById('orb-canvas');
  orbCanvas.addEventListener('click', () => {
    if (panelTargetHeight > 0) {
      collapsePanel();
    } else {
      expandPanel();
    }
  });
});

// --- Typed input ---

document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('text-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && input.value.trim()) {
      // Rendered when the backend broadcasts it back as a transcript
      // message (same path voice input uses), not optimistically here.
      sendToBackend({ type: 'text_input', text: input.value.trim() });
      input.value = '';
    }
  });

  const copyButton = document.getElementById('copy-last-button');
  copyButton.addEventListener('click', () => {
    // Each assistant message is now built from per-sentence .sentence
    // elements (see appendAssistantChunk) rather than a single .text span,
    // so the whole .message.assistant's innerText is what to copy.
    const messages = document.querySelectorAll('.message.assistant');
    if (!messages.length) return;
    const text = messages[messages.length - 1].innerText;
    navigator.clipboard.writeText(text).then(() => {
      const original = copyButton.textContent;
      copyButton.textContent = '✓ Copied';
      setTimeout(() => (copyButton.textContent = original), 1500);
    }).catch((err) => console.error('[Spark UI] Failed to copy:', err));
  });

  // Mouse-region interactivity: click-through everywhere except the orb and
  // panel, since the window itself has no border/frame to grab.
  document.addEventListener('mousemove', (e) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const interactive = el && (el.closest('#panel') || el.closest('#orb-wrap'));
    ipcRenderer.send('set-mouse-events', interactive);
  });
});

// --- Connect to backend ---
connectSparkSocket();
