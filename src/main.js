const { app, BrowserWindow, globalShortcut } = require('electron');
const path = require('path');

let win;

function createWindow() {
  win = new BrowserWindow({
    width: 200,
    height: 200,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, '../preload.js'),
    }
  });

  win.loadFile('public/index.html');
  win.setIgnoreMouseEvents(false); // can be toggled to let clicks pass through
}

app.whenReady().then(() => {
  createWindow();

  // HOTKEY: Cmd+Shift+M
  globalShortcut.register('CommandOrControl+Shift+M', () => {
    win.webContents.send('trigger-listen');
    console.log('[Spark] Hotkey pressed - Listening triggered');
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
