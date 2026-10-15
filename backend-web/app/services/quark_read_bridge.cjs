/* Shorten audited diagnostic budgets in the pinned CLI after a complete read.
 * Business requests, artifact writes, other timers and process exit are retained.
 * An unmatched version, output or call site keeps the original timing.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { StringDecoder } = require('node:string_decoder');

const PINNED_CLI_SHA256 = '0e132196bea28114cffd248b055f2e1bc64a521cb16c5cb5aba5e786199e572e';
const READ_COMMANDS = new Set(['get-user-info', 'browse', 'search']);
const command = process.argv[2];
let cliPath;
let enabled = false;
try {
  cliPath = path.resolve(process.argv[1] || '');
  enabled = READ_COMMANDS.has(command)
    && path.basename(cliPath) === 'quark-drive.cjs'
    && crypto.createHash('sha256').update(fs.readFileSync(cliPath)).digest('hex') === PINNED_CLI_SHA256;
} catch {
  // No optimization when the known runtime cannot be verified.
}

if (enabled) {
  const originalWrite = process.stdout.write;
  const originalTimeout = global.setTimeout;
  const decoder = new StringDecoder('utf8');
  const MAX_TRACKED_LINE_BYTES = 64 * 1024;
  let buffered = '';
  let successfulResult = false;
  let completeArtifact = false;
  let disabled = false;

  function observeLine(line) {
    let event;
    try { event = JSON.parse(line); } catch {
      if (/^\s*[\[{]/.test(line)) disabled = true;
      return;
    }
    if (!event || typeof event !== 'object' || event.action !== command) return;
    if (event.type === 'result') {
      successfulResult = event.code === 0 && !!event.data && typeof event.data === 'object'
        && !Array.isArray(event.data);
      if (!successfulResult) disabled = true;
    } else if (event.type === 'artifact') {
      const data = event.data;
      completeArtifact = event.code === 0 && !!data && data.format === 'jsonl'
        && typeof data.file_path === 'string' && path.isAbsolute(data.file_path)
        && Number.isSafeInteger(data.count) && data.count >= 0;
      if (!completeArtifact) disabled = true;
    }
  }

  process.stdout.write = function observeReadOutput(chunk, encoding) {
    if (!disabled) {
      try {
        const bytes = typeof chunk === 'string'
          ? Buffer.from(chunk, typeof encoding === 'string' ? encoding : 'utf8')
          : Buffer.from(chunk);
        buffered += decoder.write(bytes);
        let newline;
        while ((newline = buffered.indexOf('\n')) !== -1) {
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          if (Buffer.byteLength(line, 'utf8') > MAX_TRACKED_LINE_BYTES) {
            disabled = true;
            break;
          }
          observeLine(line);
        }
        if (Buffer.byteLength(buffered, 'utf8') > MAX_TRACKED_LINE_BYTES) disabled = true;
        if (disabled) buffered = '';
      } catch {
        disabled = true;
        buffered = '';
      }
    }
    // Preserve stdout bytes, encoding, callback, return value and backpressure.
    return Reflect.apply(originalWrite, this, arguments);
  };

  global.setTimeout = function shortenDiagnosticDrain(callback, delay, ...args) {
    if (!disabled && successfulResult && (command === 'get-user-info' || completeArtifact)
        && !process.stdout.writableNeedDrain && (process.stdout.writableLength || 0) === 0
        && typeof delay === 'number' && Number.isFinite(delay) && delay > 100) {
      const stack = new Error().stack;
      const frames = typeof stack === 'string' ? stack.split('\n').map(frame => frame.trim()) : [];
      const transportDrain = delay === 5000 && frames.includes(`at ${cliPath}:45:19110`)
        && frames.includes(`at ju.waitForTransportDrain (${cliPath}:45:19094)`);
      const probeBudget = frames.includes(`at ${cliPath}:69:3535`)
        && frames.includes(`at _l.waitForReportBudget (${cliPath}:69:3520)`);
      if (transportDrain || probeBudget) {
        return originalTimeout(callback, 100, ...args);
      }
    }
    return originalTimeout(callback, delay, ...args);
  };
}
