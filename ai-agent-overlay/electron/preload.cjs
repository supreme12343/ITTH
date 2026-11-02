const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('campusApi', {
  listApps: () => ipcRenderer.invoke('campus:list-apps'),
  getTheme: () => ipcRenderer.invoke('campus:get-theme'),
  setTheme: (mode) => ipcRenderer.invoke('campus:set-theme', mode),
  launchApp: (appId) => ipcRenderer.invoke('campus:launch-app', appId),
  windowAction: (action) => ipcRenderer.invoke('campus:window-action', action),
  // A tool can start and then die a moment later (a missing Python package, say),
  // which launchApp has already reported as a success. This carries the reason.
  onToolFailed: (callback) => {
    const listener = (_event, detail) => callback(detail);
    ipcRenderer.on('campus:tool-failed', listener);
    return () => ipcRenderer.removeListener('campus:tool-failed', listener);
  },
});
