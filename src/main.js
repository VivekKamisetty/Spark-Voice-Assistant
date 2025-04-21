const { app, BrowserWindow, globalShortcut } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

require('dotenv').config();
const { OpenAI } = require('openai');

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY
});


let win;
let whisperProcess;

const { screen } = require('electron');

function createWindow() {
  const { width } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width: 200,
    height: 200,
    x: width - 240, // places window near top-right (with padding)
    y: 40,
    frame: false,
    transparent: true,
    focusable: false,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, '../preload.js'),
      nodeIntegration: false, // make sure this is false
      contextIsolation: true,
      enableRemoteModule: true
    }
  });

  win.loadFile('public/index.html');
  win.setIgnoreMouseEvents(true);
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
      
      
  
      whisperProcess.stdout.on('data', async (data) => {
        const output = data.toString().trim();
      
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
      
          try {
            const completion = await openai.chat.completions.create({
              model: 'gpt-3.5-turbo-0125',
              messages: [
                {
                  role: 'system',
                  content: 'You are Spark, a helpful and concise voice assistant.'
                },
                {
                  role: 'user',
                  content: output
                }
              ]
            });
      
            const reply = completion.choices[0].message.content;
            console.log(`[Spark GPT] ${reply}`);
            win.webContents.send('gpt-response', reply);
          } catch (err) {
            console.error(`[Spark GPT Error]`, err.message);
          }
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