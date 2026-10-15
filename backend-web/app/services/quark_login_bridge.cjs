/* Deliver the official OAuth URL to the web UI instead of opening a server browser.
 * The unmodified official CLI still polls Quark and exchanges/saves its tokens.
 * Loaded only for interactive login, in the requesting site's private runtime.
 */
'use strict';
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const originalExec = childProcess.exec;

childProcess.exec = function (command, options, callback) {
  const done = typeof options === 'function' ? options : callback;
  const match = typeof command === 'string'
    ? /^(?:open|xdg-open|start "") "(https:\/\/pan\.quark\.cn\/open\/v1\/oauth\/agent\?[^"\r\n]+)"$/.exec(command)
    : null;
  if (!match) return originalExec.apply(this, arguments);
  const url = new URL(match[1]);
  if (url.hostname !== 'pan.quark.cn' || !url.searchParams.get('page_code')) {
    return originalExec.apply(this, arguments);
  }
  // A real provider URL, not a substitute for provider authentication.
  process.stdout.write(JSON.stringify({
    type: 'authorization', data: { authorization_url: url.toString() },
  }) + '\n');
  const child = new EventEmitter();
  process.nextTick(() => {
    if (done) done(null, '', '');
    child.emit('exit', 0);
    child.emit('close', 0);
  });
  return child;
};
