const { ipcRenderer } = require('electron');

ipcRenderer.on('trigger-listen', () => {
  const bubble = document.getElementById('bubble');
  bubble.textContent = '🎙️ Listening...';

  // revert back to idle after 2 sec
  setTimeout(() => {
    bubble.textContent = 'Spark';
  }, 2000);
});
