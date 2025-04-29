const fs = require('fs');
const path = require('path');

const outputPath = path.join(__dirname, 'spark_output.json');
let lastStatus = "";
let backendStarted = false; // 🚀 Track if Spark backend really started

// 🧹 Clean up old output file on app start
function resetSparkOutput() {
  if (fs.existsSync(outputPath)) {
    fs.unlinkSync(outputPath);
    console.log('[Spark Main] 🧹 Old spark_output.json deleted.');
  }
}

function pollSparkStatus() {
  fs.readFile(outputPath, 'utf8', (err, data) => {
    if (err) {
      console.error('[Spark UI] Error reading output:', err);
      return;
    }

    try {
      const json = JSON.parse(data);
      const status = json.status; // read `status`!

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

  // 🚨 Show only if backend actually started AND valid status
  if (['listening', 'thinking', 'speaking'].includes(status)) {
    backendStarted = true;
    bubble.style.display = 'block'; // show bubble
  } else if (!backendStarted) {
    bubble.style.display = 'none'; // still hide if backend not really started
  }

  bubble.className = ''; // clear previous classes

  if (status === 'listening') {
    bubble.classList.add('listening');
  } else if (status === 'thinking') {
    bubble.classList.add('thinking');
  } else if (status === 'speaking') {
    bubble.classList.add('speaking');
  }
}

setInterval(pollSparkStatus, 500);
