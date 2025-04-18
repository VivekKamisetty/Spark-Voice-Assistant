const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sparkAPI', {
  onListen: (callback) => ipcRenderer.on('trigger-listen', callback),
  onTranscription: (callback) => ipcRenderer.on('transcription', callback),
});
