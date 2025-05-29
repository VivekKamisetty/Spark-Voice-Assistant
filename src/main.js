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
    height: 400,
    x: width - 640,   // ⬅ positions properly near right edge
    y: 40,
    frame: false,
    transparent: true,
    focusable: true,  // ✅ must be focusable to stay on top reliably
    skipTaskbar: true,
    alwaysOnTop: true,
    resizable: false,
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

win.on('ready-to-show', () => {
  win.setIgnoreMouseEvents(true, { forward: true });
})
}

ipcMain.on('resize-window', (event, { width, height }) => {
  if (win) {
    win.setSize(Math.round(width), Math.round(height));
  }
});

ipcMain.on('set-mouse-events', (event, interactive) => {
  if (win) {
    win.setIgnoreMouseEvents(!interactive, { forward: true });
  }
});


function resetSparkOutput() {
  const defaultData = {
    status: "idle",
    show_popup: false,
    text: ""
  };
  try {
    fs.writeFileSync(outputPath, JSON.stringify(defaultData, null, 2));
    console.log('[Spark Main] 🧹 Reset spark_output.json with defaults.');
  } catch (e) {
    console.error('[Spark Main] Failed to reset:', e);
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

  // globalShortcut.register('CommandOrControl+Shift+S', () => {
  //   win.reload();
  // });

  // globalShortcut.register('Space', () => {
  //   console.log('[Spark Main] 🔥 Spacebar pressed!');
  //   startSparkBackend();
  // });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

