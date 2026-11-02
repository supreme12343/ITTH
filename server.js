const express = require('express');
const http = require('http');
const https = require('https');
const { Server } = require('socket.io');
const fs = require('fs');
const path = require('path');
const dgram = require('dgram');

// Allow switching to plain HTTP for local development: set USE_HTTP=true or NODE_ENV=development
const USE_HTTP = process.env.USE_HTTP === 'true' || process.env.NODE_ENV === 'development';
const { resolveBindHost } = require('./server-bind-host');
const BIND_HOST = resolveBindHost(USE_HTTP, process.env.NODE_BIND_HOST);

// --- CONFIGURATION ---
const PORT = parseInt(process.env.NODE_FRONTEND_PORT || '8000', 10);
const PYTHON_CMD_URL = process.env.PYTHON_CMD_URL || "http://127.0.0.1:9005/command";
const PYTHON_AUDIO_PORT = parseInt(process.env.UDP_AUDIO_PORT || '5005', 10);
const PYTHON_SYNC_URL = process.env.PYTHON_SYNC_URL || "http://127.0.0.1:9005/sync";
const PYTHON_ANNOTATIONS_URL = process.env.PYTHON_ANNOTATIONS_URL || "http://127.0.0.1:9005/annotations";
const PYTHON_API_ORIGIN = new URL(PYTHON_SYNC_URL).origin;

// --- ULTRA-LOW LATENCY TUNING ---
// Persistent HTTP agent with connection pooling for rapid fire commands
const pythonHttpAgent = new http.Agent({
    keepAlive: true,
    maxSockets: 50,
    maxFreeSockets: 10,
    timeout: 60000,
    keepAliveMsecs: 30000
});
const pythonHttpsAgent = new https.Agent({
    keepAlive: true,
    maxSockets: 50,
    maxFreeSockets: 10,
    keepAliveMsecs: 30000
});

// Command queue for batching rapid clicks
const commandQueue = [];
let commandQueueTimer = null;
const COMMAND_BATCH_WINDOW = 0; // Dispatch on the next event-loop turn; keep input latency low.
const annotationSocketQueues = new WeakMap();

function enqueueSocketAnnotation(socket, work) {
    const previous = annotationSocketQueues.get(socket) || Promise.resolve();
    const next = previous.catch(() => {}).then(work);
    annotationSocketQueues.set(socket, next);
    return next;
}

function annotationPayload(data) {
    return {
        stroke_id: data.stroke_id,
        tool: data.tool,
        color: data.color,
        width: data.width,
        points: data.points,
        ppt_points: data.ppt_points,
        canvas_width: data.canvas_width,
        canvas_height: data.canvas_height,
        slide_aspect: data.slide_aspect,
        text: data.text,
        font_size: data.font_size,
        slide_index: data.slide_index
    };
}

// --- SSL CERTIFICATE SETUP (conditional for dev-mode) ---
let sslOptions;
if (!USE_HTTP) {
    try {
        sslOptions = {
            key: fs.readFileSync(path.join(__dirname, 'key.pem')),
            cert: fs.readFileSync(path.join(__dirname, 'cert.pem'))
        };
    } catch (error) {
        console.error("\n[CRITICAL ERROR] Could not find key.pem or cert.pem!");
        console.error("Make sure you generated the SSL certificates in this exact folder.\n");
        process.exit(1);
    }
}

// Initialize Express
const app = express();

// Create HTTP or HTTPS server depending on environment
let server;
if (USE_HTTP) {
    server = http.createServer(app);
    console.log("[CONFIG] Starting Node server in HTTP development mode (USE_HTTP=true).");
} else {
    server = https.createServer(sslOptions, app);
}

app.use(express.json());

// --- SOCKET.IO (OPTIMIZED FOR LATENCY) ---
const io = new Server(server, {
    cors: { origin: "*", methods: ["GET", "POST"] },
    perMessageDeflate: false, // Disabling compression speeds up binary audio passing
    transports: ['websocket', 'polling'],
    pingTimeout: 10000,
    pingInterval: 5000,
    maxHttpBufferSize: 1e6
});

// --- UDP AUDIO FIREHOSE ---
const udpClient = dgram.createSocket('udp4');
let isUdpActive = true;

udpClient.on('error', (err) => {
    console.error(`[UDP CRITICAL] Audio pipeline fault detected:\n${err.stack}`);
    isUdpActive = false;
});

// --- DIRECTORY SETUP ---
const publicDir = path.join(__dirname, 'public');
const thumbDir = path.join(publicDir, 'thumbnails');

if (!fs.existsSync(publicDir)) fs.mkdirSync(publicDir, { recursive: true });
if (!fs.existsSync(thumbDir)) fs.mkdirSync(thumbDir, { recursive: true });

app.use(express.static(publicDir));
app.use('/thumbnails', express.static(thumbDir));

// --- PYTHON BACKEND COMMUNICATION (OPTIMIZED) ---
async function callPythonBackend(method, url, data = null, timeoutMs = 3000) {
    return new Promise((resolve) => {
        const payload = data ? JSON.stringify(data) : null;
        const parsedUrl = new URL(url);
        if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
            resolve({ ok: false, error: `Unsupported backend protocol: ${parsedUrl.protocol}` });
            return;
        }
        const transport = parsedUrl.protocol === 'https:' ? https : http;
        const agent = parsedUrl.protocol === 'https:' ? pythonHttpsAgent : pythonHttpAgent;
        
        const options = {
            hostname: parsedUrl.hostname,
            port: parsedUrl.port,
            path: `${parsedUrl.pathname}${parsedUrl.search}`,
            method: method,
            agent,
            headers: payload ? {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(payload)
            } : {},
            timeout: timeoutMs
        };

        const start = Date.now();
        const req = transport.request(options, (res) => {
            let body = '';
            res.on('data', (chunk) => body += chunk);
            res.on('end', () => {
                const latency = Date.now() - start;
                try {
                    const parsed = body ? JSON.parse(body) : null;
                    resolve({ ok: res.statusCode === 200, statusCode: res.statusCode, body: parsed, latency });
                } catch (e) {
                    resolve({ ok: res.statusCode === 200, statusCode: res.statusCode, body, latency });
                }
            });
        });

        req.on('error', (err) => {
            resolve({ ok: false, error: err.message, latency: Date.now() - start });
        });
        req.on('timeout', () => {
            req.destroy();
            resolve({ ok: false, error: 'timeout', latency: Date.now() - start });
        });

        if (payload) req.write(payload);
        req.end();
    });
}

// Fire-and-forget command to Python (for non-critical latency)
function sendCommandToPythonFireAndForget(data) {
    return new Promise((resolve) => {
    const payload = JSON.stringify(data);
    const pythonUrl = new URL(PYTHON_CMD_URL);
    if (!['http:', 'https:'].includes(pythonUrl.protocol)) {
        console.error(`[PYTHON BACKEND] Unsupported command URL protocol: ${pythonUrl.protocol}`);
        resolve();
        return;
    }
    const transport = pythonUrl.protocol === 'https:' ? https : http;
    const agent = pythonUrl.protocol === 'https:' ? pythonHttpsAgent : pythonHttpAgent;
    
    const options = {
        hostname: pythonUrl.hostname,
        port: pythonUrl.port,
        path: `${pythonUrl.pathname}${pythonUrl.search}`,
        method: 'POST',
        agent,
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payload)
        },
        timeout: 2000
    };

    const req = transport.request(options, (res) => {
        res.on('data', () => {}); // Consume response
        res.on('end', resolve);
    });
    
    req.on('error', resolve); // Keep later commands moving on a backend failure.
    req.on('timeout', () => req.destroy());
    
    req.write(payload);
    req.end();
    });
}

// Batched command processor for rapid clicks
let commandDispatch = Promise.resolve();
function queueCommandForPython(data) {
    commandQueue.push(data);
    
    if (commandQueueTimer) return;
    
    commandQueueTimer = setTimeout(() => {
        commandQueueTimer = null;
        const commands = [...commandQueue];
        commandQueue.length = 0;
        
        commandDispatch = commandDispatch.then(() =>
            forwardQueuedCommands(commands, sendCommandToPythonFireAndForget)
        ).catch(error => console.error('[COMMAND] Dispatch failed:', error));
    }, COMMAND_BATCH_WINDOW);
}

async function forwardQueuedCommands(commands, send) {
    // Preserve the order and count of rapid navigation clicks.
    for (const command of commands) await send(command);
}

// --- GLOBAL STATE ---
let systemState = {
    currentSlide: 1,
    totalSlides: 1,
    aiListening: false,
    activeDeck: null,
    presentationName: '',
    presentationId: null,
    thumbnailRevision: 0,
    decks: []
};
let aiMicSocketId = null;

function getActiveDeckList() {
    if (!systemState.presentationId) return [];
    return [{
        id: systemState.presentationId,
        name: systemState.presentationName || 'Untitled presentation',
        current_slide: systemState.currentSlide,
        total_slides: systemState.totalSlides,
        is_active: true
    }];
}

function readDeckData() {
    return new Promise((resolve) => {
        fs.readdir(thumbDir, (err, files) => {
            if (err || !systemState.activeDeck) return resolve([]);
            const images = files
                .filter((file) => /^slide_\d+\.(?:jpe?g|png)$/i.test(file))
                .sort((left, right) => {
                    const leftIndex = Number(left.match(/\d+/)?.[0] || 0);
                    const rightIndex = Number(right.match(/\d+/)?.[0] || 0);
                    return leftIndex - rightIndex;
                });
            resolve(images.map((file, index) => ({
                slideNumber: index + 1,
                url: `/thumbnails/${file}`
            })));
        });
    });
}

function toPositiveInteger(value) {
    // Number.parseInt("2abc", 10) returns 2, which can turn malformed
    // navigation/sync input into a valid slide number.
    if (typeof value !== 'number' && typeof value !== 'string') return null;
    const parsed = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

// --- BI-DIRECTIONAL BACKEND SYNCHRONIZATION ---
app.post('/sync', async (req, res) => {
    const {
        currentSlide, totalSlides, presentationName, presentationId, thumbnailRevision
    } = req.body || {};
    const validTotalSlides = toPositiveInteger(totalSlides);
    const validCurrentSlide = toPositiveInteger(currentSlide);
    const validThumbnailRevision = toPositiveInteger(thumbnailRevision);
    const previousDeckId = systemState.presentationId;
    const previousRevision = systemState.thumbnailRevision;
    const previousTotal = systemState.totalSlides;
    const hasPresentationId = Object.prototype.hasOwnProperty.call(req.body || {}, 'presentationId');
    const validPresentationId = typeof presentationId === 'string' && /^[a-f0-9]{16}$/i.test(presentationId)
        ? presentationId
        : null;

    if (validTotalSlides !== null) systemState.totalSlides = validTotalSlides;
    if (validCurrentSlide !== null) {
        systemState.currentSlide = validCurrentSlide;
    }
    if (typeof presentationName === 'string') systemState.presentationName = presentationName;
    if (hasPresentationId) {
        systemState.presentationId = validPresentationId;
        systemState.activeDeck = validPresentationId;
        if (!validPresentationId) {
            systemState.presentationName = '';
            systemState.currentSlide = 1;
            systemState.totalSlides = 0;
            systemState.thumbnailRevision = 0;
        }
    }
    if (validThumbnailRevision !== null) systemState.thumbnailRevision = validThumbnailRevision;
    systemState.currentSlide = Math.max(1, Math.min(systemState.totalSlides, systemState.currentSlide));
    systemState.decks = getActiveDeckList();
    const deckContentChanged = previousDeckId !== systemState.presentationId ||
        previousRevision !== systemState.thumbnailRevision || previousTotal !== systemState.totalSlides;

    // Instantly update the phone's UI
    io.emit('system_sync', systemState);
    if (deckContentChanged) {
        io.emit('deck_list', systemState.decks);
        readDeckData().then((slides) => io.emit('deck_data_response', slides));
    }
    res.status(200).json({ status: 'node_synchronized' });
});

// Annotation proxy endpoints
app.post('/annotations', async (req, res) => {
    const result = await callPythonBackend('POST', PYTHON_ANNOTATIONS_URL, req.body);
    if (result.ok) {
        res.status(200).json({ status: 'ok' });
    } else {
        res.status(result.statusCode || 500).json({ error: result.error || result.body });
    }
});

function annotationSlideUrl(slideIndex) {
    if (!/^(0|[1-9]\d*)$/.test(String(slideIndex)) || Number(slideIndex) > 9999) return null;
    return `${PYTHON_ANNOTATIONS_URL}/${slideIndex}`;
}

app.delete('/annotations/:deckId/:slideIndex', async (req, res) => {
    const url = annotationSlideUrl(req.params.slideIndex);
    if (!url) return res.status(400).json({ error: 'Invalid slide index' });
    const result = await callPythonBackend('DELETE', url);
    if (result.ok) {
        res.status(200).json({ status: 'ok' });
    } else {
        res.status(result.statusCode || 500).json({ error: result.error || result.body });
    }
});

// --- BULLETPROOF HTML ROUTING ---
app.get('/', (req, res) => {
    const indexPath = path.join(publicDir, 'index.html');

    if (fs.existsSync(indexPath)) {
        return res.sendFile(indexPath);
    } else {
        res.status(404).send(`
            <div style="color:#ef4444; font-family:sans-serif; background:#11111b; padding:20px; border-radius:8px; margin:20px; border:1px solid #7f1d1d;">
                <h2 style="margin-top:0;">UI Asset Missing Error</h2>
                <p>Could not locate <b>index.html</b> inside: <br><code>${publicDir}</code></p>
            </div>
        `);
    }
});

// Health check
app.get('/health', (req, res) => {
    const boundAddress = server.address();
    res.json({
        status: 'ok',
        controller_version: 2,
        bind_host: boundAddress && typeof boundAddress === 'object'
            ? boundAddress.address
            : BIND_HOST,
        timestamp: Date.now(),
        uptime: process.uptime()
    });
});

// --- WEBSOCKET EVENT LOOP ---
io.on('connection', (socket) => {
    console.log(`[NETWORK] Device Connected: ${socket.id}`);

    // Send immediate state sync on connect
    socket.emit('system_sync', systemState);

    socket.on('request_deck_data', async () => {
        socket.emit('deck_data_response', await readDeckData());
    });

    socket.on('request_deck_list', () => {
        systemState.decks = getActiveDeckList();
        socket.emit('deck_list', systemState.decks);
    });

    socket.on('sync_deck', async () => {
        const result = await callPythonBackend('POST', `${PYTHON_API_ORIGIN}/decks/sync`, {}, 120000);
        if (!result.ok || !result.body?.deck) {
            socket.emit('deck_sync_result', {
                error: result.body?.detail || result.error || 'Could not sync the open PowerPoint deck.'
            });
            return;
        }

        const deck = result.body.deck;
        systemState.presentationId = deck.id;
        systemState.presentationName = deck.name;
        systemState.activeDeck = deck.id;
        systemState.currentSlide = deck.current_slide;
        systemState.totalSlides = deck.total_slides;
        systemState.thumbnailRevision = result.body.thumbnail_revision || systemState.thumbnailRevision;
        systemState.decks = getActiveDeckList();
        io.emit('system_sync', systemState);
        io.emit('deck_list', systemState.decks);
        io.emit('deck_data_response', await readDeckData());
        socket.emit('deck_sync_result', { ok: true, message: `${deck.name} synced.` });
    });

    socket.on('request_annotations', async (data) => {
        const slide_index = data?.slide_index ?? 0;
        const request_id = data?.request_id;
        const res = await callPythonBackend('GET', `${PYTHON_ANNOTATIONS_URL}/${slide_index}`);
        if (res.ok && res.body && Array.isArray(res.body.strokes)) {
            socket.emit('annotations_loaded', { slide_index, request_id, strokes: res.body.strokes });
        } else {
            socket.emit('annotations_loaded', {
                slide_index, request_id, error: res.error || res.body || 'Could not load annotations'
            });
        }
    });

    socket.data.annotationPreviewStrokes = new Map();

    socket.on('annotation_preview', (data = {}, acknowledge = () => {}) => {
        enqueueSocketAnnotation(socket, async () => {
            try {
                if (!data.stroke_id || !Array.isArray(data.points) || data.points.length < 2 ||
                    !Number.isInteger(data.slide_index) || !Number.isInteger(data.point_offset) ||
                    !['pen', 'highlighter'].includes(data.tool)) {
                    throw new Error('Invalid annotation preview payload');
                }
                const res = await callPythonBackend(
                    'POST', `${PYTHON_ANNOTATIONS_URL}/preview`,
                    { ...annotationPayload(data), point_offset: data.point_offset }, 3000
                );
                if (res.ok && res.body?.projected) {
                    socket.data.annotationPreviewStrokes.set(data.stroke_id, data.slide_index);
                }
                acknowledge({
                    ok: res.ok,
                    projected: Boolean(res.body?.projected),
                    projection_ms: res.body?.projection_ms,
                    error: res.body?.detail || res.error || null
                });
            } catch (error) {
                acknowledge({ ok: false, projected: false, error: error.message });
            }
        });
    });

    socket.on('annotation_preview_cancel', (data = {}, acknowledge = () => {}) => {
        enqueueSocketAnnotation(socket, async () => {
            const slideIndex = Number.isInteger(data.slide_index)
                ? data.slide_index : socket.data.annotationPreviewStrokes.get(data.stroke_id);
            try {
                if (!data.stroke_id || !Number.isInteger(slideIndex)) {
                    throw new Error('Invalid annotation preview cancellation');
                }
                const result = await callPythonBackend(
                    'POST', `${PYTHON_ANNOTATIONS_URL}/preview/cancel`,
                    { stroke_id: data.stroke_id, slide_index: slideIndex }, 5000
                );
                socket.data.annotationPreviewStrokes.delete(data.stroke_id);
                acknowledge({ ok: result.ok, error: result.body?.detail || result.error || null });
            } catch (error) {
                acknowledge({ ok: false, error: error.message });
            }
        });
    });

    socket.on('annotation', (data = {}) => {
        enqueueSocketAnnotation(socket, async () => {
            try {
                if (!data.stroke_id || !Array.isArray(data.points) || !Number.isInteger(data.slide_index)) {
                    throw new Error('Invalid annotation payload');
                }
                const payload = annotationPayload(data);
                const hadPreview = socket.data.annotationPreviewStrokes.has(data.stroke_id);
                const res = await callPythonBackend('POST', PYTHON_ANNOTATIONS_URL, payload, 10000);
                if (res.ok) {
                    socket.data.annotationPreviewStrokes.delete(data.stroke_id);
                    // Broadcast updated strokes to ALL screens
                    io.emit('annotations_updated', { slide_index: data.slide_index, strokes: res.body.strokes || [] });
                    socket.emit('annotation_confirmed', {
                        stroke_id: data.stroke_id,
                        projected: Boolean(res.body.projected),
                        projection_ms: res.body.projection_ms,
                        projection_error: res.body.projection_error || null
                    });
                } else {
                    const backendError = res.body?.detail || res.error || res.body || 'Could not save annotation';
                    if (hadPreview) {
                        await callPythonBackend(
                            'POST', `${PYTHON_ANNOTATIONS_URL}/preview/cancel`,
                            { stroke_id: data.stroke_id, slide_index: data.slide_index }, 5000
                        );
                        socket.data.annotationPreviewStrokes.delete(data.stroke_id);
                    }
                    socket.emit('annotation_confirmed', { stroke_id: data.stroke_id, error: backendError });
                }
            } catch (e) {
                socket.emit('annotation_confirmed', { stroke_id: data.stroke_id, error: e.message });
            }
        });
    });

    socket.on('clear_annotations', async (data) => {
        const slide_index = data?.slide_index ?? 0;
        const res = await callPythonBackend('DELETE', `${PYTHON_ANNOTATIONS_URL}/${slide_index}`);
        if (res.ok) {
            io.emit('annotations_updated', { slide_index, strokes: [] });
            socket.emit('annotations_cleared', { slide_index });
        } else {
            socket.emit('annotations_cleared', { slide_index, error: res.error || res.body || 'Clear failed' });
        }
    });

    socket.on('undo_annotation', async (data) => {
        const slide_index = data?.slide_index ?? 0;
        const res = await callPythonBackend('POST', `${PYTHON_ANNOTATIONS_URL}/undo`, { slide_index });
        if (res.ok) {
            io.emit('annotations_updated', { slide_index, strokes: res.body.strokes });
            socket.emit('annotation_undone', { slide_index, strokes: res.body.strokes });
        } else {
            socket.emit('annotation_undone', { slide_index, error: res.error || res.body || 'Undo failed' });
        }
    });

    socket.on('redo_annotation', async (data) => {
        const slide_index = data?.slide_index ?? 0;
        const res = await callPythonBackend('POST', `${PYTHON_ANNOTATIONS_URL}/redo`, { slide_index });
        if (res.ok) {
            io.emit('annotations_updated', { slide_index, strokes: res.body.strokes });
            socket.emit('annotation_redone', { slide_index, strokes: res.body.strokes });
        } else {
            socket.emit('annotation_redone', { slide_index, error: res.error || res.body });
        }
    });

    socket.on('switch_deck', async (data) => {
        if (!data || !data.deck_id) return;
        const res = await callPythonBackend('POST', `${PYTHON_API_ORIGIN}/decks/switch`, { deck_id: data.deck_id });
        if (res.ok) {
            const listRes = await callPythonBackend('GET', `${PYTHON_API_ORIGIN}/decks`);
            if (listRes.ok && listRes.body && listRes.body.decks) {
                systemState.decks = listRes.body.decks;
                socket.emit('deck_list', listRes.body.decks);
                systemState.decks = getActiveDeckList();
                socket.emit('deck_switched', {
                    deck_id: systemState.activeDeck,
                    slides: await readDeckData(),
                    current_slide: systemState.currentSlide
                });
            }
        } else {
            socket.emit('error', { message: 'Deck switch failed' });
        }
    });

    socket.on('navigate_deck', async (data) => {
        const direction = data?.direction || 'next';
        const res = await callPythonBackend('POST', `${PYTHON_API_ORIGIN}/decks/navigate`, { direction });
        if (res.ok) {
            const listRes = await callPythonBackend('GET', `${PYTHON_API_ORIGIN}/decks`);
            if (listRes.ok) {
                socket.emit('deck_list', listRes.body.decks);
            }
        }
    });

    socket.on('create_deck', async (data) => {
        const name = data?.name || `Deck ${systemState.decks.length + 1}`;
        const res = await callPythonBackend('POST', `${PYTHON_API_ORIGIN}/decks/create`, { name });
        if (res.ok) {
            const listRes = await callPythonBackend('GET', `${PYTHON_API_ORIGIN}/decks`);
            if (listRes.ok) {
                socket.emit('deck_list', listRes.body.decks);
            }
        }
    });

    // INSTANT COMMAND TRIGGER (Batched for rapid clicks)
    socket.on('hardware_command', (data = {}) => {
        if (typeof data.command !== 'string') return;
        console.log(`[COMMAND] UI Triggered: ${data.command} | Target: ${data.target || 'N/A'}`);

        // Optimistic UI update for immediate feedback on the mobile device
        if (data.command === 'NEXT') {
            systemState.currentSlide = Math.min(systemState.totalSlides, systemState.currentSlide + 1);
        }
        if (data.command === 'PREVIOUS') {
            systemState.currentSlide = Math.max(1, systemState.currentSlide - 1);
        }
        if (data.command === 'JUMP' && data.target) {
            const target = toPositiveInteger(data.target);
            if (target !== null) {
                systemState.currentSlide = Math.max(1, Math.min(systemState.totalSlides, target));
            }
        }

        io.emit('system_sync', systemState);
        
        // Queue for batched Python forwarding
        queueCommandForPython(data);
    });

    // --- MICROPHONE STREAMING LOGIC ---
    socket.on('toggle_ai_mic', (isListening) => {
        systemState.aiListening = Boolean(isListening);
        aiMicSocketId = systemState.aiListening ? socket.id : null;
        console.log(`[AI MIC] Stream Status: ${isListening ? '🔴 LIVE' : '🔇 MUTED'}`);
        io.emit('ai_status_update', systemState.aiListening);
    });

    socket.on('audio_stream_chunk', (audioBuffer) => {
        if (!isUdpActive || !systemState.aiListening || !audioBuffer) return;

        // Safe buffer handling for WebSocket ArrayBuffers
        const bufferChunk = Buffer.from(audioBuffer);
        if (bufferChunk.length === 0) return;

        // Zero-copy UDP dispatch for maximum stream performance
        udpClient.send(bufferChunk, 0, bufferChunk.length, PYTHON_AUDIO_PORT, '127.0.0.1', (err) => {
            if (err) console.error(`[UDP TRANSMIT ERROR] ${err.message}`);
        });
    });

    socket.on('disconnect', () => {
        console.log(`[NETWORK] Device Disconnected: ${socket.id}`);

        for (const [strokeId, slideIndex] of socket.data.annotationPreviewStrokes || []) {
            enqueueSocketAnnotation(socket, async () => {
                await callPythonBackend(
                    'POST', `${PYTHON_ANNOTATIONS_URL}/preview/cancel`,
                    { stroke_id: strokeId, slide_index: slideIndex }, 5000
                );
            });
        }
        if (socket.data.annotationPreviewStrokes) socket.data.annotationPreviewStrokes.clear();

        if (systemState.aiListening && aiMicSocketId === socket.id) {
            systemState.aiListening = false;
            aiMicSocketId = null;
            io.emit('ai_status_update', false);
            console.log("[AI MIC] Connection dropped. Streaming halted.");
        }
    });
});

// --- GRACEFUL SHUTDOWN ---
function gracefulShutdown() {
    console.log("\n[SYSTEM] Initiating graceful shutdown sequence...");
    if (systemState.aiListening) io.emit('ai_status_update', false);

    try { udpClient.close(); } catch (e) {}

    // Clear any pending command batch
    if (commandQueueTimer) {
        clearTimeout(commandQueueTimer);
        commandQueue.length = 0;
    }

    server.close(() => {
        console.log("HTTP/WebSocket Matrix unmounted cleanly.");
        process.exit(0);
    });
    setTimeout(() => process.exit(1), 3000).unref();
}

if (require.main === module) {
process.on('SIGINT', gracefulShutdown);
process.on('SIGTERM', gracefulShutdown);

server.on('error', (error) => {
    console.error(`[SERVER] Failed to bind frontend on port ${PORT}: ${error.message}`);
    try { udpClient.close(); } catch (closeError) {}
    process.exitCode = 1;
});

// --- BOOT SEQUENCE ---
server.listen(PORT, BIND_HOST, () => {
    console.log("\n=========================================================");
    console.log(`🚀 DECKSYNC PRO NODE.JS ROUTER ONLINE (ULTRA-LOW LATENCY EDITION)`);
    console.log(`📡 Listening on: ${USE_HTTP ? 'http' : 'https'}://${BIND_HOST}:${PORT}`);
    console.log(`📁 Target directory mapped to: ${publicDir}`);
    console.log(`🛡️ Persistent TCP Agents & Raw Websockets: ACTIVE`);
    console.log(`⚡ Command Batching: ${COMMAND_BATCH_WINDOW}ms window`);
    console.log("=========================================================\n");
});
}

module.exports = {
    forwardQueuedCommands,
    annotationSlideUrl,
    closeForTests: () => {
        io.close();
        udpClient.close();
    }
};
