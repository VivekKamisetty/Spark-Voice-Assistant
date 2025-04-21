console.log('[Spark Renderer] Loaded');
function showBubble(text) {
  const bubble = document.getElementById('bubble');
  bubble.textContent = text;
  bubble.classList.add('show');

  setTimeout(() => {
    bubble.classList.remove('show');
  }, 100000);
}

window.sparkAPI.onListen(() => {
  showBubble('Spark Listening...');
});

window.sparkAPI.onGPTResponse((_, reply) => {
  showBubble(reply);
});

  