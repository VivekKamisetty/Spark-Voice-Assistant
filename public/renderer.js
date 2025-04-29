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

      if (status !== lastStatus) {
        lastStatus = status;
        updateBubble(status);
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
