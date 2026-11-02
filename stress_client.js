const io = require('socket.io-client');

const SERVER = 'http://127.0.0.1:8000';
const TOTAL = 200;
const INTERVAL_MS = 20;

async function run() {
  const socket = io(SERVER, { transports: ['websocket', 'polling'] });

  socket.on('connect', async () => {
    console.log('connected', socket.id);
    for (let i = 0; i < TOTAL; i++) {
      const payload = (i % 3 === 0)
        ? { command: 'NEXT' }
        : (i % 3 === 1)
          ? { command: 'PREVIOUS' }
          : { command: 'JUMP', target: 1 };

      socket.emit('hardware_command', payload);
      if (i % 50 === 0) console.log('sent', i);
      await new Promise(r => setTimeout(r, INTERVAL_MS));
    }

    console.log('done sending');
    setTimeout(() => { socket.disconnect(); process.exit(0); }, 500);
  });

  socket.on('connect_error', (err) => {
    console.error('connect_error', err);
    process.exit(2);
  });
}

run();
