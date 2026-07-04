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
    height: 600,
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
    const backendDir = path.join(__dirname, '..', 'backend');
    const pythonPath = path.join(backendDir, 'whisper-env', 'bin', 'python');
    // cwd must be backendDir: spark_whisper_mic.py's load_dotenv() resolves
    // .env relative to the process's working directory, not the script path.
    // "arch -arm64" forces native execution: this Electron/Node install runs
    // under Rosetta on Apple Silicon, and a translated (x86_64) parent spawns
    // children in x86_64 by default, which crashes torch (installed arm64-only).
    sparkProcess = spawn('arch', ['-arm64', pythonPath, 'spark_whisper_mic.py'], { cwd: backendDir });

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
  resetSparkOutput();
  createWindow();
  startSparkBackend();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (sparkProcess) {
    sparkProcess.kill();
    sparkProcess = null;
  }
});

