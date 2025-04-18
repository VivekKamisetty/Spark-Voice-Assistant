window.sparkAPI.onListen(() => {
    const bubble = document.getElementById('bubble');
    bubble.style.display = 'flex';
    bubble.textContent = 'Spark';
  });
  
//   window.sparkAPI.onTranscription((_, text) => {
//     const bubble = document.getElementById('bubble');
//     bubble.textContent = text;
  
//     // Optional: auto-hide after 5s
//     setTimeout(() => {
//       bubble.style.display = 'none';
//       bubble.textContent = 'Idle';
//     }, 5000);
//   });
  