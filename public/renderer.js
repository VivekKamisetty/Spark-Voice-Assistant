const fs = require('fs');
const path = require('path');

const outputPath = path.join(__dirname, '..', 'public', 'spark_output.json');
let lastStatus = "";

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
  } else if (status === 'thinking') {
    bubble.classList.add('thinking');
  } else if (status === 'speaking') {
    bubble.classList.add('speaking');
  }
}

setInterval(pollSparkStatus, 500);

let popupTimeout = null;
let lastPopupText = "";

function showPopup(text) {
  const popup = document.getElementById("spark-popup");
  const popupText = document.getElementById("spark-popup-text");

  if (text === lastPopupText) return; // 💡 Skip update if content hasn't changed
  lastPopupText = text;

  popupText.textContent = text;
  popup.classList.remove("hidden");

  popup.scrollTop = 0;

  if (popupTimeout) clearTimeout(popupTimeout);
  popupTimeout = setTimeout(() => {
    popup.classList.add("hidden");
    lastPopupText = ""; // reset tracker
  }, 20000);
}
function closePopup() {
  document.getElementById("spark-popup").classList.add("hidden");
}

function copyPopupText() {
  const text = document.getElementById("spark-popup-text").innerText;
  navigator.clipboard.writeText(text);
}

