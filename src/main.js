const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs'); // <-- 💥 IMPORTANT! you missed this

let win;
let sparkProcess = null;

const outputPath = path.join(__dirname, '..', 'public', 'spark_output.json'); // <-- You also missed defining this before using

function createWindow() {
  const { screen } = require('electron');
  const { width } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width: 600,
    height: 300,
    x: width - 640,   // ⬅ positions properly near right edge
    y: 40,
    frame: false,
    transparent: true,
    focusable: true,  // ✅ must be focusable to stay on top reliably
    skipTaskbar: true,
    alwaysOnTop: true,
    resizable: true,
    fullscreenable: false,        // ✅ for better macOS layering
    hasShadow: false,
    titleBarStyle: 'customButtonsOnHover',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      backgroundThrottling: false,
      enableBlinkFeatures: 'CSSBackdropFilter'
    }
  });
  win.setAlwaysOnTop(true, 'floating', 1);
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  const indexPath = path.join(__dirname, '../public/index.html');
  win.loadFile(indexPath);
  win.setIgnoreMouseEvents(false); // let clicks pass through
}

function resetSparkOutput() {
  if (fs.existsSync(outputPath)) {
    fs.unlinkSync(outputPath);
    console.log('[Spark Main] 🧹 Old spark_output.json deleted.');
  }
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
  resetSparkOutput();  // 🧹 very good!
  createWindow();

  globalShortcut.register('CommandOrControl+Shift+S', () => {
    win.reload();
  });

  globalShortcut.register('Space', () => {
    console.log('[Spark Main] 🔥 Spacebar pressed!');
    startSparkBackend();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
