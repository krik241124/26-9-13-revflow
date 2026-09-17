// Execute the real browser script with a tiny DOM and fake fetch/file APIs.
// No browser login, production account or network connection is used.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../sku_detect/sku_detect.js'), 'utf8');

async function scan({input = 'EXISTS\nNEW\nERROR', responder, store = '1001'} = {}) {
  const nodes = [];
  const files = {};
  const requests = [];
  const writes = [];
  const alerts = [];
  class Element {
    constructor(tag) { this.tag = tag; this.style = {}; this.children = []; this.value = ''; nodes.push(this); }
    append(...items) { this.children.push(...items); }
    appendChild(item) { this.children.push(item); }
    remove() { this.removed = true; }
    focus() {}
    click() {
      if (this.type === 'file' && this.accept !== '.json') {
        this.files = [{name: 'skus.txt', text: async () => input}];
        this.onchange();
      }
    }
  }
  const document = {createElement: tag => new Element(tag),
    getElementById: id => nodes.find(x => x.id === id && !x.removed)};
  document.body = new Element('body');
  const window = {showDirectoryPicker: async () => ({
    getFileHandle: async name => ({createWritable: async () => ({
      write: async content => { files[name] = content; writes.push(name); }, close: async () => {}
    })})
  })};
  const context = {document, window, console: {log() {}, warn() {}, error() {}, table() {}},
    alert: text => alerts.push(text), crypto: crypto.webcrypto, TextEncoder, URLSearchParams, AbortSignal,
    setTimeout: fn => { queueMicrotask(fn); return 0; },
    fetch: async url => {
      const params = new URL('https://example.invalid' + url).searchParams;
      requests.push(Object.fromEntries(params));
      const sku = params.get('sellerSku');
      if (responder) return responder(sku, params);
      if (sku === 'ERROR') throw new Error('simulated network failure');
      return {ok: true, json: async () => ({code: 200, data: {
        list: sku === 'EXISTS' ? [{sellerSku: 'EXISTS'}] : [], pages: sku === 'EXISTS' ? 1 : 0,
        total: sku === 'EXISTS' ? 1 : 0
      }})};
    }};
  const running = vm.runInNewContext(source, context);
  const picker = nodes.find(x => x.accept === '.json');
  picker.files = [
    {name: 'runtime.json', text: async () => JSON.stringify({market: 'ca'})},
    {name: 'markets.json', text: async () => JSON.stringify({ca: {store_id: store, country_code: 'CA'}})}
  ];
  await picker.onchange();
  await new Promise(resolve => setImmediate(resolve));
  const start = nodes.find(x => x.tag === 'button' && x.textContent === '选择 SKU 文件并开始');
  if (!start) return {nodes, requests};
  start.onclick();
  await running;
  const save = document.getElementById('__sku_detect_save_button__');
  assert.ok(save, 'scan must offer saving');
  await save.onclick();
  return {files, requests, writes, alerts, nodes};
}

test('uses configured store and separates exists/new/error with valid file hashes', async () => {
  const result = await scan();
  assert.equal(result.files['ca-all.txt'], 'EXISTS\r\nNEW\r\nERROR');
  assert.equal(result.files['ca-need_create.txt'], 'NEW');
  assert.equal(result.files['ca-errors.txt'], 'ERROR');
  assert.ok(result.requests.every(r => r._storeId === '1001'));
  assert.equal(result.requests.filter(r => r.sellerSku === 'ERROR').length, 3);
  assert.equal(result.writes[0], 'ca-detect.json');
  assert.equal(result.writes.at(-1), 'ca-detect.json');
  const manifest = JSON.parse(result.files['ca-detect.json']);
  assert.equal(manifest.errors, 1);
  assert.equal(manifest.market, 'ca');
  for (const [name, hash] of Object.entries(manifest.files)) {
    assert.equal(hash, crypto.createHash('sha256').update(result.files[name]).digest('hex'));
  }
  assert.ok(result.nodes.some(x => (x.textContent || '').includes('不要继续执行 RevFlow')));
});

test('malformed success response is ERROR, never need_create', async () => {
  const result = await scan({input: 'A', responder: async () => ({ok: true, json: async () => ({code: 200, data: {}})})});
  assert.equal(result.files['ca-errors.txt'], 'A');
  assert.equal(result.files['ca-need_create.txt'], '');
});

test('pagination and exact matching preserve existing behavior', async () => {
  const result = await scan({input: 'A', responder: async (_sku, params) => ({ok: true, json: async () => ({code: 200,
    data: {list: [{sellerSku: params.get('pageNum') === '1' ? 'A-OTHER' : 'a'}], pages: 2, total: 2}
  })})});
  assert.equal(result.requests.length, 2);
  assert.equal(result.files['ca-need_create.txt'], '');
  assert.equal(result.files['ca-errors.txt'], '');
});

test('normalizes separators and deduplicates the actual saved worklist', async () => {
  const result = await scan({input: '# comment\nNEW,NEW;NEXT\tLAST'});
  assert.equal(result.files['ca-all.txt'], 'NEW\r\nNEXT\r\nLAST');
  assert.equal(result.files['ca-need_create.txt'], result.files['ca-all.txt']);
  assert.equal(result.files['ca-errors.txt'], '');
});

test('unknown or numeric store ID does not start requests', async () => {
  for (const store of ['', 1001]) {
    const result = await scan({store});
    assert.equal(result.requests.length, 0);
    assert.ok(result.nodes.some(x => (x.textContent || '').includes('store_id 尚未确认')));
  }
});
