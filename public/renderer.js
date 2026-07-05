const { marked } = require("marked");
const hljs = require("highlight.js");
const { ipcRenderer } = require('electron');

let lastStatus = "";
let pendingAssistantText = "";

// --- Setup Markdown + Code Highlighting ---
marked.setOptions({
  highlight: (code) => hljs.highlightAuto(code).value
});

// --- Saved Dimensions ---
let popupSettings = {
  width: null,
  height: null,
  left: null,
  top: null
};

// Try to load settings
try {
  const saved = localStorage.getItem('spark-popup-settings');
  if (saved) popupSettings = JSON.parse(saved);
} catch (e) {
  console.error('[Spark UI] Failed to load popup settings:', e);
}

// --- WebSocket connection to the Python backend (protocol v2) ---
const WS_URL = 'ws://localhost:8765';
let reconnectDelay = 500;
const MAX_RECONNECT_DELAY = 5000;

function connectSparkSocket() {
  const socket = new WebSocket(WS_URL);

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

function handleSparkMessage(msg) {
  switch (msg.type) {
    case 'state':
      if (msg.value !== lastStatus) {
        lastStatus = msg.value;
        updateBubble(msg.value);
      }
      break;
    case 'amplitude':
      // Not consumed visually yet — the reactive orb lands in Phase 3.
      // Logged here so Phase 0's acceptance check (amplitude visible in
      // devtools while speaking) can be verified.
      console.log('[Spark UI] amplitude', msg.source, msg.rms);
      break;
    case 'assistant_chunk':
      pendingAssistantText += msg.text;
      break;
    case 'assistant_done':
      if (msg.show_popup && pendingAssistantText.trim().length > 0) {
        showPopup(pendingAssistantText);
      }
      pendingAssistantText = "";
      break;
    case 'transcript':
    case 'tool_activity':
    case 'confirmation_request':
    case 'briefing':
      // Not yet consumed by the UI — their features land in later phases.
      console.log('[Spark UI]', msg.type, msg);
      break;
    default:
      // Unknown message types are ignored gracefully, per protocol v2.
      break;
  }
}

// --- Update Bubble Status ---
function updateBubble(status) {
  const bubble = document.getElementById('bubble');
  if (!bubble.classList.contains('show')) bubble.classList.add('show');
  bubble.className = 'show'; // reset
  bubble.textContent = '';

  switch (status) {
    case 'listening': bubble.classList.add('listening'); break;
    case 'thinking': bubble.classList.add('thinking'); break;
    case 'speaking': bubble.classList.add('speaking'); break;
    case 'calibrating':
      bubble.classList.add('thinking');
      bubble.textContent = 'Calibrating...';
      break;
  }
}

// --- Show Popup with Markdown/Code ---
function showPopup(text) {
  const popup = document.getElementById("spark-popup");
  const popupText = document.getElementById("spark-popup-text");

  // Set dimensions
  if (popupSettings.width) popup.style.width = popupSettings.width;
  if (popupSettings.height) popup.style.height = popupSettings.height;
  if (popupSettings.left) popup.style.left = popupSettings.left;
  if (popupSettings.top) popup.style.top = popupSettings.top;

  popupText.innerHTML = marked.parse(text);
  popup.classList.remove("hidden");
  popup.classList.add("show");

}

// --- Close Popup ---
function closePopup() {
  const popup = document.getElementById("spark-popup");
  savePopupDimensions();
  popup.classList.remove("show");
  popup.classList.add("hidden");

}

// --- Copy Button ---
function copyPopupText(event) {
  const text = document.getElementById("spark-popup-text").innerText;
  navigator.clipboard.writeText(text).then(() => {
    const btn = event.target;
    const original = btn.innerText;
    btn.innerText = "✓ Copied!";
    setTimeout(() => (btn.innerText = original), 1500);
  }).catch(err => {
    console.error('Failed to copy:', err);
  });
}

// --- Save Dimensions ---
function savePopupDimensions() {
  const popup = document.getElementById("spark-popup");
  popupSettings = {
    width: popup.style.width,
    height: popup.style.height,
    left: popup.style.left,
    top: popup.style.top
  };
  try {
    localStorage.setItem('spark-popup-settings', JSON.stringify(popupSettings));
  } catch (e) {
    console.error('[Spark UI] Failed to save popup settings:', e);
  }
}

// --- ResizeObserver Setup ---
document.addEventListener('DOMContentLoaded', () => {
  const popup = document.getElementById("spark-popup");
  const grip = document.getElementById("popup-resize-grip");

  // Save size on change
  const resizeObserver = new ResizeObserver(entries => {
    for (let entry of entries) {
      if (entry.target === popup) {
        savePopupDimensions();
      }
    }
  });
  resizeObserver.observe(popup);

  document.addEventListener('mousemove', (e) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const interactive = el && (el.closest('#spark-popup') || el.closest('#bubble'));
    ipcRenderer.send('set-mouse-events', interactive);
  });

  // Only grip triggers resize
  grip.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const rect = popup.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startWidth = rect.width;
    const startHeight = rect.height;
  
    function onMouseMove(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const newWidth = startWidth + dx;
      const newHeight = startHeight + dy;
  
      // Resize the DOM popup
      popup.style.width = `${newWidth}px`;
      popup.style.height = `${newHeight}px`;
  
      // Tell Electron to resize the actual window
      //ipcRenderer.send('resize-window', { width: newWidth, height: newHeight});
    }
  
    function onMouseUp() {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    }
  
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  });
});


// --- Connect to backend ---
connectSparkSocket();
