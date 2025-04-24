const { app, BrowserWindow, globalShortcut } = require('electron');
const path = require('path');

let win;

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

app.whenReady().then(() => {
  createWindow();

  // Optional: Hotkey to refresh the bubble
  globalShortcut.register('CommandOrControl+Shift+S', () => {
    win.reload();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
