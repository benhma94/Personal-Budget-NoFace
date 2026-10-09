/* Journal view controller. Imports, review, transfers, month close, posting,
 * and learned rules all stay in this local single-page workflow. The Budget
 * tab is live, so switching to it after a post shows the new numbers directly
 * (finance_hub/server.py invalidates the budget payload cache on post).
 */
(()=>{
const $=(sel,root=document)=>root.querySelector(sel);
const $$=(sel,root=document)=>Array.from(root.querySelectorAll(sel));
const CAD=new Intl.NumberFormat('en-CA',{style:'currency',currency:'CAD'});
const money=v=>CAD.format(v||0);
const moneyOrDash=v=>v==null?'—':money(v);
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}

function currentMonth(){
  const now=new Date();
  return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
}
function moneyInput(v){return v==null?'':Number(v).toFixed(2)}

// Plain-language labels for the strptime codes journal_entry/csv_import.py
// tries when guessing a date format (_DATE_FORMAT_CANDIDATES, same order).
// Each example renders a day of 31 so day and month are never confusable
// with each other, letting a user pattern-match against their own file
// without ever reading a %-code.
const DATE_FORMAT_PRESETS=[
  {fmt:'%Y-%m-%d', label:'Year-Month-Day', example:'2026-12-31'},
  {fmt:'%m/%d/%Y', label:'Month/Day/Year', example:'12/31/2026'},
  {fmt:'%d/%m/%Y', label:'Day/Month/Year', example:'31/12/2026'},
  {fmt:'%m/%d/%y', label:'Month/Day/Year (2-digit year)', example:'12/31/26'},
  {fmt:'%d/%m/%y', label:'Day/Month/Year (2-digit year)', example:'31/12/26'},
  {fmt:'%m-%d-%Y', label:'Month-Day-Year', example:'12-31-2026'},
  {fmt:'%d-%m-%Y', label:'Day-Month-Year', example:'31-12-2026'},
  {fmt:'%b %d, %Y', label:'Month Day, Year', example:'Dec 31, 2026'},
  {fmt:'%d %b %Y', label:'Day Month Year', example:'31 Dec 2026'},
];
const DATE_FORMAT_CUSTOM='__custom__';
function toast(msg,ok){const el=$('#toast');el.textContent=msg;el.className='toast '+(ok?'ok':'error');el.style.display='block';clearTimeout(toast._t);toast._t=setTimeout(()=>el.style.display='none',ok?3500:6000)}

async function api(method,path,body){
  const opts={method,headers:{}};
  if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body)}
  const res=await fetch(path,opts);
  let data=null;
  try{data=await res.json()}catch(e){/* empty body */}
  if(!res.ok){throw new Error((data&&data.error)||`${method} ${path} failed (${res.status})`)}
  return data;
}

// render() rebuilds a whole tab with innerHTML, destroying the .table-wrap
// scroller (journal.css: overflow:auto; max-height:560px) and whatever input
// had focus. Capture both before a swap, put them back after, so an edit
// inside a long list doesn't throw the user back to the top.
let lastRenderedTab=null;

function captureViewPosition(){
  const view=$('#view');
  if(!view)return null;
  const pos={tab:lastRenderedTab, scrolls:$$('#view .table-wrap').map(w=>w.scrollTop), focus:null};
  const el=document.activeElement;
  if(el&&el!==document.body&&view.contains(el)){
    const cls=(el.className||'').split(/\s+/).filter(Boolean)[0]||'';
    const tr=el.closest('tr');
    const f={id:el.id||'', cls, fp:tr?(tr.dataset.fp||''):''};
    if(cls)f.index=$$('#view .'+cls).indexOf(el);
    try{f.start=el.selectionStart;f.end=el.selectionEnd}catch(e){/* input type has no caret */}
    pos.focus=f;
  }
  return pos;
}

function restoreViewPosition(pos){
  if(!pos||pos.tab!==State.tab)return; // switching tabs should start at the top
  const view=$('#view'); if(!view)return;
  const f=pos.focus;
  if(f){
    let el=null;
    if(f.id){const byId=document.getElementById(f.id); if(byId&&view.contains(byId))el=byId}
    if(!el&&f.fp&&f.cls){
      const tr=$$('#view tr[data-fp]').find(r=>r.dataset.fp===f.fp);
      if(tr)el=$('.'+f.cls,tr);
    }
    if(!el&&f.cls&&f.index>=0)el=$$('#view .'+f.cls)[f.index]||null;
    if(el){
      el.focus({preventScroll:true});
      if(f.start!=null){try{el.setSelectionRange(f.start,f.end)}catch(e){/* no caret */}}
    }
  }
  $$('#view .table-wrap').forEach((w,i)=>{if(pos.scrolls[i]!=null)w.scrollTop=pos.scrolls[i]});
}

const State={
  tab:'import', accounts:[], kinds:{}, transactions:[], rules:[], suggestedTransfers:[],
  reviewSort:{col:'txn_date',dir:1}, reviewSelection:new Set(), reviewFilter:{status:'open',account:'',q:''},
  pendingMapping:null, importLog:[], postingDate:new Date().toISOString().slice(0,10), preview:null,
  closeMonth:currentMonth(), closeData:null, closeInputs:null, closeDirty:false,
  batches:[], expandedBatch:null, batchLines:null, editingRow:null,
};

async function loadAccounts(){
  const data=await api('GET','/api/accounts');
  State.accounts=data.accounts; State.kinds=data.kinds;
}
async function loadTransactions(){
  State.transactions=await api('GET','/api/transactions');
}
async function loadRules(){
  State.rules=await api('GET','/api/rules');
}
async function loadBatches(){
  State.batches=await api('GET','/api/batches');
}

function categoryOptions(selected){
  const groups={income:[],expense:[],asset:[],liability:[],other:[]};
  for(const name of State.accounts){(groups[State.kinds[name]]||groups.other).push(name)}
  const label={income:'Income',expense:'Expense',asset:'Asset / transfer',liability:'Liability / transfer',other:'Other'};
  let html='<option value="">— choose —</option>';
  for(const key of ['expense','income','liability','asset','other']){
    if(!groups[key].length)continue;
    html+=`<option disabled>— ${label[key]} —</option>`;
    for(const name of groups[key]){html+=`<option value="${esc(name)}" ${name===selected?'selected':''}>${esc(name)}</option>`}
  }
  return html;
}
function accountOptions(selected){
  return State.accounts.map(a=>`<option value="${esc(a)}" ${a===selected?'selected':''}>${esc(a)}</option>`).join('');
}

// ---------------------------------------------------------------- Import --
function renderImport(){
  return `
  <div class="panel">
    <h2>Import a CSV</h2>
    <div class="dropzone" id="dropzone">Drop a bank CSV here, or click to choose a file</div>
    <input type="file" id="file-input" accept=".csv,text/csv" style="display:none">
    <div id="mapping-area"></div>
  </div>
  <div class="panel">
    <h2>This session</h2>
    <div class="import-log" id="import-log">${State.importLog.length?'':'<div class="empty">Nothing imported yet.</div>'}
      ${State.importLog.map((item,i)=>`<div class="item">
        <div>${esc(item.summary)}</div>
        <div class="row" style="margin-top:6px">
          ${item.fingerprints.length?`<button class="ghost il-undo" data-i="${i}">Undo (delete ${item.fingerprints.length} new row${item.fingerprints.length===1?'':'s'})</button>`:''}
          <button class="ghost il-edit" data-i="${i}">Edit layout</button>
        </div>
      </div>`).join('')}
    </div>
  </div>`;
}

function wireImport(){
  const dz=$('#dropzone'), input=$('#file-input');
  dz.addEventListener('click',()=>input.click());
  ['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('drag')}));
  ['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('drag')}));
  dz.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)handleFile(f)});
  input.addEventListener('change',()=>{const f=input.files[0];if(f)handleFile(f);input.value=''});
  $$('.il-undo').forEach(btn=>btn.addEventListener('click',()=>undoImport(+btn.dataset.i)));
  $$('.il-edit').forEach(btn=>btn.addEventListener('click',()=>editLayout(+btn.dataset.i)));
}

async function handleFile(file){
  const text=await file.text();
  try{
    const sniff=await api('POST','/api/import/sniff',{text});
    if(sniff.known_source){
      const ok=await commitImport(text,{header_signature:sniff.header_signature},{filename:file.name,sniff});
      // A saved-but-broken mapping (e.g. an unparseable date_format) would
      // otherwise fail forever with no way back to the form. Reopen it.
      if(!ok)showMappingForm(file.name,text,sniff,sniff.known_source);
    } else {
      showMappingForm(file.name,text,sniff);
    }
  }catch(e){toast(e.message,false)}
}

function showMappingForm(filename,text,sniff,existing){
  const headers=sniff.headers;
  const opt=(h,selected)=>`<option value="${esc(h)}" ${h===selected?'selected':''}>${esc(h)}</option>`;
  const mapping=existing?existing.mapping:null;
  const isSplit=!!(mapping&&mapping.out_col);
  const dateCol=mapping?mapping.date_col:(sniff.guessed_date_col||'');
  const dateFormat=existing?existing.date_format:(sniff.guessed_date_format||'%Y-%m-%d');
  const guessedHint=!existing&&sniff.guessed_date_format?'<div class="hint">Auto-detected from the file — please verify.</div>':'';
  $('#mapping-area').innerHTML=`
    <div class="panel" style="margin-top:14px;background:var(--panel2)">
      <h2>${existing?'Edit layout':'New format detected'} — ${esc(filename)}</h2>
      <div class="hint">Headers: ${headers.map(esc).join(', ')}</div>
      <div class="mapping-grid">
        <div class="field"><label>This file is for</label><select id="m-account">${accountOptions(existing?existing.default_account:undefined)}</select></div>
        <div class="field"><label>Date column</label><select id="m-date">${headers.map(h=>opt(h,dateCol)).join('')}</select></div>
        <div class="field"><label>Date format</label>
          <select id="m-dateformat-preset">${
            DATE_FORMAT_PRESETS.map(p=>`<option value="${esc(p.fmt)}" ${p.fmt===dateFormat?'selected':''}>${esc(p.label)} — e.g. ${esc(p.example)}</option>`).join('')
          }<option value="${DATE_FORMAT_CUSTOM}" ${DATE_FORMAT_PRESETS.some(p=>p.fmt===dateFormat)?'':'selected'}>Custom format…</option></select>
          <input type="text" id="m-dateformat" value="${esc(dateFormat)}" style="${DATE_FORMAT_PRESETS.some(p=>p.fmt===dateFormat)?'display:none':''}">${guessedHint}
          <div id="m-dateformat-preview" class="date-preview"></div></div>
        <div class="field"><label>Description column</label><select id="m-desc">${headers.map(h=>opt(h,mapping?mapping.desc_col:'')).join('')}</select></div>
        <div class="field"><label>Amount style</label>
          <select id="m-style"><option value="single" ${!isSplit?'selected':''}>Single amount column</option><option value="split" ${isSplit?'selected':''}>Separate out/in columns</option></select></div>
        <div class="field" id="m-amount-field"><label>Amount column</label><select id="m-amount">${headers.map(h=>opt(h,mapping&&!isSplit?mapping.amount_col:'')).join('')}</select></div>
        <div class="field" id="m-sign-field"><label>Sign convention</label>
          <select id="m-sign"><option value="1" ${!mapping||mapping.sign!==-1?'selected':''}>Negative = money out</option><option value="-1" ${mapping&&mapping.sign===-1?'selected':''}>Positive = money out</option></select></div>
        <div class="field" id="m-out-field" style="display:none"><label>Money-out column</label><select id="m-out">${headers.map(h=>opt(h,mapping&&isSplit?mapping.out_col:'')).join('')}</select></div>
        <div class="field" id="m-in-field" style="display:none"><label>Money-in column</label><select id="m-in">${headers.map(h=>opt(h,mapping&&isSplit?mapping.in_col:'')).join('')}</select></div>
        <div class="field"><label>Label</label><input type="text" id="m-label" value="${esc(existing?existing.label:filename)}"></div>
      </div>
      <div class="row"><button class="primary" id="m-save">Save layout & import</button>
        <button class="ghost" id="m-cancel">Cancel</button></div>
    </div>`;
  $('#m-style').addEventListener('change',e=>{
    const split=e.target.value==='split';
    $('#m-amount-field').style.display=split?'none':'';
    $('#m-sign-field').style.display=split?'none':'';
    $('#m-out-field').style.display=split?'':'none';
    $('#m-in-field').style.display=split?'':'none';
  });
  if(isSplit)$('#m-style').dispatchEvent(new Event('change'));

  // Live preview of how the typed date format parses real sample values from
  // this file — the field takes a Python strptime code, which is otherwise
  // impossible to verify without waiting for import to fail.
  let previewToken=0, previewTimer=null;
  async function updateDatePreview(){
    const dateCol=$('#m-date').value, dateFormat=$('#m-dateformat').value;
    const el=$('#m-dateformat-preview');
    if(!dateCol||!dateFormat){el.innerHTML='';return}
    const token=++previewToken;
    try{
      const res=await api('POST','/api/import/preview-dates',{text,date_col:dateCol,date_format:dateFormat});
      if(token!==previewToken)return;
      if(!res.samples.length){el.innerHTML='<div class="hint">No sample values found in this column.</div>';return}
      el.innerHTML=res.samples.map(s=>
        `<div class="date-preview-row ${s.parsed?'ok':'bad'}">${esc(s.raw)} → ${s.parsed?esc(s.parsed):'no match'}</div>`
      ).join('');
    }catch(e){if(token===previewToken)el.innerHTML='<div class="hint">Could not preview dates.</div>'}
  }
  const scheduleDatePreview=()=>{clearTimeout(previewTimer);previewTimer=setTimeout(updateDatePreview,300)};
  $('#m-date').addEventListener('change',scheduleDatePreview);
  $('#m-dateformat').addEventListener('input',scheduleDatePreview);
  $('#m-dateformat-preset').addEventListener('change',e=>{
    const custom=e.target.value===DATE_FORMAT_CUSTOM;
    $('#m-dateformat').style.display=custom?'':'none';
    if(!custom){$('#m-dateformat').value=e.target.value;scheduleDatePreview()}
  });
  updateDatePreview();

  $('#m-cancel').addEventListener('click',()=>{$('#mapping-area').innerHTML=''});
  $('#m-save').addEventListener('click',async()=>{
    const split=$('#m-style').value==='split';
    const newMapping=split
      ? {date_col:$('#m-date').value, desc_col:$('#m-desc').value, out_col:$('#m-out').value, in_col:$('#m-in').value}
      : {date_col:$('#m-date').value, desc_col:$('#m-desc').value, amount_col:$('#m-amount').value, sign:+$('#m-sign').value};
    const account=$('#m-account').value, dateFormat=$('#m-dateformat').value, label=$('#m-label').value;
    if(!account){toast('Choose which account this file belongs to',false);return}
    try{
      await api('POST','/api/import/mapping',{
        header_signature:sniff.header_signature, label, default_account:account, mapping:newMapping, date_format:dateFormat,
      });
      $('#mapping-area').innerHTML='';
      const ok=await commitImport(text,{header_signature:sniff.header_signature},{filename,sniff});
      // The mapping saved fine but the import failed (e.g. a date_format that
      // does not parse this file). Reopen the form pre-filled with what was
      // just saved so the user can correct it without re-dropping the file.
      if(!ok)showMappingForm(filename,text,sniff,{label, default_account:account, mapping:newMapping, date_format:dateFormat});
    }catch(e){toast(e.message,false)}
  });
}

// Returns true on success, false on failure (errors are toasted here, not
// thrown, so callers can react to a failure without double-toasting).
async function commitImport(text,ref,meta){
  try{
    const result=await api('POST','/api/import/commit',{text,...ref});
    // Deduped: two identical rows in one file share a fingerprint, and both
    // are flagged duplicate:false ("duplicate" means "already in the DB").
    const fingerprints=[...new Set(result.rows.filter(r=>!r.duplicate).map(r=>r.fingerprint))];
    State.importLog.unshift({
      summary:`${result.read} rows read · ${result.new} new · ${result.duplicate} already imported`,
      fingerprints, filename:meta.filename, text, sniff:meta.sniff,
    });
    await loadTransactions();
    toast(`Imported ${result.new} new transaction(s)`,true);
    render();
    return true;
  }catch(e){toast(e.message,false);return false}
}

async function undoImport(i){
  const item=State.importLog[i];
  if(!item||!item.fingerprints.length)return;
  try{
    const result=await api('POST','/api/import/undo',{fingerprints:item.fingerprints});
    item.fingerprints=[];
    item.summary+=` — undone (${result.deleted} removed${result.kept?`, ${result.kept} already reviewed`:''})`;
    await loadTransactions();
    toast(`Removed ${result.deleted} row(s)`,true);
    render();
  }catch(e){toast(e.message,false)}
}

// Re-sniffs from the server rather than trusting the log entry's cached
// sniff: the server is the single source of truth for the mapping currently
// saved against this header signature, so editing an older log entry can no
// longer reopen (and re-save) a mapping that has since been corrected.
async function editLayout(i){
  const item=State.importLog[i];
  if(!item)return;
  try{
    const sniff=await api('POST','/api/import/sniff',{text:item.text});
    showMappingForm(item.filename,item.text,sniff,sniff.known_source);
  }catch(e){toast(e.message,false)}
}

// ---------------------------------------------------------------- Review --
function reviewRows(){
  const f=State.reviewFilter;
  return State.transactions.filter(t=>{
    if(f.status==='open'&&!['new','categorized'].includes(t.status))return false;
    if(f.status!=='open'&&f.status&&t.status!==f.status)return false;
    if(f.account&&t.account!==f.account)return false;
    if(f.q&&!(`${t.description} ${t.note||''}`.toLowerCase().includes(f.q.toLowerCase())))return false;
    return true;
  }).sort((a,b)=>{
    const {col,dir}=State.reviewSort; const av=a[col],bv=b[col];
    if(av===bv)return 0; return (av>bv?1:-1)*dir;
  });
}

// Filters/bulk-actions panel and the scrollable table are rendered and wired
// separately: an edit inside the table (sort, select, categorize, ignore)
// only needs to repaint the table via repaintReviewTable(), so the search
// box and other filter inputs are never destroyed and recreated mid-use.
function renderReview(){
  return renderReviewFilters()+`<div class="panel table-wrap" id="rv-table-wrap">${reviewTableHtml()}</div>`;
}

function renderReviewFilters(){
  const accountFilterOptions=['<option value="">All accounts</option>',...State.accounts.map(a=>`<option value="${esc(a)}" ${State.reviewFilter.account===a?'selected':''}>${esc(a)}</option>`)].join('');
  return `
  <div class="panel">
    <div class="row" style="justify-content:space-between">
      <div class="row">
        <select id="rv-status">
          <option value="open" ${State.reviewFilter.status==='open'?'selected':''}>New + suggested</option>
          <option value="categorized" ${State.reviewFilter.status==='categorized'?'selected':''}>Confirmed</option>
          <option value="ignored" ${State.reviewFilter.status==='ignored'?'selected':''}>Ignored</option>
          <option value="posted" ${State.reviewFilter.status==='posted'?'selected':''}>Posted</option>
          <option value="" ${State.reviewFilter.status===''?'selected':''}>All</option>
        </select>
        <select id="rv-account">${accountFilterOptions}</select>
        <input type="search" id="rv-search" placeholder="Search description or note" value="${esc(State.reviewFilter.q)}">
      </div>
      <div class="row">
        <select id="rv-bulk-category">${categoryOptions()}</select>
        <input type="text" id="rv-bulk-note" placeholder="Note" style="width:160px">
        <button class="primary" id="rv-apply">Apply to selected</button>
        <button class="ghost" id="rv-ignore">Ignore selected</button>
        <button class="ghost" id="rv-unignore">Unignore selected</button>
      </div>
    </div>
  </div>`;
}

function reviewTableHtml(){
  const rows=reviewRows();
  const cols=[['txn_date','Date'],['account','Account'],['description','Description'],['amount','Amount'],['category','Category'],['note','Note']];
  return `
    <table>
      <thead><tr><th style="width:26px"><input type="checkbox" id="rv-select-all"></th>
        ${cols.map(([c,l])=>`<th data-col="${c}" class="${State.reviewSort.col===c?'sorted':''}">${l}${State.reviewSort.col===c?(State.reviewSort.dir>0?' ▲':' ▼'):''}</th>`).join('')}
        <th>Status</th></tr></thead>
      <tbody>${rows.length?rows.map(rowHtml).join(''):'<tr><td colspan="8" class="empty">No transactions match this filter</td></tr>'}</tbody>
    </table>`;
}

function rowHtml(t){
  const suggested=t.status==='new'&&t.category;
  const badge=suggested?'<span class="badge suggested">Suggested</span>':`<span class="badge ${t.status}">${t.status}</span>`;
  const checked=State.reviewSelection.has(t.fingerprint)?'checked':'';
  return `<tr data-fp="${esc(t.fingerprint)}">
    <td><input type="checkbox" class="rv-row-select" ${checked}></td>
    <td>${esc(t.txn_date)}</td>
    <td>${esc(t.account)}</td>
    <td class="wrap">${esc(t.description)}</td>
    <td class="right ${t.amount<0?'bad':'good'}">${money(t.amount)}</td>
    <td><select class="rv-category">${categoryOptions(t.category)}</select></td>
    <td><input type="text" class="rv-note" value="${esc(t.note||'')}"></td>
    <td>${badge}${suggested?' <button class="ghost rv-confirm">Confirm</button>':''}</td>
  </tr>`;
}

// Repaints just the Review table (used by every in-table action) instead of
// the full render(), so scroll position and focus are captured/restored
// around a much smaller swap. Falls back to a full render() if the table
// wrapper isn't there for some reason (e.g. tab changed underneath us).
function repaintReviewTable(){
  const wrap=$('#rv-table-wrap');
  if(!wrap){render();return}
  const pos=captureViewPosition();
  wrap.innerHTML=reviewTableHtml();
  wireReviewTable();
  restoreViewPosition(pos);
}

function wireReview(){
  wireReviewFilters();
  wireReviewTable();
}

function wireReviewFilters(){
  $('#rv-status').addEventListener('change',e=>{State.reviewFilter.status=e.target.value;repaintReviewTable()});
  $('#rv-account').addEventListener('change',e=>{State.reviewFilter.account=e.target.value;repaintReviewTable()});
  $('#rv-search').addEventListener('input',e=>{State.reviewFilter.q=e.target.value;repaintReviewTable()});
  const applyBtn=$('#rv-apply');
  if(applyBtn)applyBtn.addEventListener('click',async()=>{
    const category=$('#rv-bulk-category').value, note=$('#rv-bulk-note').value;
    if(!category){toast('Choose a category first',false);return}
    if(!State.reviewSelection.size){toast('Select at least one row',false);return}
    await categorizeRows(Array.from(State.reviewSelection),category,note);
    State.reviewSelection.clear();
  });
  const ignoreBtn=$('#rv-ignore');
  if(ignoreBtn)ignoreBtn.addEventListener('click',async()=>{
    if(!State.reviewSelection.size){toast('Select at least one row',false);return}
    await api('POST','/api/transactions/status',{fingerprints:Array.from(State.reviewSelection),status:'ignored'});
    State.reviewSelection.clear();
    await loadTransactions(); await loadRules(); repaintReviewTable();
  });
  const unignoreBtn=$('#rv-unignore');
  if(unignoreBtn)unignoreBtn.addEventListener('click',async()=>{
    if(!State.reviewSelection.size){toast('Select at least one row',false);return}
    await api('POST','/api/transactions/status',{fingerprints:Array.from(State.reviewSelection),status:'new'});
    State.reviewSelection.clear();
    await loadTransactions(); await loadRules(); repaintReviewTable();
  });
}

function wireReviewTable(){
  $$('#view th[data-col]').forEach(th=>th.addEventListener('click',()=>{
    const col=th.dataset.col;
    if(State.reviewSort.col===col)State.reviewSort.dir*=-1; else State.reviewSort={col,dir:1};
    repaintReviewTable();
  }));
  const selectAll=$('#rv-select-all');
  if(selectAll)selectAll.addEventListener('change',e=>{
    reviewRows().forEach(t=>e.target.checked?State.reviewSelection.add(t.fingerprint):State.reviewSelection.delete(t.fingerprint));
    repaintReviewTable();
  });
  $$('.rv-row-select').forEach(cb=>cb.addEventListener('change',e=>{
    const fp=e.target.closest('tr').dataset.fp;
    e.target.checked?State.reviewSelection.add(fp):State.reviewSelection.delete(fp);
  }));
  $$('.rv-confirm').forEach(btn=>btn.addEventListener('click',async()=>{
    const tr=btn.closest('tr'), fp=tr.dataset.fp;
    const t=State.transactions.find(x=>x.fingerprint===fp);
    await categorizeRows([fp],t.category,t.note||'');
  }));
  $$('.rv-category,.rv-note').forEach(el=>el.addEventListener('change',async e=>{
    const tr=e.target.closest('tr'), fp=tr.dataset.fp;
    const category=$('.rv-category',tr).value, note=$('.rv-note',tr).value;
    if(!category)return;
    await categorizeRows([fp],category,note);
  }));
}

async function categorizeRows(fingerprints,category,note){
  try{
    await api('POST','/api/transactions/categorize',{fingerprints,category,note,learn:true});
    await loadTransactions(); await loadRules();
    repaintReviewTable();
  }catch(e){toast(e.message,false)}
}

// ------------------------------------------------------------- Transfers --
function renderTransfers(){
  const confirmed=State.transactions.filter(t=>t.status==='transfer');
  const seen=new Set(); const pairs=[];
  for(const t of confirmed){
    if(seen.has(t.fingerprint))continue;
    const peer=confirmed.find(p=>p.fingerprint===t.transfer_peer);
    if(!peer)continue;
    seen.add(t.fingerprint);seen.add(peer.fingerprint);
    pairs.push(t.amount<0?[t,peer]:[peer,t]);
  }
  return `
  <div class="panel">
    <h2>Suggested transfers</h2>
    ${State.suggestedTransfers.length?`<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>From</th><th>To</th><th class="right">Amount</th><th></th></tr></thead><tbody>
      ${State.suggestedTransfers.map((m,i)=>`<tr>
        <td>${esc(m.outgoing.txn_date)}</td><td>${esc(m.outgoing.account)}</td><td>${esc(m.incoming.account)}</td>
        <td class="right">${money(m.amount)}</td>
        <td class="row"><button class="primary tr-confirm" data-i="${i}">Confirm</button>
        <button class="ghost tr-dismiss" data-i="${i}">Dismiss</button></td>
      </tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">No matching pairs found in the current import.</div>'}
  </div>
  <div class="panel">
    <h2>Confirmed transfers</h2>
    ${pairs.length?`<div class="table-wrap"><table><thead><tr>
      <th>Date</th><th>From</th><th>To</th><th class="right">Amount</th><th>Note</th><th></th></tr></thead><tbody>
      ${pairs.map(([out,inc])=>`<tr>
        <td>${esc(out.txn_date)}</td><td>${esc(out.account)}</td><td>${esc(inc.account)}</td>
        <td class="right">${money(-out.amount)}</td><td class="wrap">${esc(out.note||'')}</td>
        <td><button class="ghost tr-unlink" data-fp="${esc(out.fingerprint)}">Unlink</button></td>
      </tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">None confirmed yet.</div>'}
  </div>`;
}

function wireTransfers(){
  $$('.tr-confirm').forEach(btn=>btn.addEventListener('click',async()=>{
    const m=State.suggestedTransfers[+btn.dataset.i];
    try{
      await api('POST','/api/transfers/confirm',{
        outgoing_fingerprint:m.outgoing.fingerprint, incoming_fingerprint:m.incoming.fingerprint, note:'Pay Bill',
      });
      await loadTransactions(); await refreshSuggestedTransfers(); render();
    }catch(e){toast(e.message,false)}
  }));
  $$('.tr-dismiss').forEach(btn=>btn.addEventListener('click',()=>{
    State.suggestedTransfers.splice(+btn.dataset.i,1); render();
  }));
  $$('.tr-unlink').forEach(btn=>btn.addEventListener('click',async()=>{
    await api('POST','/api/transfers/unlink',{fingerprint:btn.dataset.fp});
    await loadTransactions(); await refreshSuggestedTransfers(); render();
  }));
}

async function refreshSuggestedTransfers(){
  State.suggestedTransfers=await api('GET','/api/transfers/suggested');
}

// ----------------------------------------------------------- Month close --
function setCloseInputsFromData(){
  State.closeInputs={};
  for(const kind of ['investment','transit']){
    const row=State.closeData.reconciliations[kind];
    State.closeInputs[kind]={enabled:row.enabled,ending_balance:moneyInput(row.ending_balance)};
  }
  State.closeDirty=false;
}

async function loadMonthClose(){
  State.closeData=await api('GET',`/api/month-close?month=${encodeURIComponent(State.closeMonth)}`);
  setCloseInputsFromData();
}

function renderCloseCard(kind){
  const row=State.closeData.reconciliations[kind], input=State.closeInputs[kind];
  const isInvestment=kind==='investment';
  const inputLabel=isInvestment?'Ending portfolio balance':`Ending ${esc(row.account)} balance`;
  const source=isInvestment?State.closeData.portfolio_cache:null;
  const sourceText=source
    ?`Cached Portfolio: ${moneyOrDash(source.total_value_cad)} · as of ${esc(source.as_of_date||'unknown')} · generated ${esc(source.generated_at||'unknown')}`
    :`Enter the balance remaining in ${esc(row.account)} at month-end.`;
  let entryText='This reconciliation is not included.';
  if(input.enabled&&row.adjustment===0)entryText='No journal entry is needed; the projected and ending balances match.';
  else if(input.enabled&&row.amount!=null)entryText=`Debit ${esc(row.debit)} · Credit ${esc(row.credit)} · ${money(row.amount)} · ${esc(row.note)}`;
  return `<div class="close-card">
    <div class="close-card-head">
      <div><h2>${esc(row.label)}</h2><div class="hint">${sourceText}</div></div>
      <label class="close-toggle"><input type="checkbox" id="close-${kind}-enabled" ${input.enabled?'checked':''}> Include</label>
    </div>
    <div class="field close-target"><label>${inputLabel}</label>
      <input type="text" inputmode="decimal" id="close-${kind}-balance" value="${esc(input.ending_balance)}" ${input.enabled?'':'disabled'}>
    </div>
    <div class="summary-grid close-summary">
      <div class="stat"><div class="n">${money(row.workbook_balance)}</div><div class="l">Workbook balance</div></div>
      <div class="stat"><div class="n ${row.pending_effect<0?'bad':row.pending_effect>0?'good':''}">${money(row.pending_effect)}</div><div class="l">Pending activity</div></div>
      <div class="stat"><div class="n">${money(row.projected_balance)}</div><div class="l">Projected before plug</div></div>
      <div class="stat"><div class="n">${moneyOrDash(row.ending_balance)}</div><div class="l">Ending balance</div></div>
      <div class="stat"><div class="n ${row.adjustment<0?'bad':row.adjustment>0?'good':''}">${moneyOrDash(row.adjustment)}</div><div class="l">Adjustment</div></div>
    </div>
    <div class="close-entry ${input.enabled?'':'muted'}">${entryText}${row.staged?' <span class="badge categorized">Staged</span>':''}</div>
  </div>`;
}

function renderClose(){
  if(!State.closeData||!State.closeInputs)return '<div class="panel"><div class="empty">Loading month close…</div></div>';
  const warnings=State.closeData.warnings||[], blockers=State.closeData.blockers||[];
  return `<div class="panel">
    <div class="close-toolbar">
      <div><h2>Month-end closing assistant</h2><div class="hint">Reconcile the workbook and pending Post queue to actual ending balances.</div></div>
      <div class="field"><label>Month to close</label><input type="month" id="close-month" value="${esc(State.closeMonth)}"></div>
    </div>
    ${warnings.map(message=>`<div class="close-alert warning">${esc(message)}</div>`).join('')}
    ${blockers.map(message=>`<div class="close-alert blocker">${esc(message)}</div>`).join('')}
  </div>
  <div class="close-grid">${renderCloseCard('investment')}${renderCloseCard('transit')}</div>
  <div class="panel">
    <div class="row">
      <button id="close-calculate">Calculate preview</button>
      <button class="primary" id="close-stage" ${State.closeData.can_stage&&!State.closeDirty?'':'disabled'}>Stage adjustments</button>
      <span class="hint" id="close-preview-state">${State.closeDirty?'Inputs changed — calculate a new preview before staging.':`Entries will be dated ${esc(State.closeData.closing_date)}.`}</span>
    </div>
  </div>`;
}

function readClosePayload(){
  const payload={month:State.closeMonth};
  for(const kind of ['investment','transit']){
    const enabled=$(`#close-${kind}-enabled`).checked;
    const raw=$(`#close-${kind}-balance`).value.trim().replace(/,/g,'');
    let endingBalance=null;
    if(enabled){
      endingBalance=Number(raw);
      if(raw===''||!Number.isFinite(endingBalance)||endingBalance<0){
        throw new Error(`${State.closeData.reconciliations[kind].label} ending balance must be a non-negative amount.`);
      }
    }
    payload[kind]={enabled,ending_balance:endingBalance};
  }
  return payload;
}

function markCloseDirty(){
  State.closeDirty=true;
  const stage=$('#close-stage'), state=$('#close-preview-state');
  if(stage)stage.disabled=true;
  if(state)state.textContent='Inputs changed — calculate a new preview before staging.';
}

function wireClose(){
  $('#close-month').addEventListener('change',async e=>{
    State.closeMonth=e.target.value;
    try{await loadMonthClose();await render()}catch(error){toast(error.message,false)}
  });
  for(const kind of ['investment','transit']){
    const checkbox=$(`#close-${kind}-enabled`), input=$(`#close-${kind}-balance`);
    checkbox.addEventListener('change',()=>{input.disabled=!checkbox.checked;markCloseDirty()});
    input.addEventListener('input',markCloseDirty);
  }
  $('#close-calculate').addEventListener('click',async()=>{
    try{
      const payload=readClosePayload();
      State.closeData=await api('POST','/api/month-close/preview',payload);
      setCloseInputsFromData();
      await render();
    }catch(error){toast(error.message,false)}
  });
  $('#close-stage').addEventListener('click',async()=>{
    try{
      const payload=readClosePayload();
      const result=await api('POST','/api/month-close/stage',payload);
      await loadTransactions();
      State.postingDate=result.closing_date;
      State.preview=await api('GET',`/api/post/preview?posting_date=${encodeURIComponent(State.postingDate)}`);
      State.tab='post';
      await render();
      toast(`Staged ${result.staged_count} month-close adjustment(s)`,true);
    }catch(error){toast(error.message,false)}
  });
}

// ------------------------------------------------------------------ Post --
function renderPost(){
  const p=State.preview;
  return `
  <div class="panel">
    <h2>Post to the Journal</h2>
    <div class="row">
      <div class="field"><label>Posting date</label><input type="date" id="post-date" value="${State.postingDate}"></div>
      <button class="primary" id="post-preview-btn">Preview</button>
      <button class="primary" id="post-btn" ${p&&p.lines.length?'':'disabled'}>Post to workbook</button>
    </div>
  </div>
  ${p?`<div class="panel">
    <h2>Preview — ${p.lines.length} line(s)</h2>
    ${p.skipped.length?`<div class="hint">${p.skipped.length} categorized row(s) skipped (no category or zero amount) — check the Review tab.</div>`:''}
    ${p.lines.length?`<div class="table-wrap"><table><thead><tr><th>Debit</th><th>Credit</th><th class="right">Amount</th><th>Note</th><th class="right">Components</th></tr></thead><tbody>
      ${p.lines.map(l=>`<tr><td>${esc(l.debit)}</td><td>${esc(l.credit)}</td><td class="right">${money(l.amount)}</td><td class="wrap">${esc(l.note)}</td><td class="right muted">${l.components.length}</td></tr>`).join('')}
    </tbody></table></div>`:'<div class="empty">Nothing to post yet — categorize some transactions first.</div>'}
  </div>`:''}
  <div class="panel">
    <h2>Recent batches</h2>
    ${State.batches.length?`<div class="table-wrap"><table><thead><tr><th></th><th>Posted</th><th>Posting date</th><th class="right">Lines</th><th>Rows</th></tr></thead><tbody>
      ${State.batches.map(batchRowsHtml).join('')}
    </tbody></table></div>`:'<div class="empty">No batches posted yet.</div>'}
  </div>`;
}

function batchRowsHtml(b){
  const expanded=State.expandedBatch===b.batch_id;
  const header=`<tr><td><button class="ghost batch-toggle" data-batch="${esc(b.batch_id)}">${expanded?'▾':'▸'}</button></td>
    <td>${esc(b.posted_at)}</td><td>${esc(b.posting_date)}</td><td class="right">${b.line_count}</td><td>${b.first_row}–${b.last_row}</td></tr>`;
  if(!expanded)return header;
  return header+`<tr><td></td><td colspan="4">${batchLinesHtml()}</td></tr>`;
}

function batchLinesHtml(){
  if(State.batchLines===null)return '<div class="muted">Loading…</div>';
  if(!State.batchLines.length)return '<div class="empty">No lines found for this batch.</div>';
  return `<table><thead><tr><th>Date</th><th>Debit</th><th>Credit</th><th class="right">Amount</th><th>Note</th><th></th></tr></thead><tbody>
    ${State.batchLines.map(lineHtml).join('')}
  </tbody></table>`;
}

function lineHtml(l){
  if(State.editingRow!==l.row_number){
    return `<tr data-row="${l.row_number}"${l.amount===0?' class="muted"':''}>
      <td>${esc(l.posting_date)}</td><td>${esc(l.debit)}</td><td>${esc(l.credit)}</td>
      <td class="right">${money(l.amount)}</td>
      <td class="wrap">${esc(l.note)}</td>
      <td><button class="ghost ln-edit" data-row="${l.row_number}">Edit</button></td>
    </tr>`;
  }
  const hint=l.is_formula?`<tr><td colspan="6" class="hint">This line sums multiple imported transactions — saving replaces it with a single fixed amount.</td></tr>`:'';
  return `<tr data-row="${l.row_number}">
    <td><input type="date" class="ln-date" value="${esc(l.posting_date)}"></td>
    <td><select class="ln-debit">${accountOptions(l.debit)}</select></td>
    <td><select class="ln-credit">${accountOptions(l.credit)}</select></td>
    <td class="right"><input type="number" step="0.01" class="ln-amount" value="${l.amount}" style="width:100px"></td>
    <td><input type="text" class="ln-note" value="${esc(l.note)}"></td>
    <td class="row"><button class="primary ln-save" data-row="${l.row_number}">Save</button><button class="ghost ln-cancel">Cancel</button><button class="danger ln-delete" data-row="${l.row_number}">Delete</button></td>
  </tr>${hint}`;
}

async function wirePost(){
  $('#post-date').addEventListener('change',e=>{State.postingDate=e.target.value});
  $('#post-preview-btn').addEventListener('click',async()=>{
    try{
      State.preview=await api('GET',`/api/post/preview?posting_date=${encodeURIComponent(State.postingDate)}`);
      render();
    }catch(e){toast(e.message,false)}
  });
  const postBtn=$('#post-btn');
  if(postBtn)postBtn.addEventListener('click',async()=>{
    if(!confirm('Write these lines to Personal Budget.xlsx now?'))return;
    try{
      const result=await api('POST','/api/post',{posting_date:State.postingDate});
      toast(`Posted ${result.line_count} line(s): rows ${result.first_row}–${result.last_row}`,true);
      State.preview=null;
      await loadTransactions(); await loadBatches();
      render();
    }catch(e){toast(e.message,false)}
  });
  $$('.batch-toggle').forEach(btn=>btn.addEventListener('click',async()=>{
    const batchId=btn.dataset.batch;
    if(State.expandedBatch===batchId){
      State.expandedBatch=null; State.batchLines=null; State.editingRow=null;
      render();
      return;
    }
    State.expandedBatch=batchId; State.batchLines=null; State.editingRow=null;
    render();
    try{
      State.batchLines=await api('GET',`/api/batches/${encodeURIComponent(batchId)}/lines`);
    }catch(e){toast(e.message,false);State.batchLines=[]}
    render();
  }));
  $$('.ln-edit').forEach(btn=>btn.addEventListener('click',()=>{
    State.editingRow=+btn.dataset.row; render();
  }));
  $$('.ln-cancel').forEach(btn=>btn.addEventListener('click',()=>{
    State.editingRow=null; render();
  }));
  // Save and Delete share one write path: a posted row is never removed
  // (that would shift every later row out of its batch range), so Delete is
  // just a save of amount 0 with the note tagged.
  const saveLine=async(rowNumber,fields,okMessage)=>{
    const original=State.batchLines.find(l=>l.row_number===rowNumber);
    const body={
      row_number:rowNumber, ...fields,
      expected:{
        posting_date:original.posting_date, debit:original.debit, credit:original.credit,
        amount:original.amount, note:original.note,
      },
    };
    try{
      await api('POST','/api/journal/row',body);
      toast(okMessage,true);
      State.editingRow=null;
      State.batchLines=await api('GET',`/api/batches/${encodeURIComponent(State.expandedBatch)}/lines`);
      render();
    }catch(e){
      toast(e.message,false);
      State.editingRow=null;
      try{State.batchLines=await api('GET',`/api/batches/${encodeURIComponent(State.expandedBatch)}/lines`)}catch(_e){/* keep stale lines */}
      render();
    }
  };
  $$('.ln-save').forEach(btn=>btn.addEventListener('click',()=>{
    const tr=btn.closest('tr');
    saveLine(+btn.dataset.row,{
      posting_date:$('.ln-date',tr).value,
      debit:$('.ln-debit',tr).value,
      credit:$('.ln-credit',tr).value,
      amount:parseFloat($('.ln-amount',tr).value),
      note:$('.ln-note',tr).value,
    },'Row updated');
  }));
  $$('.ln-delete').forEach(btn=>btn.addEventListener('click',()=>{
    if(!confirm('Delete this row? It stays in the Journal with amount 0 so later rows keep their positions.'))return;
    const rowNumber=+btn.dataset.row;
    const original=State.batchLines.find(l=>l.row_number===rowNumber);
    const note=original.note||'';
    saveLine(rowNumber,{
      posting_date:original.posting_date, debit:original.debit, credit:original.credit,
      amount:0, note:note.startsWith('[deleted]')?note:`[deleted] ${note}`.trim(),
    },'Row deleted (zeroed)');
  }));
}

// ----------------------------------------------------------------- Rules --
function renderRules(){
  return `
  <div class="panel">
    <h2>Add a rule</h2>
    <div class="row">
      <input type="text" id="ru-pattern" placeholder="Pattern, e.g. EXAMPLE MARKET">
      <select id="ru-match"><option value="contains">Contains</option><option value="regex">Regex</option></select>
      <select id="ru-category">${categoryOptions()}</select>
      <input type="text" id="ru-note" placeholder="Note">
      <button class="primary" id="ru-add">Add rule</button>
    </div>
  </div>
  <div class="panel table-wrap">
    <h2>Learned rules (${State.rules.length})</h2>
    ${State.rules.length?`<table><thead><tr><th>Pattern</th><th>Type</th><th>Category</th><th>Note</th><th class="right">Hits</th><th></th></tr></thead><tbody>
      ${State.rules.map(r=>`<tr><td>${esc(r.pattern)}</td><td>${esc(r.match_type)}</td><td>${esc(r.category)}</td><td class="wrap">${esc(r.note||'')}</td>
        <td class="right">${r.hits}</td><td><button class="danger ru-delete" data-id="${r.id}">Delete</button></td></tr>`).join('')}
    </tbody></table>`:'<div class="empty">No rules yet.</div>'}
  </div>`;
}

function wireRules(){
  $('#ru-add').addEventListener('click',async()=>{
    const pattern=$('#ru-pattern').value.trim(), category=$('#ru-category').value, note=$('#ru-note').value;
    const match_type=$('#ru-match').value;
    if(!pattern||!category){toast('Pattern and category are required',false);return}
    try{
      await api('POST','/api/rules',{pattern,category,note,match_type});
      await loadRules(); render();
    }catch(e){toast(e.message,false)}
  });
  $$('.ru-delete').forEach(btn=>btn.addEventListener('click',async()=>{
    await api('DELETE',`/api/rules/${btn.dataset.id}`);
    await loadRules(); render();
  }));
}

// ------------------------------------------------------------------- Tabs --
async function render(){
  $$('#tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab===State.tab));
  const pos=captureViewPosition();
  const view=$('#view');
  if(State.tab==='import'){view.innerHTML=renderImport();wireImport()}
  else if(State.tab==='review'){view.innerHTML=renderReview();wireReview()}
  else if(State.tab==='transfers'){view.innerHTML=renderTransfers();wireTransfers()}
  else if(State.tab==='close'){view.innerHTML=renderClose();wireClose()}
  else if(State.tab==='post'){view.innerHTML=renderPost();await wirePost()}
  else if(State.tab==='rules'){view.innerHTML=renderRules();wireRules()}
  restoreViewPosition(pos);
  lastRenderedTab=State.tab;
}

async function setTab(tab){
  State.tab=tab;
  if(tab==='review')await loadTransactions();
  if(tab==='transfers'){await loadTransactions();await refreshSuggestedTransfers()}
  if(tab==='close')await loadMonthClose();
  if(tab==='rules')await loadRules();
  if(tab==='post'){
    State.preview=null; State.expandedBatch=null; State.batchLines=null; State.editingRow=null;
    await loadBatches();
  }
  await render();
}

$$('#tabs button').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));

(async()=>{
  try{
    await loadAccounts(); await loadTransactions(); await loadRules();
    $('#workbook-status').textContent=`${State.accounts.length} accounts loaded from Ledger`;
    await render();
  }catch(e){
    $('#workbook-status').textContent='Failed to load — is the workbook open elsewhere?';
    toast(e.message,false);
  }
})();
})();
