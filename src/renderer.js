window.sparkAPI.onListen(() => {
    const bubble = document.getElementById('bubble');
    bubble.style.display = 'flex';
    bubble.textContent = 'Spark';
  
    setTimeout(() => {
      bubble.style.display = 'none';
    }, 10000);
  });
  