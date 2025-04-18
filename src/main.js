const { app, BrowserWindow, globalShortcut } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

let win;
let whisperProcess;

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
  
    globalShortcut.register('CommandOrControl+Shift+S', () => {
      if (whisperProcess) {
        whisperProcess.kill();
        whisperProcess = null;
      }

      win.webContents.send('trigger-listen'); // 👈 make bubble appear again
  
      const modelPath = path.join(__dirname, '../whisper/models/ggml-base.en.bin');
      const binaryPath = path.join(__dirname, '../whisper/build/bin/whisper-stream');
  
      whisperProcess = spawn(binaryPath, [
        '-m', modelPath,
        '-t', '4',
        '--no-gpu',
        '-c', '1',
        '--step', '5000',
        '--keep', '100',
        '--vad-thold', '0.8',
      ]);
      
      
  
      whisperProcess.stdout.on('data', (data) => {
        const output = data.toString().trim();
      
        // Ignore Whisper debug stuff
        if (
          output.length > 0 &&
          !output.includes('init:') &&
          !output.includes('whisper_') &&
          !output.includes('model_load:') &&
          !output.includes('main:') &&
          !output.includes('[ Silence ]') &&
          !output.includes('[BLANK_AUDIO]')
        ) {
          console.log(`[Spark Whisper] ${output}`);
          win.webContents.send('transcription', output);
        }
      });
      
  
      whisperProcess.stderr.on('data', (data) => {
        const error = data.toString().trim();
        if (error.length > 0) {
          console.error(`[Spark Whisper STDERR] ${error}`);
        }
      });
      
  
      whisperProcess.on('exit', () => {
        console.log('[Whisper] Process ended.');
      });
    });
  });
  
  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    if (whisperProcess) whisperProcess.kill();
  });