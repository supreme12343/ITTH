const assert = require('node:assert/strict');
const { test } = require('node:test');

process.env.USE_HTTP = 'true';
const { forwardQueuedCommands, annotationSlideUrl, closeForTests } = require('../server');

test('rapid commands retain their order and every navigation step', async () => {
    const queued = [
        { command: 'NEXT' },
        { command: 'NEXT' },
        { command: 'BLACKOUT' },
        { command: 'PREVIOUS' },
        { command: 'JUMP', target: 8 }
    ];
    const forwarded = [];
    await forwardQueuedCommands(queued, async command => {
        await new Promise(resolve => setTimeout(resolve, command.command === 'NEXT' ? 2 : 0));
        forwarded.push(command);
    });
    assert.deepEqual(forwarded, queued);
    closeForTests();
});

test('legacy deck-scoped delete targets the live slide-only Python route', () => {
    assert.equal(annotationSlideUrl('7'), 'http://127.0.0.1:9005/annotations/7');
    assert.equal(annotationSlideUrl('7/other'), null);
    assert.equal(annotationSlideUrl('-1'), null);
});
