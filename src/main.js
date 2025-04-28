const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

let win;
let sparkProcess = null;

function createWindow() {
  const { screen } = require('electron');
  const { width } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width: 200,
    height: 200,
    x: width - 240, // top-right positioning
    y: 40,
    frame: false,
    transparent: true,
    focusable: false,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      contextIsolation: true
    }
  });

  const indexPath = path.join(__dirname, '../public/index.html');
  win.loadFile(indexPath);
  win.setIgnoreMouseEvents(true); // let clicks pass through the bubble
}

function startSparkBackend() {
  if (!sparkProcess) {
    console.log('[Spark Main] 🚀 Starting backend...');
    sparkProcess = spawn('python3', ['backend/spark_whisper_mic.py']);

    sparkProcess.stdout.on('data', (data) => {
      console.log(`[Spark] ${data}`);
    });

    sparkProcess.stderr.on('data', (data) => {
      console.error(`[Spark Error] ${data}`);
    });

    sparkProcess.on('close', (code) => {
      console.log(`[Spark] exited with code ${code}`);
      sparkProcess = null;
    });
  }
}

app.whenReady().then(() => {
  createWindow();

  // Hotkey to refresh the bubble (keep this)
  globalShortcut.register('CommandOrControl+Shift+S', () => {
    win.reload();
  });

  // 🚀 NEW: Hotkey to start Spark
  globalShortcut.register('Space', () => {
    console.log('[Spark Main] 🔥 Spacebar pressed!');
    startSparkBackend();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
