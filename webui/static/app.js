const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="console-token"]').content;
let state = null, confirming = null, lastJob = '', polling = false;
const names = {fr:'France',de:'Germany',ca:'Canada',ro:'Romania',es:'Spain',us:'United States',it:'Italy',uk:'United Kingdom',pt:'Portugal',pl:'Poland'};
const jobs = {detect:'SKU Detect',retry:'重试 Errors',extract:'RevFlow',preflight:'Preflight',mapping:'Apply Mapping', 'dry-run':'Dry Run',write:'创建草稿','test-ark':'ArkSwift 连接测试','test-cl':'CL 连接测试'};
function notify(text, error=false) {
  $('notice').textContent=text; $('notice').className='notice'+(error?' error':''); $('notice').hidden=false;
  const dialog=document.querySelector('dialog[open]');
  if(dialog){let message=dialog.querySelector('.dialog-feedback');if(!message){message=document.createElement('p');message.className='dialog-feedback';message.setAttribute('role','status');dialog.append(message);}message.textContent=text;}
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({market:state?.market,...body})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请稍后重试。');
  return result;
}
function badge(id, text, type='') { $(id).textContent=text; $(id).className='badge '+type; }
function metrics(id, pairs) {
  $(id).replaceChildren(...pairs.map(([label,count]) => { const box=document.createElement('span'),value=document.createElement('strong');value.textContent=count||0;box.append(value,document.createTextNode(label));return box; }));
}
function render(s) {
  state=s;
  if ($('market').options.length !== s.markets.length) {
    $('market').replaceChildren(...s.markets.map(m => new Option(m.toUpperCase()+' · '+(names[m]||m),m)));
  }
  $('market').value=s.market; $('market').disabled=s.busy;
  $('version').textContent='v'+s.version;
  $('store-label').textContent=s.market.toUpperCase()+' · Store '+s.store_id;
  $('ark-status').textContent=s.auth?'● 已配置':'○ 未配置'; $('cl-status').textContent=s.cl?'● cURL 已配置':'○ 未配置';
  for (const id of ['ark-open','cl-open','settings-open','ark-save','cl-save','settings-save','ark-test','cl-test','mapping-file','sku-file','skus']) $(id).disabled=s.busy;
  const d=s.detect,e=s.extract,p=s.preflight;
  const detecting=s.job?.status==='running' && ['detect','retry'].includes(s.job.action);
  const pending=d.pending||0;
  badge('detect-status',d.errors?'有查询错误':d.ready?'检测完成':'未开始',d.errors?'bad':d.ready?'good':'');
  badge('extract-status',e.ready?'抓取完成':d.ready?'待抓取':'等待 Step 1',e.ready?'good':'');
  if(e.ready && ((e.counts.ERROR||0)+(e.counts.PARTIAL||0))) badge('extract-status','有抓取异常','warning');
  badge('preflight-status',p.ready?'Preflight Passed':p.mapping?'需要 Mapping':p.counts.BLOCKED||p.counts.CL_ERROR?'存在阻塞':p.has_report?'需重新预检':e.ready?'待预检':'等待 Step 2',p.ready?'good':p.has_report?'warning':'');
  badge('write-status',s.dry_run?'Dry Run Passed':p.ready?'等待 Dry Run':'等待 Preflight',s.dry_run?'good':'');
  metrics('detect-metrics',d.total?[['总 SKU',d.total],['已存在',d.existing],['需要创建',d.need],['查询错误',d.errors],['待检测',pending]]:[]);
  metrics('extract-metrics',e.total?[['提取成功',e.counts.OK],['异常',(e.counts.ERROR||0)+(e.counts.PARTIAL||0)],['已存在 / 跳过',e.counts.SKIP_EXISTS]]:d.ready?[['待抓取 SKU',d.need]]:[]);
  metrics('preflight-metrics',p.has_report?[['READY',p.counts.READY],['WARNINGS',p.counts.READY_WITH_WARNINGS],['MAPPING 问题',p.mapping],['BLOCKED / CL ERROR',(p.counts.BLOCKED||0)+(p.counts.CL_ERROR||0)]]:[]);
  $('detect-message').hidden=!(detecting||pending||d.errors||d.message);
  $('detect-message').textContent=detecting?`正在检测中，请等待… 已完成 ${d.total-pending} / ${d.total}；结果会自动更新。`:pending?`还有 ${pending} 个 SKU 未完成，请点击继续检测。成功结果会保留。`:d.errors?`有 ${d.errors} 个 SKU 查询失败，请重试 Errors。成功结果会保留，错误不会视为已存在。`:d.message;
  $('mapping-tools').hidden=!p.mapping;
  $('mapping-prompt').disabled=s.busy;
  $('blocker-guide').hidden=!(p.blockers?.length);
  $('blocker-list').replaceChildren(...(p.blockers||[]).map(b=>{const li=document.createElement('li');li.textContent=b.sku+'：'+b.reason;return li;}));
  $('batch-results').hidden=!s.write_started;
  const activeStep=s.job?.status==='running'?({detect:'detect',retry:'detect',extract:'extract',preflight:'preflight',mapping:'preflight','dry-run':'write',write:'write'})[s.job.action]:null;
  const elapsed=s.job?.started_at?Math.max(0,Math.floor((Date.now()-Date.parse(s.job.started_at))/1000)):0;
  const activityText={detect:'正在查询 SKU，结果会自动更新',extract:'正在抓取产品资料和下载图片，请等待；详情见实时日志',preflight:s.job?.action==='mapping'?'正在校验并应用映射，随后自动重新预检':'正在检查类目、属性、图片和产品数据，请等待',write:s.job?.action==='dry-run'?'正在模拟校验，请等待；此步骤不会创建草稿':'正在上传图片并创建草稿，请等待，勿重复提交'};
  for(const step of ['detect','extract','preflight','write']){const running=activeStep===step;$('activity-'+step).hidden=!running;$('card-'+step).classList.toggle('is-running',running);if(running)$('activity-'+step).textContent=activityText[step]+` · 已运行 ${elapsed} 秒`;}
  for(const [id,label] of Object.entries({extract:'开始抓取',preflight:'运行预检',mapping:'应用映射并重新预检','dry-run':'① Dry Run',write:'② 创建草稿'}))$(id).textContent=s.job?.status==='running'&&s.job.action===id?'处理中，请等待…':label;
  $('retry').hidden=detecting||!(d.errors||pending);
  $('retry').textContent=pending?'继续检测未完成项':`仅重试失败的 ${d.errors} 个 SKU`;
  $('detect').textContent=detecting?'正在检测中，请等待…':d.total?'重新检测完整清单':'开始检测';
  $('detect').disabled=s.busy||!s.auth; $('retry').disabled=s.busy||!s.auth;
  $('extract').disabled=s.busy||!d.ready||!s.cl;
  $('preflight').disabled=s.busy||!e.ready||!s.auth;
  $('mapping').disabled=s.busy||!e.ready||!s.auth;
  $('dry-run').disabled=s.busy||!p.ready;
  $('write').disabled=s.busy||!s.dry_run||s.write_count===0;
  $('write-note').textContent=s.dry_run?`校验通过，本次有 ${s.write_count} 个 SKU 待创建草稿。`:p.ready?'预检通过。先运行 Dry Run，检查完整数据后再创建草稿。':'先完成 Dry Run 校验，通过后才可创建草稿。';
  $('limited').hidden=!s.limited;
  $('result-title').textContent='本批次创建结果';
  $('result-description').textContent='仅统计本批次实际创建操作；检测已存在与历史记录不计入新增草稿。';
  metrics('audit-metrics',[['本次创建成功',s.audit.DRAFT_SAVED],['创建时发现已存在',s.audit.ALREADY_EXISTS],['本次创建失败',(s.audit.ERROR||0)+(s.audit.CL_ERROR||0)],['检测已存在 / 跳过',d.existing]]);
  const job=s.job;
  if (job?.id) {
    $('job-status').textContent=(jobs[job.action]||job.action)+' · '+({running:'运行中',success:'完成',failed:'失败 / 待处理',interrupted:'已中断'}[job.status]||job.status);
    $('job-message').textContent=job.message||'';
    if (s.log) { const pre=$('log-content'),atEnd=$('log-follow').checked;pre.textContent=s.log;if(atEnd)pre.scrollTop=pre.scrollHeight; }
    if(job.status==='running') {
      const step=({detect:'detect',retry:'detect',extract:'extract',preflight:'preflight',mapping:'preflight','dry-run':'write',write:'write'})[job.action];
      if(step) badge(step+'-status',detecting?'正在检测，请等待…':'运行中…','running');
      $('logs').open=true;
    }
    if(lastJob===job.id+':running' && job.status!=='running') notify(job.message,job.status!=='success');
    lastJob=job.id+':'+job.status;
  }
}
async function refresh() {
  if(polling)return;
  polling=true;
  try { render(await api('/api/state')); } catch(error) { notify(error.message,true); } finally { polling=false; }
}
async function safely(fn) { try { await fn(); } catch(error) { notify(error.message,true); } }
async function run(action,extra={}) {
  await api('/api/jobs',{action,...extra});
  notify((jobs[action]||action)+' 已开始，可在下方查看进度。');
  await refresh();
}
for(const action of ['extract','preflight','mapping','dry-run','retry']) $(action).onclick=()=>safely(()=>run(action));
$('detect').onclick=()=>safely(()=>run('detect',{skus:$('skus').value}));
$('market').onchange=()=>safely(async()=>{await api('/api/market',{target:$('market').value});await refresh();notify('市场已切换，请核对 CL cURL 是否来自当前国家。');});
function inputCount(){ $('input-count').textContent=new Set($('skus').value.split(/[\r\n,;\t]+/).map(x=>x.trim()).filter(Boolean)).size+' 个输入 SKU'; }
$('skus').oninput=inputCount;
async function loadSkuFile(file){if(!file)return;if(file.size>2*1024*1024)throw new Error('文件不能超过 2 MB。');$('skus').value=await file.text();inputCount();}
$('sku-file').onchange=()=>safely(()=>loadSkuFile($('sku-file').files[0]));
$('skus').ondragover=e=>e.preventDefault();
$('skus').ondrop=e=>{e.preventDefault();if(!state.busy)safely(()=>loadSkuFile(e.dataTransfer.files[0]));};
for(const kind of ['ark','cl']) {
  $(kind+'-open').onclick=()=>$(kind+'-dialog').showModal();
  const save=async()=>{await api('/api/credentials/'+kind,kind==='ark'?{cookie:$('cookie').value,authorization:$('authorization').value}:{curl:$('curl').value});if(kind==='ark'){$('cookie').value='';$('authorization').value='';}else $('curl').value='';notify('凭证已保存到本机。');await refresh();};
  $(kind+'-save').onclick=()=>safely(async()=>{await save();$(kind+'-dialog').close();});
  $(kind+'-test').onclick=()=>safely(async()=>{if(kind==='ark'?$('cookie').value.trim()||$('authorization').value.trim():$('curl').value.trim())await save();$(kind+'-dialog').close();await run('test-'+kind);});
}
const fields={delay:['请求间隔（秒）',.1,30,.1],timeout:['请求超时（秒）',5,300,1],image_timeout:['图片超时（秒）',5,300,1],image_workers:['图片并发',1,8,1],max_images:['最多图片数',5,12,1],min_images:['最低图片数',5,12,1],checkpoint_every:['保存间隔（SKU）',1,1000,1]};
$('settings-open').onclick=()=>safely(async()=>{const values=await api('/api/settings');$('settings-fields').replaceChildren(...Object.entries(fields).map(([key,[name,min,max,step]])=>{const label=document.createElement('label');label.textContent=name;const input=document.createElement('input');Object.assign(input,{type:'number',id:'setting-'+key,min,max,step,value:values[key]});label.append(input);return label;}));$('settings-dialog').showModal();});
$('settings-save').onclick=()=>safely(async()=>{await api('/api/settings',Object.fromEntries(Object.keys(fields).map(key=>[key,Number($('setting-'+key).value)])));$('settings-dialog').close();notify('设置已保存，已有校验需要重新运行。');await refresh();});
document.querySelectorAll('[data-open]').forEach(button=>button.onclick=()=>safely(()=>api('/api/open',{kind:button.dataset.open})));
document.querySelectorAll('[data-report]').forEach(button=>button.onclick=()=>safely(async()=>{const report=await api('/api/report/'+button.dataset.report);$('report-title').textContent=button.textContent;$('report-content').textContent=report.text||'文件为空。';$('report-note').textContent=report.note||'';$('report-dialog').showModal();}));
async function importMapping(file){if(state.busy)throw new Error('任务运行中，请等待完成后导入。');if(!file)return;if(!file.name.toLowerCase().endsWith('.csv'))throw new Error('请选择 CSV 文件。');if(file.size>2*1024*1024)throw new Error('文件不能超过 2 MB。');await api('/api/mapping-upload',{csv:await file.text()});$('mapping-drop').firstChild.textContent='已导入 '+file.name+'，可重新拖入替换';notify('建议文件已导入。下一步：点击“应用映射并重新预检”。');$('mapping-file').value='';await refresh();}
$('mapping-file').onchange=()=>safely(()=>importMapping($('mapping-file').files[0]));
$('mapping-drop').ondragover=e=>{e.preventDefault();if(!state.busy)$('mapping-drop').classList.add('dragover');};
$('mapping-drop').ondragleave=()=>$('mapping-drop').classList.remove('dragover');
$('mapping-drop').ondrop=e=>{e.preventDefault();$('mapping-drop').classList.remove('dragover');safely(()=>importMapping(e.dataTransfer.files[0]));};
$('mapping-prompt').onclick=()=>safely(async()=>{try{await navigator.clipboard.writeText($('mapping-prompt-text').value);notify('提示词已复制，请连同 CSV 和目标类目 / 属性资料交给 AI。');}catch{$('mapping-prompt-text').closest('details').open=true;$('mapping-prompt-text').select();notify('请按 Ctrl+C 复制已选中的提示词。');}});
$('write').onclick=()=>safely(async()=>{await refresh();if(!state.dry_run||state.busy||!state.write_count)throw new Error('状态已变化，请重新预检和 Dry Run。');confirming={market:state.market,fingerprint:state.fingerprint,count:state.write_count};$('confirm-market').textContent='Market：'+state.market.toUpperCase();$('confirm-store').textContent='Store：'+state.store_id;$('confirm-count').textContent='待创建 SKU：'+state.write_count;$('write-dialog').showModal();});
$('write-cancel').onclick=()=>$('write-dialog').close();
$('write-confirm').onclick=()=>safely(async()=>{$('write-confirm').disabled=true;try{await run('write',{...confirming,confirmed:true});$('write-dialog').close();}finally{$('write-confirm').disabled=false;}});
refresh();setInterval(refresh,1200);
