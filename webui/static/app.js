const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="console-token"]').content;
const names = {fr:'France',de:'Germany',ca:'Canada',ro:'Romania',es:'Spain',us:'United States',it:'Italy',uk:'United Kingdom',pt:'Portugal',pl:'Poland'};
const jobs = {
  detect:'SKU Detect',
  retry:'Retry errors',
  extract:'RevFlow',
  preflight:'Preflight',
  mapping:'Apply mapping',
  'dry-run':'Dry Run',
  write:'Create drafts',
  'test-ark':'ArkSwift connection test',
  'test-cl':'CL connection test'
};
const stageOrder = ['detect','extract','preflight','write'];

let state = null;
let confirming = null;
let lastJob = '';
let polling = false;
let currentStage = localStorage.getItem('arkswift-active-stage') || 'detect';

function notify(text, error = false) {
  $('notice').textContent = text;
  $('notice').className = 'notice' + (error ? ' error' : '');
  $('notice').hidden = false;
  const dialog = document.querySelector('dialog[open]');
  if (dialog) {
    let message = dialog.querySelector('.dialog-feedback');
    if (!message) {
      message = document.createElement('p');
      message.className = 'dialog-feedback';
      message.setAttribute('role', 'status');
      dialog.append(message);
    }
    message.textContent = text;
  }
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Console-Token': token},
    body: JSON.stringify({market: state?.market, ...body})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Action failed. Please try again.');
  return result;
}

function badge(id, text, type = '') {
  const el = $(id);
  el.textContent = text;
  el.className = 'badge' + (type ? ' ' + type : '');
}

function stageMeta(step, text, type = '') {
  const el = $('stage-status-' + step);
  el.textContent = text;
  el.className = 'stage-meta' + (type ? ' ' + type : '');
}

function metrics(id, pairs) {
  $(id).replaceChildren(...pairs.map(([label, count]) => {
    const box = document.createElement('span');
    const value = document.createElement('strong');
    value.textContent = count || 0;
    box.append(value, document.createTextNode(label));
    return box;
  }));
}

function setStage(stage) {
  if (!stageOrder.includes(stage)) stage = 'detect';
  currentStage = stage;
  localStorage.setItem('arkswift-active-stage', currentStage);
  document.querySelectorAll('.stage-tab').forEach(button => {
    const active = button.dataset.stage === currentStage;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  document.querySelectorAll('.stage-panel').forEach(panel => {
    panel.hidden = panel.dataset.stagePanel !== currentStage;
  });
}

function stageStatus(step, s) {
  const d = s.detect, e = s.extract, p = s.preflight;
  const activeStep = s.job?.status === 'running' ? ({detect:'detect', retry:'detect', extract:'extract', preflight:'preflight', mapping:'preflight', 'dry-run':'write', write:'write'})[s.job.action] : null;
  if (activeStep === step) return {text: 'Running…', type: 'running'};

  if (step === 'detect') {
    if (d.errors) return {text: d.pending ? 'Partial result' : 'Query issues', type: 'bad'};
    if (d.pending) return {text: 'Pending items', type: 'warning'};
    if (d.ready) return {text: 'Completed', type: 'good'};
    return {text: 'Not started', type: ''};
  }
  if (step === 'extract') {
    const issues = (e.counts.ERROR || 0) + (e.counts.PARTIAL || 0);
    if (e.ready && issues) return {text: 'Completed with issues', type: 'warning'};
    if (e.ready) return {text: 'Completed', type: 'good'};
    if (d.ready) return {text: 'Ready to run', type: ''};
    return {text: 'Waiting for Detect', type: ''};
  }
  if (step === 'preflight') {
    if (p.ready) return {text: 'Passed', type: 'good'};
    if (p.mapping) return {text: 'Needs mapping', type: 'warning'};
    if ((p.counts.BLOCKED || 0) + (p.counts.CL_ERROR || 0)) return {text: 'Blocked', type: 'bad'};
    if (p.has_report) return {text: 'Needs rerun', type: 'warning'};
    if (e.ready) return {text: 'Ready to run', type: ''};
    return {text: 'Waiting for Extract', type: ''};
  }
  if (step === 'write') {
    if (s.dry_run) return {text: 'Dry run passed', type: 'good'};
    if (p.ready) return {text: 'Ready for Dry Run', type: ''};
    return {text: 'Waiting for Preflight', type: ''};
  }
  return {text: '', type: ''};
}

function render(s) {
  state = s;
  if ($('market').options.length !== s.markets.length) {
    $('market').replaceChildren(...s.markets.map(m => new Option(m.toUpperCase() + ' · ' + (names[m] || m), m)));
  }
  $('market').value = s.market;
  $('market').disabled = s.busy;
  $('version').textContent = 'v' + s.version;
  $('store-label').textContent = s.market.toUpperCase() + ' · Store ' + s.store_id;
  $('ark-status').textContent = s.auth ? '● Configured' : '○ Not configured';
  $('cl-status').textContent = s.cl ? '● cURL configured' : '○ Not configured';

  for (const id of ['ark-open','cl-open','settings-open','ark-save','cl-save','settings-save','ark-test','cl-test','mapping-file','sku-file','skus']) {
    $(id).disabled = s.busy;
  }

  const d = s.detect, e = s.extract, p = s.preflight;
  const detecting = s.job?.status === 'running' && ['detect', 'retry'].includes(s.job.action);
  const pending = d.pending || 0;

  const detectStage = stageStatus('detect', s);
  const extractStage = stageStatus('extract', s);
  const preflightStage = stageStatus('preflight', s);
  const writeStage = stageStatus('write', s);

  badge('detect-status', detectStage.text, detectStage.type);
  badge('extract-status', extractStage.text, extractStage.type);
  badge('preflight-status', preflightStage.text, preflightStage.type);
  badge('write-status', writeStage.text, writeStage.type);
  stageMeta('detect', detectStage.text, detectStage.type);
  stageMeta('extract', extractStage.text, extractStage.type);
  stageMeta('preflight', preflightStage.text, preflightStage.type);
  stageMeta('write', writeStage.text, writeStage.type);

  metrics('detect-metrics', d.total ? [
    ['Total SKUs', d.total],
    ['Existing', d.existing],
    ['Need Create', d.need],
    ['Errors', d.errors],
    ['Pending', pending]
  ] : []);

  metrics('extract-metrics', e.total ? [
    ['Extracted', e.counts.OK],
    ['Issues', (e.counts.ERROR || 0) + (e.counts.PARTIAL || 0)],
    ['Existing / skipped', e.counts.SKIP_EXISTS]
  ] : d.ready ? [['SKUs queued', d.need]] : []);

  metrics('preflight-metrics', p.has_report ? [
    ['Ready', p.counts.READY],
    ['Warnings', p.counts.READY_WITH_WARNINGS],
    ['Mapping', p.mapping],
    ['Blocked / CL Error', (p.counts.BLOCKED || 0) + (p.counts.CL_ERROR || 0)]
  ] : []);

  $('detect-message').hidden = !(detecting || pending || d.errors || d.message);
  $('detect-message').textContent = detecting
    ? `Detection is running… ${Math.max(0, d.total - pending)} / ${d.total} completed. Results will refresh automatically.`
    : pending
      ? `${pending} SKUs are still pending. Click retry to continue from unfinished items.`
      : d.errors
        ? `${d.errors} SKU queries failed. You can retry only the failed items.`
        : d.message;

  $('mapping-tools').hidden = !p.mapping;
  $('mapping-prompt').disabled = s.busy;
  $('blocker-guide').hidden = !(p.blockers?.length);
  $('blocker-list').replaceChildren(...(p.blockers || []).map(item => {
    const li = document.createElement('li');
    li.textContent = item.sku + ': ' + item.reason;
    return li;
  }));

  $('batch-results').hidden = !s.write_started;

  const activeStep = s.job?.status === 'running' ? ({detect:'detect', retry:'detect', extract:'extract', preflight:'preflight', mapping:'preflight', 'dry-run':'write', write:'write'})[s.job.action] : null;
  const elapsed = s.job?.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(s.job.started_at)) / 1000)) : 0;
  const activityText = {
    detect:'Querying ArkSwift for existing SKUs. Results will update automatically',
    extract:'Fetching source product data and downloading images. See the live log for details',
    preflight:s.job?.action === 'mapping'
      ? 'Validating and applying mapping input, then rerunning preflight automatically'
      : 'Checking category, attributes, images, and product data before write',
    write:s.job?.action === 'dry-run'
      ? 'Dry Run is validating payloads. This step does not create drafts'
      : 'Uploading images and creating drafts. Please do not submit again'
  };

  for (const step of stageOrder) {
    const running = activeStep === step;
    $('activity-' + step).hidden = !running;
    $('card-' + step).classList.toggle('is-running', running);
    if (running) $('activity-' + step).textContent = activityText[step] + ` · ${elapsed}s elapsed`;
  }

  for (const [id, label] of Object.entries({extract:'Run RevFlow', preflight:'Run preflight', mapping:'Apply mapping & rerun', 'dry-run':'1. Dry Run', write:'2. Create drafts'})) {
    $(id).textContent = s.job?.status === 'running' && s.job.action === id ? 'Working… please wait' : label;
  }

  $('retry').hidden = detecting || !(d.errors || pending);
  $('retry').textContent = pending ? 'Continue pending detection' : `Retry failed ${d.errors} SKUs`;
  $('detect').textContent = detecting ? 'Detection is running… please wait' : d.total ? 'Rerun full detection' : 'Run SKU detection';

  $('detect').disabled = s.busy || !s.auth;
  $('retry').disabled = s.busy || !s.auth;
  $('extract').disabled = s.busy || !d.ready || !s.cl;
  $('preflight').disabled = s.busy || !e.ready || !s.auth;
  $('mapping').disabled = s.busy || !e.ready || !s.auth;
  $('dry-run').disabled = s.busy || !p.ready;
  $('write').disabled = s.busy || !s.dry_run || s.write_count === 0;

  $('write-note').textContent = s.dry_run
    ? `${s.write_count} SKU(s) are ready for draft creation in this batch.`
    : p.ready
      ? 'Preflight passed. Run Dry Run first to validate the final payload before creating drafts.'
      : 'Finish Dry Run first. Draft creation stays locked until validation passes.';

  $('limited').hidden = !s.limited;
  $('result-title').textContent = 'Batch creation results';
  $('result-description').textContent = 'Only the current create-draft batch is counted here. Existing detections and historical records are not counted as new drafts.';
  metrics('audit-metrics', [
    ['Draft saved', s.audit.DRAFT_SAVED],
    ['Already exists', s.audit.ALREADY_EXISTS],
    ['Failed', (s.audit.ERROR || 0) + (s.audit.CL_ERROR || 0)],
    ['Existing / skipped', d.existing]
  ]);

  const job = s.job;
  if (job?.id) {
    $('job-status').textContent = (jobs[job.action] || job.action) + ' · ' + ({running:'Running', success:'Completed', failed:'Failed / action required', interrupted:'Interrupted'}[job.status] || job.status);
    $('job-message').textContent = job.message || '';
    if (s.log) {
      const pre = $('log-content');
      const atEnd = $('log-follow').checked;
      pre.textContent = s.log;
      if (atEnd) pre.scrollTop = pre.scrollHeight;
    }
    if (job.status === 'running') {
      $('logs').open = true;
    }
    if (lastJob === job.id + ':running' && job.status !== 'running') notify(job.message, job.status !== 'success');
    lastJob = job.id + ':' + job.status;
  }
}

async function refresh() {
  if (polling) return;
  polling = true;
  try {
    render(await api('/api/state'));
  } catch (error) {
    notify(error.message, true);
  } finally {
    polling = false;
  }
}

async function safely(fn) {
  try {
    await fn();
  } catch (error) {
    notify(error.message, true);
  }
}

async function run(action, extra = {}) {
  await api('/api/jobs', {action, ...extra});
  notify((jobs[action] || action) + ' started. Watch progress in the live activity panel.');
  await refresh();
}

for (const action of ['extract','preflight','mapping','dry-run','retry']) {
  $(action).onclick = () => safely(() => run(action));
}
$('detect').onclick = () => safely(() => run('detect', {skus: $('skus').value}));

$('market').onchange = () => safely(async () => {
  await api('/api/market', {target: $('market').value});
  await refresh();
  notify('Market switched. Please verify that the CL cURL belongs to the current country.');
});

function inputCount() {
  const count = new Set($('skus').value.split(/[\r\n,;\t]+/).map(x => x.trim()).filter(Boolean)).size;
  $('input-count').textContent = count ? `${count} input SKU(s)` : 'One SKU per line';
}
$('skus').oninput = inputCount;

async function loadSkuFile(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) throw new Error('The file must be smaller than 2 MB.');
  $('skus').value = await file.text();
  inputCount();
}
$('sku-file').onchange = () => safely(() => loadSkuFile($('sku-file').files[0]));
$('skus').ondragover = event => event.preventDefault();
$('skus').ondrop = event => {
  event.preventDefault();
  if (!state.busy) safely(() => loadSkuFile(event.dataTransfer.files[0]));
};

for (const kind of ['ark', 'cl']) {
  $(kind + '-open').onclick = () => $(kind + '-dialog').showModal();
  const save = async () => {
    await api('/api/credentials/' + kind, kind === 'ark'
      ? {cookie: $('cookie').value, authorization: $('authorization').value}
      : {curl: $('curl').value});
    if (kind === 'ark') {
      $('cookie').value = '';
      $('authorization').value = '';
    } else {
      $('curl').value = '';
    }
    notify('Credentials saved locally.');
    await refresh();
  };
  $(kind + '-save').onclick = () => safely(async () => { await save(); $(kind + '-dialog').close(); });
  $(kind + '-test').onclick = () => safely(async () => {
    if (kind === 'ark' ? $('cookie').value.trim() || $('authorization').value.trim() : $('curl').value.trim()) await save();
    $(kind + '-dialog').close();
    await run('test-' + kind);
  });
}

const fields = {
  delay:['Request delay (sec)', .1, 30, .1],
  timeout:['Request timeout (sec)', 5, 300, 1],
  image_timeout:['Image timeout (sec)', 5, 300, 1],
  image_workers:['Image workers', 1, 8, 1],
  max_images:['Max images', 5, 12, 1],
  min_images:['Min images', 5, 12, 1],
  checkpoint_every:['Checkpoint every (SKU)', 1, 1000, 1]
};

$('settings-open').onclick = () => safely(async () => {
  const values = await api('/api/settings');
  $('settings-fields').replaceChildren(...Object.entries(fields).map(([key, [name, min, max, step]]) => {
    const label = document.createElement('label');
    label.textContent = name;
    const input = document.createElement('input');
    Object.assign(input, {type:'number', id:'setting-' + key, min, max, step, value: values[key]});
    label.append(input);
    return label;
  }));
  $('settings-dialog').showModal();
});

$('settings-save').onclick = () => safely(async () => {
  await api('/api/settings', Object.fromEntries(Object.keys(fields).map(key => [key, Number($('setting-' + key).value)])));
  $('settings-dialog').close();
  notify('Settings saved. Existing validation may need to be rerun.');
  await refresh();
});

document.querySelectorAll('[data-open]').forEach(button => {
  button.onclick = () => safely(() => api('/api/open', {kind: button.dataset.open}));
});

document.querySelectorAll('[data-report]').forEach(button => {
  button.onclick = () => safely(async () => {
    const report = await api('/api/report/' + button.dataset.report);
    $('report-title').textContent = button.textContent;
    $('report-content').textContent = report.text || 'File is empty.';
    $('report-note').textContent = report.note || '';
    $('report-dialog').showModal();
  });
});

async function importMapping(file) {
  if (state.busy) throw new Error('A job is running. Please wait for it to finish before importing.');
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) throw new Error('Please choose a CSV file.');
  if (file.size > 2 * 1024 * 1024) throw new Error('The file must be smaller than 2 MB.');
  await api('/api/mapping-upload', {csv: await file.text()});
  $('mapping-drop').firstChild.textContent = 'Imported ' + file.name + '. Click or drop another CSV to replace it';
  notify('Mapping file imported. Next: apply mapping and rerun preflight.');
  $('mapping-file').value = '';
  await refresh();
}

$('mapping-file').onchange = () => safely(() => importMapping($('mapping-file').files[0]));
$('mapping-drop').ondragover = event => {
  event.preventDefault();
  if (!state.busy) $('mapping-drop').classList.add('dragover');
};
$('mapping-drop').ondragleave = () => $('mapping-drop').classList.remove('dragover');
$('mapping-drop').ondrop = event => {
  event.preventDefault();
  $('mapping-drop').classList.remove('dragover');
  safely(() => importMapping(event.dataTransfer.files[0]));
};
$('mapping-prompt').onclick = () => safely(async () => {
  try {
    await navigator.clipboard.writeText($('mapping-prompt-text').value);
    notify('The prompt has been copied. Send it together with the CSV and the store taxonomy data.');
  } catch {
    $('mapping-prompt-text').closest('details').open = true;
    $('mapping-prompt-text').select();
    notify('Press Ctrl+C to copy the selected prompt.');
  }
});

$('write').onclick = () => safely(async () => {
  await refresh();
  if (!state.dry_run || state.busy || !state.write_count) throw new Error('State changed. Please rerun preflight and Dry Run.');
  confirming = {market: state.market, fingerprint: state.fingerprint, count: state.write_count};
  $('confirm-market').textContent = 'Market: ' + state.market.toUpperCase();
  $('confirm-store').textContent = 'Store: ' + state.store_id;
  $('confirm-count').textContent = 'SKUs to create: ' + state.write_count;
  $('write-dialog').showModal();
});
$('write-cancel').onclick = () => $('write-dialog').close();
$('write-confirm').onclick = () => safely(async () => {
  $('write-confirm').disabled = true;
  try {
    await run('write', {...confirming, confirmed: true});
    $('write-dialog').close();
  } finally {
    $('write-confirm').disabled = false;
  }
});

document.querySelectorAll('.stage-tab').forEach(button => {
  button.onclick = () => setStage(button.dataset.stage);
});
setStage(currentStage);
refresh();
setInterval(refresh, 1200);
