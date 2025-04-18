const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sparkAPI', {
  onListen: (callback) => ipcRenderer.on('trigger-listen', callback),
});
