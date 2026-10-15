'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const { StringDecoder } = require('node:string_decoder');
const assert = require('node:assert/strict');
const test = require('node:test');

const services = path.resolve(__dirname, '../app/services');
const vendor = fs.readFileSync(path.resolve(__dirname, '../vendor/quarkclouddrive/quark-drive.cjs'));
const bridge = fs.readFileSync(path.join(services, 'quark_read_bridge.cjs'), 'utf8');
const cliPath = path.resolve('/private/tmp/isolated-quark-read-test/quark-drive.cjs');
const sourceLine = vendor.toString('utf8').split('\n')[44];
const methodStart = sourceLine.indexOf('waitForTransportDrain(){');
const method = sourceLine.slice(methodStart).match(/^waitForTransportDrain\(\)\{return new Promise\(e=>\{setTimeout\(e,uw\)\}\)\}/)?.[0];
assert.ok(method, 'pinned diagnostic drain method must retain its audited source location');
const drainClass = ' '.repeat(methodStart - 'class ju{'.length) + 'class ju{' + method + '}';
const probeLine = vendor.toString('utf8').split('\n')[68];
const probeStart = probeLine.indexOf('async waitForReportBudget(');
const probeMethod = probeLine.slice(probeStart).match(/^async waitForReportBudget\(e,t,r\)\{let n=-1;for\(;n!==this.reportedSpanCount;\)\{n=this.reportedSpanCount;let o=Math.max\(r,n\),i=t\*o-\(Date.now\(\)-e\);i>0&&await new Promise\(a=>setTimeout\(a,i\)\)\}\}/)?.[0];
assert.ok(probeMethod, 'pinned diagnostic budget must retain its audited source location');
const probeClass = ' '.repeat(probeStart - 'class _l{'.length) + 'class _l{' + probeMethod + '}';

function runtime(command = 'browse', options = {}) {
  const timers = [], writes = [];
  const stdout = {
    writableNeedDrain: false, writableLength: 0,
    write(...args) { writes.push({ receiver: this, args }); return options.writeReturn ?? true; },
  };
  const originalTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args };
    timers.push(handle);
    return handle;
  };
  const context = vm.createContext({
    Buffer, uw: 5000, process: { argv: ['node', cliPath, command], stdout },
    setTimeout: originalTimeout,
    require(name) {
      if (name === 'node:fs') return { readFileSync: () => options.invalidHash ? Buffer.from('different runtime') : vendor };
      if (name === 'node:path') return path;
      if (name === 'node:crypto') return crypto;
      if (name === 'node:string_decoder') return { StringDecoder };
      throw new Error('unexpected dependency');
    },
  });
  context.global = context;
  vm.runInContext(bridge, context, { filename: path.join(services, 'quark_read_bridge.cjs') });
  vm.runInContext(drainClass, context, { filename: options.wrongCaller ? '/private/tmp/other-script.cjs' : cliPath, lineOffset: options.wrongPosition ? 43 : 44 });
  vm.runInContext(probeClass, context, { filename: options.wrongCaller ? '/private/tmp/other-script.cjs' : cliPath, lineOffset: options.wrongPosition ? 67 : 68 });
  return {
    context, stdout, timers, writes, originalTimeout,
    emit(event) { stdout.write(JSON.stringify(event) + '\n'); },
    drain() { vm.runInContext('new ju().waitForTransportDrain()', context); return timers.at(-1); },
    probe(count = 8) {
      context.probeCount = count;
      vm.runInContext('var probe = new _l(); probe.reportedSpanCount = probeCount; probe.waitForReportBudget(Date.now(), 650, 2)', context);
      return timers.at(-1);
    },
  };
}

const success = command => ({ type: 'result', action: command, code: 0, data: { total: 1 } });
const artifact = command => ({ type: 'artifact', action: command, code: 0, data: { file_path: '/private/results.jsonl', count: 1, format: 'jsonl' } });

test('pinned vendor and Python adapter agree on the audited runtime', () => {
  const python = fs.readFileSync(path.join(services, 'quark_drive_auth.py'), 'utf8');
  const expected = python.match(/VENDOR_SHA256 = "([a-f0-9]{64})"/)[1];
  assert.equal(crypto.createHash('sha256').update(vendor).digest('hex'), expected);
  assert.ok(bridge.includes(expected));
});

test('browse needs both successful result and full artifact', () => {
  const r = runtime();
  r.emit(success('browse'));
  assert.equal(r.drain().delay, 5000);
  r.emit(artifact('browse'));
  assert.equal(r.drain().delay, 100);
});

test('search preserves artifact completeness and user info needs only its result', () => {
  const search = runtime('search');
  search.emit(success('search')); search.emit(artifact('search'));
  assert.equal(search.drain().delay, 100);
  const user = runtime('get-user-info');
  assert.equal(user.drain().delay, 5000);
  user.emit(success('get-user-info'));
  assert.equal(user.drain().delay, 100);
});

test('only the audited dynamic configuration probe budget is shortened', () => {
  const pending = runtime('get-user-info');
  assert.ok(pending.probe().delay > 5100);
  const ready = runtime('get-user-info'); ready.emit(success('get-user-info'));
  assert.equal(ready.probe().delay, 100);
  const lowCount = runtime(); lowCount.emit(success('browse')); lowCount.emit(artifact('browse'));
  assert.equal(lowCount.probe(2).delay, 100);
  for (const options of [{ invalidHash: true }, { wrongCaller: true }, { wrongPosition: true }]) {
    const fallback = runtime('get-user-info', options); fallback.emit(success('get-user-info'));
    assert.ok(fallback.probe().delay > 5100);
  }
});

test('write/login commands, different hash, caller and source position remain unchanged', () => {
  for (const command of ['share', 'move', 'login', 'upload', 'download', 'unauthorize']) {
    const r = runtime(command); r.emit(success(command)); r.emit(artifact(command));
    assert.equal(r.drain().delay, 5000, command);
  }
  for (const options of [{ invalidHash: true }, { wrongCaller: true }, { wrongPosition: true }]) {
    const r = runtime('browse', options); r.emit(success('browse')); r.emit(artifact('browse'));
    assert.equal(r.drain().delay, 5000);
  }
});

test('failures, malformed protocol, invalid artifact and mismatched action never speed up', () => {
  const laterFailure = runtime();
  laterFailure.emit(success('browse')); laterFailure.emit(artifact('browse'));
  laterFailure.emit({ type: 'result', action: 'browse', code: -103, data: {} });
  assert.equal(laterFailure.drain().delay, 5000);
  const malformed = runtime();
  malformed.emit(success('browse')); malformed.emit(artifact('browse'));
  malformed.stdout.write('{invalid json}\n');
  assert.equal(malformed.drain().delay, 5000);
  const invalid = runtime();
  invalid.emit(success('browse')); invalid.emit({ ...artifact('browse'), data: { file_path: 'relative.jsonl', count: 1, format: 'jsonl' } });
  assert.equal(invalid.drain().delay, 5000);
  const mismatched = runtime();
  mismatched.emit(success('search')); mismatched.emit(artifact('search'));
  assert.equal(mismatched.drain().delay, 5000);
});

test('partial lines and split UTF-8 retain the original stdout stream', () => {
  const r = runtime();
  const bytes = Buffer.from(JSON.stringify({ ...success('browse'), msg: '真实读取' }) + '\n');
  const split = bytes.indexOf(Buffer.from('真实')) + 1;
  r.stdout.write(bytes.subarray(0, split));
  assert.equal(r.drain().delay, 5000);
  r.stdout.write(bytes.subarray(split)); r.emit(artifact('browse'));
  assert.equal(r.drain().delay, 100);
  assert.deepEqual(Buffer.concat(r.writes.slice(0, 2).map(record => record.args[0])), bytes);
});

test('oversized output and pending stdout writes conservatively keep normal timing', () => {
  const oversized = runtime();
  oversized.stdout.write('x'.repeat(65537));
  oversized.emit(success('browse')); oversized.emit(artifact('browse'));
  assert.equal(oversized.drain().delay, 5000);
  const pending = runtime(); pending.emit(success('browse')); pending.emit(artifact('browse'));
  pending.stdout.writableNeedDrain = true;
  assert.equal(pending.drain().delay, 5000);
  pending.stdout.writableNeedDrain = false; pending.stdout.writableLength = 12;
  assert.equal(pending.drain().delay, 5000);
});

test('stdout callbacks, return value and receiver plus unrelated timers are preserved', () => {
  const r = runtime('get-user-info', { writeReturn: false });
  const callback = () => {};
  assert.equal(r.stdout.write('unchanged\n', 'utf8', callback), false);
  assert.equal(r.writes[0].receiver, r.stdout);
  assert.equal(r.writes[0].args[2], callback);
  r.emit(success('get-user-info'));
  const handle = vm.runInContext('setTimeout(() => {}, 5000, "original argument")', r.context);
  assert.equal(handle.delay, 5000);
  assert.equal(handle.args[0], 'original argument');
  r.context.uw = 500;
  assert.equal(r.drain().delay, 500);
});
