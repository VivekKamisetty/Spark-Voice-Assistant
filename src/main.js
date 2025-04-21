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
let chatHistory = [
  {
    role: 'system',
    content: 'You are Spark, a helpful and concise voice assistant.'
  }
];

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

        if (!output || output.trim().length === 0 || ['"', "''", '""'].includes(output)) {
          console.log(`[Spark] Ignored totally blank or quote-only output: "${output}"`);
          return;
        }
      
        // ==========================
        // 🎯 Filtering low-value inputs
        // ==========================
        const cleanedOutput = output.toLowerCase().replace(/["'\[\]]/g, '').trim();

        const tooShort = cleanedOutput.length < 4;
        const symbolsOnly = /^\W+$/.test(cleanedOutput);

        const knownNoise = [
          'blank_audio',
          'silence',
          'noise',
          'init:',
          'whisper_',
          'model_load:',
          'main:'
        ].some(sub => cleanedOutput.includes(sub));

        const lowIntent = [
          'uh', 'hmm', 'mmm', 'okay', 'yes', 'no', 'i see',
          'sure', 'huh', 'yo', 'hi', 'hello', 'spark', 'hey',
          'can you help', 'can i help', 'help me', 'what', 'can you'
        ].some(p => cleanedOutput.includes(p));

        if (tooShort || symbolsOnly || knownNoise || lowIntent) {
          console.log(`[Spark] Ignored low-value input: "${output}"`);
          return;
        }
      
        console.log(`[Spark Whisper] ${output}`);
      
        try {
          // ✅ Add user message to history
          chatHistory.push({ role: 'user', content: output });
      
          const completion = await openai.chat.completions.create({
            model: 'gpt-3.5-turbo-0125',
            messages: chatHistory
          });
      
          const reply = completion.choices[0].message.content;
      
          // ✅ Add assistant message to history
          chatHistory.push({ role: 'assistant', content: reply });
      
          // ✅ Limit to last 20 turns
          if (chatHistory.length > 20) {
            chatHistory = chatHistory.slice(-18);
            chatHistory.unshift({
              role: 'system',
              content: 'You are Spark, a helpful and concise voice assistant.'
            });
          }
      
          console.log(`[Spark GPT] ${reply}`);
          win.webContents.send('gpt-response', reply);
        } catch (err) {
          console.error(`[Spark GPT Error]`, err.message);
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