console.log('[Spark Renderer] Loaded');

function showBubble(text) {
  const bubble = document.getElementById('bubble');
  bubble.textContent = text;
  bubble.classList.add('show');

  setTimeout(() => {
    bubble.classList.remove('show');
  }, 10000);
}

let lastReply = "";

async function pollSparkReply() {
  try {
    const res = await fetch('spark_output.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch spark output');

    const data = await res.json();
    const reply = data.reply;

    if (reply && reply !== lastReply) {
      lastReply = reply;
      console.log('[Spark Renderer] New reply:', reply);
      showBubble(reply);
    }
  } catch (err) {
    console.error('[Spark Renderer] Error reading reply:', err.message);
  }
}

setInterval(pollSparkReply, 1000);
