const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

let win;
let sparkProcess = null;

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


let quitting = false;

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
      // The backend dying (crash or otherwise) leaves a UI with nothing
      // behind it — quit the whole app rather than leaving a zombie window,
      // matching the same "if one dies, everything dies" lifecycle as the
      // reverse direction below.
      if (!quitting) {
        quitting = true;
        app.quit();
      }
    });
  }
}

function killSparkBackend() {
  if (!sparkProcess) return;
  const proc = sparkProcess;
  // kill() sends SIGTERM, which spark_whisper_mic.py's own handler normally
  // catches and exits cleanly on — but we've seen it not always respond
  // promptly (e.g. mid-blocking-call). Escalate to SIGKILL if it hasn't
  // actually exited after a short grace period, so a hung backend can never
  // outlive the app and squat on the WebSocket port for the next launch.
  proc.kill();
  const forceKillTimer = setTimeout(() => {
    if (sparkProcess === proc) proc.kill('SIGKILL');
  }, 3000);
  proc.once('exit', () => clearTimeout(forceKillTimer));
}

app.whenReady().then(() => {
  createWindow();
  startSparkBackend();
});

// macOS doesn't quit an app when its last window is closed by default — it
// stays running in the background with the Python backend still alive,
// which then squats on the WebSocket port when you try to relaunch. Force
// a real quit instead, since Spark isn't meant to be a background/menu-bar
// app today.
app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', () => {
  quitting = true;
  killSparkBackend();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

