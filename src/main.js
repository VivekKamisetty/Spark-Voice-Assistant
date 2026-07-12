const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

let win;
let sparkProcess = null;

// Fixed width; only height changes between the compact orb and the
// expanded panel, so the window's top-left corner never has to move and
// the panel purely unfolds downward from a fixed orb position.
const WINDOW_WIDTH = 380;
const COMPACT_HEIGHT = 180;

function createWindow() {
  const { screen } = require('electron');
  const { width } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: COMPACT_HEIGHT,
    x: width - WINDOW_WIDTH - 20,
    y: 40,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    vibrancy: 'under-window',    // real macOS frosted-glass blur behind the orb
    roundedCorners: true,
    focusable: true,  // ✅ must be focusable to stay on top reliably
    skipTaskbar: true,
    alwaysOnTop: true,
    resizable: false, // size is managed programmatically (see resize-window below)
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

// Spark going idle (45s of no input — see start_idle_timer in
// spark_whisper_mic.py) hides the window rather than leaving it sitting on
// screen with nothing happening; it reappears the moment a new turn starts
// (any non-idle state). The mic keeps listening the whole time regardless of
// window visibility, so this is purely cosmetic — no wake word needed to
// bring it back, just start talking. See renderer.js's onStateChange.
ipcMain.on('visibility-show', () => {
  if (win) win.show();
});

ipcMain.on('visibility-hide', () => {
  if (win) win.hide();
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

