const fs = require('fs');
const path = require('path');

const outputPath = path.join(__dirname, '..', 'public', 'spark_output.json');
let lastStatus = "";

// Store last size/position to persist across sessions
let popupSettings = {
  width: null,
  height: null,
  left: null,
  top: null
};

// Try to load saved settings
try {
  const savedSettings = localStorage.getItem('spark-popup-settings');
  if (savedSettings) {
    popupSettings = JSON.parse(savedSettings);
  }
} catch (e) {
  console.error('[Spark UI] Error loading saved settings:', e);
}

function pollSparkStatus() {
  fs.readFile(outputPath, 'utf8', (err, data) => {
    if (err) {
      console.error('[Spark UI] Error reading output:', err);
      return;
    }

    try {
      const json = JSON.parse(data);
      const status = json.status;
      const showPopupFlag = json.show_popup;
      const text = json.text || "";

      if (status !== lastStatus) {
        lastStatus = status;
        updateBubble(status);
      }

      // Show popup if instructed and there's text
      if (showPopupFlag && text.trim().length > 0) {
        showPopup(text);
      }
    } catch (error) {
      console.error('[Spark UI] Error parsing JSON:', error);
    }
  });
}

function updateBubble(status) {
  const bubble = document.getElementById('bubble');
  if (!bubble.classList.contains('show')) {
    bubble.classList.add('show');
  }
  bubble.className = 'show'; // Reset all classes but keep 'show'

  if (status === 'listening') {
    bubble.classList.add('listening');
    bubble.textContent = ' ';
  } else if (status === 'thinking') {
    bubble.classList.add('thinking');
  } else if (status === 'speaking') {
    bubble.classList.add('speaking');
  } else if (status === 'calibrating') {
    bubble.classList.add('thinking'); // reuse thinking animation
    bubble.textContent = 'Calibrating...'; // temporary label
  }
}

setInterval(pollSparkStatus, 500);

function showPopup(text) {
  const popup = document.getElementById("spark-popup");
  const popupText = document.getElementById("spark-popup-text");
  
  // Apply saved dimensions if available
  if (popupSettings.width) popup.style.width = popupSettings.width;
  if (popupSettings.height) popup.style.height = popupSettings.height;
  if (popupSettings.left) popup.style.left = popupSettings.left;
  if (popupSettings.top) popup.style.top = popupSettings.top;
  
  // Set the text
  popupText.innerText = text;
  
  // Show the popup
  popup.classList.remove("hidden");
  popup.classList.add("show");
}

function closePopup() {
  const popup = document.getElementById("spark-popup");
  savePopupDimensions();
  popup.classList.remove("show");
  popup.classList.add("hidden");
}

function savePopupDimensions() {
  const popup = document.getElementById("spark-popup");
  // Save current dimensions and position
  popupSettings = {
    width: popup.style.width,
    height: popup.style.height,
    left: popup.style.left,
    top: popup.style.top
  };
  
  try {
    localStorage.setItem('spark-popup-settings', JSON.stringify(popupSettings));
  } catch (e) {
    console.error('[Spark UI] Error saving settings:', e);
  }
}

function copyPopupText() {
  const text = document.getElementById("spark-popup-text").innerText;
  navigator.clipboard.writeText(text).then(() => {
    // Visual feedback for copy
    const copyButton = event.target;
    const originalText = copyButton.innerText;
    copyButton.innerText = "✓ Copied!";
    setTimeout(() => {
      copyButton.innerText = originalText;
    }, 1500);
  }).catch(err => {
    console.error('Failed to copy text: ', err);
  });
}

// Save popup dimensions on resize
document.addEventListener('DOMContentLoaded', function() {
  const popup = document.getElementById("spark-popup");
  
  // Use ResizeObserver to detect when the user resizes the popup
  const resizeObserver = new ResizeObserver(entries => {
    for (let entry of entries) {
      if (entry.target === popup) {
        savePopupDimensions();
      }
    }
  });
  
  resizeObserver.observe(popup);
  
});