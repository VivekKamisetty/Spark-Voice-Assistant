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


// --- Start Polling ---
setInterval(pollSparkStatus, 500);
