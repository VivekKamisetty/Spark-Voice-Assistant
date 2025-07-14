const fs = require('fs');
const path = require('path');
const { marked } = require("marked");
const hljs = require("highlight.js");
const { ipcRenderer } = require('electron');



const outputPath = path.join(__dirname, '..', 'public', 'spark_output.json');
let lastStatus = "";

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

// --- Poll the spark_output.json ---
function pollSparkStatus() {
  fs.readFile(outputPath, 'utf8', (err, data) => {
    if (err) return console.error('[Spark UI] Error reading spark_output.json:', err);

    try {
      const json = JSON.parse(data);
      const status = json.status;
      const showPopupFlag = json.show_popup;
      const text = json.text || "";

      if (status !== lastStatus) {
        lastStatus = status;
        updateBubble(status);
      }

      if (showPopupFlag && text.trim().length > 0) {
        showPopup(text);
      }
    } catch (e) {
      console.error('[Spark UI] Error parsing JSON:', e);
    }
  });
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
  const overlay   = document.getElementById('spark-popup');
  const content   = document.getElementById('popup-content');
  const popupText = document.getElementById('spark-popup-text');

  // 1) clear + render
  popupText.innerHTML = '';
  popupText.innerHTML = marked.parse(text);

  // 2) position the CONTENT div under the bubble
  const bubbleRect = document.getElementById('bubble').getBoundingClientRect();
  const vh = window.innerHeight;
  const maxH = vh * 0.8;              // must match CSS max-height
  let top = bubbleRect.bottom + 15;   // 15px gap
  if (top + maxH > vh) {
    top = vh - maxH - 20;             // keep it fully on screen
  }
  content.style.top = `${top}px`;

  // 3) restore any saved width/height/left
  if (popupSettings.width)  content.style.width  = popupSettings.width;
  if (popupSettings.height) content.style.height = popupSettings.height;
  if (popupSettings.left)   content.style.left   = popupSettings.left;

  // 4) reveal the overlay
  overlay.classList.add('show');
}

// --- Close Popup ---
function closePopup() {
  const overlay = document.getElementById('spark-popup');
  const content = document.getElementById('popup-content');

  // save the CONTENT dims & position
  popupSettings = {
    width:  content.style.width,
    height: content.style.height,
    left:   content.style.left,
    top:    content.style.top
  };
  localStorage.setItem('spark-popup-settings', JSON.stringify(popupSettings));

  overlay.classList.remove('show');

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
  const overlay = document.getElementById("spark-popup");
  const content = document.getElementById("popup-content");
  const grip    = document.getElementById("popup-resize-grip");

  // 1) Observe the card, not the overlay
  const resizeObserver = new ResizeObserver(entries => {
    for (let entry of entries) {
      if (entry.target === content) {
        savePopupDimensions();
      }
    }
  });
  resizeObserver.observe(content);

  document.addEventListener('mousemove', (e) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const interactive = el && (el.closest('#spark-popup') || el.closest('#bubble'));
    ipcRenderer.send('set-mouse-events', interactive);
  });

  document.getElementById('spark-close-button').addEventListener('click', () => {
    closePopup(); // Call your existing closePopup function
  });

  document.getElementById('spark-copy-button').addEventListener('click', (event) => {
    copyPopupText(event);
  });

  // Only grip triggers resize
  grip.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const rect = content.getBoundingClientRect();
    const startX = e.clientX, startY = e.clientY;
    const startW = rect.width, startH = rect.height;
    
    function onMouseMove(ev) {
      let newW = startW + (ev.clientX - startX);
      let newH = startH + (ev.clientY - startY);
      // clamp so it never goes below your min dimensions
      newW = Math.max(newW, 350);
      newH = Math.max(newH, 240);
      content.style.width  = `${newW}px`;
      content.style.height = `${newH}px`;
    }
    function onMouseUp() {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    }
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  });
});


// --- Start Polling ---
setInterval(pollSparkStatus, 500);
