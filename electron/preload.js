const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  onProgress: (callback) => {
    ipcRenderer.on('startup-progress', (_event, message) => callback(message));
  },
  onError: (callback) => {
    ipcRenderer.on('startup-error', (_event, message) => callback(message));
  }
});
