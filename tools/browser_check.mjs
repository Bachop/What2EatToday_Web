// Real browser QA. Uses installed Edge, Node 22+ and the project's Python.
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, mkdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve, dirname, join, relative, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pythonIndex = process.argv.indexOf('--python');
const python = pythonIndex >= 0 ? process.argv[pythonIndex + 1] : resolve(root, '../.venv/Scripts/python.exe');
const edge = process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const temp = await mkdtemp(join(tmpdir(), 'w2e-browser-'));
const artifacts = join(root, 'artifacts');
await mkdir(artifacts, {recursive: true});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { const result = await fn(); if (result) return result; } catch { /* Wait for startup or navigation. */ }
    await delay(70);
  }
  throw new Error('Timed out: ' + label);
}
const listener = createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const url = `http://127.0.0.1:${port}`;
let backend, browser, ws;
let serverErrors = '';
const errors = [];
try {
  backend = spawn(python, [join(root, 'server.py'), '--port', String(port), '--data-dir', join(temp, 'data')], {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
  backend.stderr.on('data', chunk => { serverErrors += chunk; });
  backend.on('error', error => { serverErrors += error.message; });
  await until(async () => (await fetch(url)).ok, 'Python HTTP server');
  browser = spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${join(temp, 'edge')}`, 'about:blank'], {windowsHide: true, stdio: 'ignore'});
  browser.on('error', error => errors.push(error.message));
  const active = await until(async () => {
    const lines = (await readFile(join(temp, 'edge/DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);
    return lines.length === 2 ? lines : null;
  }, 'Edge DevTools');
  ws = new WebSocket(`ws://127.0.0.1:${active[0]}${active[1]}`);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  ws.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (pending.has(message.id)) {
      const {resolve, reject, timer} = pending.get(message.id);
      clearTimeout(timer); pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(message.params.exceptionDetails));
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') errors.push(message.params.entry.text);
  };
  function send(method, params = {}, sessionId) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 15000);
      pending.set(id, {resolve, reject, timer});
      ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
  async function createPage() {
    const {browserContextId} = await send('Target.createBrowserContext');
    const {targetId} = await send('Target.createTarget', {url: 'about:blank', browserContextId});
    const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
    await send('Runtime.enable', {}, sessionId);
    await send('Log.enable', {}, sessionId);
    await send('Page.enable', {}, sessionId);
    await send('Emulation.setDeviceMetricsOverride', {width: 1400, height: 1050, deviceScaleFactor: 1, mobile: false}, sessionId);
    await send('Page.navigate', {url}, sessionId);
    return sessionId;
  }
  async function js(page, expression) {
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true}, page);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  async function wait(page, expression) {
    try { return await until(() => js(page, expression), expression); }
    catch (error) { throw new Error(error.message + '\n' + await js(page, 'document.body.innerText')); }
  }
  const click = (page, id) => js(page, `document.getElementById(${JSON.stringify(id)}).click()`);
  const fill = (page, id, value) => js(page, `document.getElementById(${JSON.stringify(id)}).value=${JSON.stringify(value)}`);
  async function signup(page, name) {
    await wait(page, "typeof state !== 'undefined' && state && document.getElementById('auth-dialog').open");
    await click(page, 'register-tab');
    await fill(page, 'auth-username', name); await fill(page, 'auth-password', 'QA密码🥗');
    await js(page, "document.getElementById('remember').checked=true");
    await click(page, 'auth-submit');
    await wait(page, 'state.user && !busy && !document.getElementById("auth-dialog").open');
  }
  async function screenshot(page, name) {
    await js(page, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const {data} = await send('Page.captureScreenshot', {captureBeyondViewport: true}, page);
    await writeFile(join(artifacts, name + '.png'), Buffer.from(data, 'base64'));
  }
  const alice = await createPage(), bob = await createPage();
  await wait(alice, "typeof state !== 'undefined' && state && document.getElementById('auth-dialog').open");
  await click(alice, 'guest-button'); await js(alice, "showView('options')");
  await fill(alice, 'new-options', '游客午餐\n游客沙拉');
  await js(alice, "document.getElementById('add-form').requestSubmit()");
  await wait(alice, 'state.options.length === 2 && !busy');
  await js(alice, "showView('today')"); await click(alice, 'draw-button');
  await wait(alice, 'state.today && !busy');
  await click(alice, 'account-button'); await signup(alice, 'Alice同学');
  assert.equal(await js(alice, 'state.options.length'), 0);
  await js(alice, "showView('options')");
  await fill(alice, 'new-options', '番茄牛腩饭\n兰州牛肉面\n轻食沙拉\n日式咖喱饭\n鸡腿盖饭\n食堂\n饺子');
  await js(alice, "document.getElementById('add-form').requestSubmit()");
  await wait(alice, 'state.options.length === 7 && !busy');
  await js(alice, "document.querySelector('.weight-form input').value='30'; document.querySelector('.weight-form').requestSubmit()");
  await wait(alice, "document.getElementById('confirm-dialog').open"); await click(alice, 'confirm-yes');
  await wait(alice, 'Math.abs(state.options[0].weight - .3) < .00001 && !busy');
  await js(alice, "showView('group')");
  await fill(alice, 'group-name', '今天也要准时开饭'); await fill(alice, 'create-code', 'LunchQA8');
  await js(alice, "document.getElementById('create-group-form').requestSubmit()");
  await wait(alice, 'state.group && !busy');
  await signup(bob, 'Bob同学'); await js(bob, "showView('group')");
  await fill(bob, 'join-code', 'LunchQA8');
  await js(bob, "document.getElementById('search-group-form').requestSubmit()");
  await wait(bob, "!!document.querySelector('#search-result button')");
  await js(bob, "document.querySelector('#search-result button').click()");
  await wait(bob, 'state.group && !busy'); await wait(alice, 'state.group.members.length === 2');
  await js(alice, "showView('today')"); await click(alice, 'draw-button');
  await wait(alice, 'state.today && !busy'); await wait(bob, '!!state.today');
  assert.equal(await js(alice, 'state.today.option_name'), await js(bob, 'state.today.option_name'));
  await js(bob, "showView('today')"); await click(bob, 'draw-button');
  await wait(bob, 'state.today.rerolls === 1 && !busy'); await wait(alice, 'state.today.rerolls === 1');
  await click(alice, 'draw-button'); await click(alice, 'confirm-no');
  assert.equal(await js(alice, 'state.today.rerolls'), 1);
  await click(alice, 'draw-button'); await click(alice, 'confirm-yes');
  await wait(alice, 'state.today.rerolls === 2 && !busy'); await wait(bob, 'state.today.rerolls === 2');
  await send('Page.reload', {}, alice);
  await wait(alice, "typeof state !== 'undefined' && state && state.user && state.group");
  await wait(alice, '!document.getElementById("auth-dialog").open');
  await screenshot(alice, 'desktop');
  await js(alice, "showView('calendar')");
  assert.equal(await js(alice, 'document.querySelector(".calendar-tag small").textContent'), '今天也要准时开饭');
  await screenshot(alice, 'calendar');
  await js(bob, "showView('group'); document.querySelector('#group-current .button').click()");
  await click(bob, 'confirm-yes'); await wait(bob, '!state.group && !busy');
  assert.equal(await js(bob, 'state.records[0].rerolls'), 2);
  await wait(alice, 'state.group.members.length === 1');
  await js(alice, "showView('today')"); await click(alice, 'draw-button'); await click(alice, 'confirm-yes');
  await wait(alice, 'state.today.rerolls === 3 && !busy');
  await js(bob, 'refresh({force:true})');
  assert.equal(await js(bob, 'state.records[0].rerolls'), 2);
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 1000, deviceScaleFactor: 1, mobile: true}, alice);
  await screenshot(alice, 'mobile');
  assert.ok(await js(alice, 'document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow');
  await click(alice, 'account-button'); await click(alice, 'confirm-yes');
  await wait(alice, '!state.user && !busy && document.getElementById("auth-dialog").open');
  assert.deepEqual(errors, [], 'Browser console errors');
  console.log('PASS: guest, registration, weights, two-browser group sync, shared rerolls, cancellation, remembered login, calendar labels, frozen exit snapshot, mobile layout, logout, browser console.');
  console.log('Screenshots: ' + artifacts);
  await send('Browser.close');
} catch (error) {
  console.error(error);
  if (serverErrors.includes('Traceback')) console.error(serverErrors);
  process.exitCode = 1;
} finally {
  if (ws) ws.close();
  if (browser && !browser.killed) browser.kill();
  if (backend && !backend.killed) backend.kill();
  await delay(800);
  // Only remove the freshly-created, verified task directory inside the system temp directory.
  const withinTemp = relative(resolve(tmpdir()), resolve(temp));
  if (withinTemp.startsWith('w2e-browser-') && !withinTemp.includes(sep) && !withinTemp.includes('..')) {
    await rm(temp, {recursive: true, force: true, maxRetries: 3, retryDelay: 300}).catch(() => {});
  }
}
