const io = require('socket.io-client');

const SERVER = 'http://127.0.0.1:8000';
const CLIENTS = 10;
const MESSAGES_PER_CLIENT = 2000;
const BATCH_LOG = 500;

function startClient(id) {
  return new Promise((resolve) => {
    const socket = io(SERVER, { transports: ['websocket', 'polling'] });
    let sent = 0;

    socket.on('connect', async () => {
      // Flood messages as quickly as possible
      for (let i = 0; i < MESSAGES_PER_CLIENT; i++) {
        const payload = (i % 4 === 0) ? { command: 'NEXT' } : (i % 4 === 1) ? { command: 'PREVIOUS' } : (i % 4 === 2) ? { command: 'BLACKOUT' } : { command: 'JUMP', target: 1 };
        socket.emit('hardware_command', payload);
        sent++;
        if (sent % BATCH_LOG === 0) console.log(`client ${id} sent ${sent}`);
      }
      console.log(`client ${id} done`);
      socket.disconnect();
      resolve(sent);
    });

    socket.on('connect_error', (err) => {
      console.error('client connect_error', err);
      resolve(0);
    });
  });
}

(async () => {
  console.log(`Starting ${CLIENTS} clients, ${MESSAGES_PER_CLIENT} messages each`);
  const promises = [];
  for (let i = 0; i < CLIENTS; i++) promises.push(startClient(i));
  const results = await Promise.all(promises);
  const total = results.reduce((a,b) => a+b, 0);
  console.log(`Completed. Total messages sent: ${total}`);
  process.exit(0);
})();
