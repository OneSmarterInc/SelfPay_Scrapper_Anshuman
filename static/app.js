const $ = id => document.getElementById(id);

const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const link = (url, label='Open source') => /^https?:\/\//i.test(url || '') ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)}</a>` : 'No source URL';

let pauseSupported=false;
const reviewSelected=new Set();

let token='', directory=[], selected=new Set(), page=0, currentJob=null, currentView='dashboard', pollTimer, busy=false, jobGeneration=0;

async function api(path, options={}, refreshed=false) {

    const response=await fetch(path,{...options,headers:{'x-app-token':token,...options.headers}});

    const body=await response.text();

    let data;try{data=body?JSON.parse(body):{}}catch{data={detail:body||response.statusText}}

    if(!response.ok){

        if(response.status===403&&!refreshed&&path!=='/api/session'){

            const session=await fetch('/api/session');

            if(session.ok){token=(await session.json()).token;return api(path,options,true)}

        }

        throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));

    }

    return data;

}

function error(err){$('message').textContent=err.message || err;$('message').hidden=false;}

function clearError(){$('message').hidden=true;}

function view(name,{load=true}={}){if(currentView!==name)$('mainContent').scrollTop=0;clearTimeout(dashboardTimer);dashboardGeneration++;if(name==='dashboard')loadDashboard();updatePageRoute(name);clearTimeout(runningTimer);runningGeneration++;if(name==='running')loadRunning();if(name!=='results'){clearTimeout(pollTimer);jobGeneration++}$('recordDetail').hidden=true;currentView=name;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('.nav').forEach(el=>setNavigationState(el,el.dataset.view===name || name==='results' && el.dataset.view==='history'));clearError();if(name==='history')loadHistory().catch(error);if(name==='enrichment'&&load)loadProposals().catch(error);if(name==='changes')loadChanges().catch(error);if(name==='scan'){$('selectionNote').hidden=false;$('selectionNote').textContent=selected.size?`${selected.size.toLocaleString()} records selected · Existing values included for comparison.`:'No records selected. Choose centers from your directory.';}}

function badge(text){const good=['accepted','completed','read','self-pay / cash'];const bad=['rejected','failed','error'];return `<span class="badge ${good.includes(text)?'good':bad.includes(text)?'bad':'warn'}">${escapeHTML(text)}</span>`;}

function empty(title,body){return `<div class="empty"><h2>${escapeHTML(title)}</h2><p>${escapeHTML(body)}</p></div>`;}

async function loadDirectory(){const data=await api('/api/directory');directory=data.records.map(r=>({...r,missing_fields:r.missing_fields||[]}));selected.clear();$('navCount').textContent=directory.length.toLocaleString();$('directorySummary').innerHTML=`<span><strong>${directory.length.toLocaleString()}</strong> source records</span><span><strong>${(data.summary.with_urls||0).toLocaleString()}</strong> with source URLs</span><span><strong>${(data.summary.with_missing_contacts||0).toLocaleString()}</strong> records with contact gaps</span>`;$('categoryFilter').innerHTML='<option value="">All categories</option>'+[...new Set(directory.map(r=>r.category))].sort().map(c=>`<option>${escapeHTML(c)}</option>`).join('');page=0;renderDirectory();}

function filteredDirectory(){const query=$('directorySearch').value.trim().toLowerCase();const category=$('categoryFilter').value;const gap=$('gapFilter').value;return directory.filter(r=>(!gap || (gap==='any'?r.missing_fields.length:[...r.missing_fields,...(r.missing_price_fields||[])].includes(gap)))&&(!category || r.category===category)&&`${r.name} ${r.state} ${r.city} ${r.id}`.toLowerCase().includes(query));}
function renderDirectory(){const filtered=filteredDirectory();const size=Number($('directoryPageSize').value);page=Math.min(page,Math.max(0,Math.ceil(filtered.length/size)-1));const start=page*size;const rows=filtered.slice(start,start+size);$('directoryRows').innerHTML=rows.length?rows.map(r=>`<tr class="${(r.updates||[]).length?'has-updates':''}"><td><input type="checkbox" aria-label="Select ${escapeHTML(r.name)}" data-select="${escapeHTML(r.id)}" ${selected.has(r.id)?'checked':''} ${r.urls.length?'':'disabled'}></td><td><button class="text-button" data-record="${escapeHTML(r.id)}">${escapeHTML(r.name)}</button><small>${escapeHTML(r.id)} · ${escapeHTML(r.category)}</small><button class="text-button" data-center-record="${escapeHTML(r.id)}">Fetched details</button>${(r.updates||[]).length?`<span class="change-label ${(r.updates||[]).some(u=>u.comparison_tone==='different')?'different':'filled'}">${r.updates.length} updates: ${(r.updates||[]).some(u=>u.state!=='applied')?'Review needed':'Applied'}</span>`:''}<small>${r.missing_fields.length?'Missing: '+r.missing_fields.map(f=>({'Current supported address':'address','Current supported call number':'phone','Current usable email':'email'}[f])).join(', '):'Contact fields populated'}</small></td><td>${escapeHTML(r.state)}<small>${escapeHTML(r.city)}</small><small>${escapeHTML(r.scope)}</small></td><td>${badge(r.status||'Not specified')}<small>${escapeHTML(r.baseline.slice(0,160)||'No current supported price recorded')}</small></td><td>${link(r.urls[0])}<small>${r.urls.length} source links</small></td></tr>`).join(''):`<tr><td colspan="5">${empty(directory.length?'No matching records':'Import your imaging directory',directory.length?'Try another provider, state or category.':'Choose the original CSV to browse records and check their prices.')}</td></tr>`;$('pageCount').textContent=filtered.length?`${start+1}–${Math.min(start+size,filtered.length)} of ${filtered.length.toLocaleString()} records`:'0 records';$('previousPage').disabled=page===0;$('nextPage').disabled=start+size>=filtered.length;updateSelection();}

function updateSelection(){$('selectedCount').textContent=selected.size;$('scanSelected').disabled=!selected.size;}

async function expandDirectoryCenter(button){const row=button.closest('tr');if(row.nextElementSibling?.classList.contains('directory-center-expanded')){row.nextElementSibling.remove();button.textContent='Fetched details';return;}button.disabled=true;try{const groups=(await api('/api/centers?record_id='+encodeURIComponent(button.dataset.centerRecord))).centers;row.insertAdjacentHTML('afterend',`<tr class="directory-center-expanded"><td colspan="5">${groups.length?centerTable(groups):'<p>No fetched findings for this record yet.</p>'}</td></tr>`);button.textContent='Hide fetched details';}finally{button.disabled=false;}}

async function showRecord(id){const r=await api('/api/directory/'+encodeURIComponent(id));const highlights=(directory.find(item=>item.id===id)?.updates||[]);$('recordFields').innerHTML=`<h2>${escapeHTML(r.name)}</h2>${highlights.map(u=>`<div class="field-update ${escapeHTML(u.comparison_tone)}"><strong>${escapeHTML(u.field)}</strong><p>${escapeHTML(u.label)} · ${escapeHTML(u.state)}</p><p>Before: ${escapeHTML(u.old_value||'Blank / not recorded')}</p><p>Found: ${escapeHTML(typeof u.new_value==='object'?JSON.stringify(u.new_value):u.new_value)}</p></div>`).join('')}<dl>${Object.entries(r.source).filter(([k,v])=>v&&k!=='Enrichment provenance JSON').map(([k,v])=>`<dt>${escapeHTML(k)}</dt><dd>${escapeHTML(v)}</dd>`).join('')}</dl>`;$('recordDetail').hidden=false;$('recordDetail').open=true;$('closeRecord').focus();}

async function loadHistory(){const jobs=await api('/api/scans');const visible=paginateList(jobs,'history');$('historyRows').innerHTML=jobs.length?visible.map(j=>`<div class="history-item"><div><strong>${escapeHTML(j.hospitals.map(h=>h.name).join(', '))}</strong>${badge(j.status)}<p>${escapeHTML(new Date(j.created_at).toLocaleString())} · ${j.pages} page log entries · ${j.prices} price matches</p></div><a class="button secondary" href="#/scans/${j.id}" data-job="${j.id}">View findings</a></div>`).join(''):empty('Your first scan starts here','Select directory records or enter an official website in New scan.');}

async function openJob(id){

    clearTimeout(pollTimer);const generation=++jobGeneration;

    const job=await api('/api/scans/'+id);

    if(generation!==jobGeneration)return;

    currentJob=job;view('results');

    for(const key of ['priceSearch','priceType','reviewFilter'])$(key).value='';

    document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.tab==='prices'));

    $('pricesPanel').hidden=false;$('coveragePanel').hidden=true;$('centersPanel').hidden=true;scanCenterPage=0;

    renderJob();schedulePoll();

}

function schedulePoll(){

    clearTimeout(pollTimer);

    if(currentView!=='results'||!currentJob||!['queued','running'].includes(currentJob.status))return;

    const id=currentJob.id,generation=jobGeneration;

    pollTimer=setTimeout(async()=>{

        try{

            const updated=await api('/api/scans/'+id);

            if(generation!==jobGeneration||currentJob?.id!==id||currentView!=='results')return;

            currentJob=updated;renderJob();

        }catch(err){if(generation===jobGeneration)error(err)}

        if(generation===jobGeneration)schedulePoll();

    },2500);

}

function renderJob(){const j=currentJob;$('contactCount').textContent=`${(j.contacts||[]).length} published contact findings · ${(j.entities||[]).length} location observations`;$('openUpdates').disabled=['queued','running'].includes(j.status);$('scanTitle').textContent=j.hospitals.length===1?j.hospitals[0].name:'Review your scan';$('scanDescription').textContent=`${j.hospitals.length} source records · ${j.max_pages==null?'All discoverable pages':j.max_pages+' pages per record'} · Link depth ${j.max_depth==null?'Unlimited':j.max_depth}`;const active=['queued','running'].includes(j.status);$('pauseScan').hidden=!pauseSupported;$('pauseScan').disabled=!active;$('pauseScan').disabled=!active||!!j.pause_requested||!!j.cancel_requested;$('pauseScan').textContent=j.pause_requested?'Pausing after current page…':'Pause scan';$('resumeScan').hidden=!pauseSupported;$('resumeScan').disabled=j.status!=='paused';$('stopScan').hidden=!active&&j.status!=='paused';$('stopScan').disabled=j.cancel_requested;$('stopScan').textContent=j.cancel_requested?'Stopping after current request…':'Stop scan';$('scanProgress').innerHTML=`${scanTimeMarkup(j)}<br><strong>${escapeHTML(j.status==='completed'?'Scan finished — review findings and coverage':j.status.charAt(0).toUpperCase()+j.status.slice(1))}</strong>${j.results.length} imaging price matches · ${j.pages.filter(p=>p.status==='read').length} pages read · ${j.pages.filter(p=>['error','blocked','partial','unsupported','discovery warning'].includes(p.status)).length} pages needing attention${j.concurrency>1?'<br><small>'+Number(j.completed_centers||0)+' / '+j.hospitals.length+' centers processed · Up to '+Number(j.concurrency||1)+' at a time</small>'+scanSourcesMarkup(j):j.current_url?'<br><small>'+escapeHTML(j.current_url)+'</small>':''}${j.error?'<br>'+escapeHTML(j.error):''}${j.hospitals.filter(h=>h.stop_reason).map(h=>'<br><small>'+escapeHTML(h.name)+': '+escapeHTML(h.stop_reason)+(h.required_details?.missing?.length?' · Missing: '+escapeHTML(h.required_details.missing.join(', ')):'')+'</small>').join('')}`;$('priceCount').textContent=j.results.length;$('coverageCount').textContent=j.pages.length;$('exportAll').href=`/api/scans/${j.id}/export.xlsx`;$('exportAccepted').href=`/api/scans/${j.id}/export.xlsx?accepted_only=true`;

const opened=new Set([...document.querySelectorAll('.price-item details[open]')].map(e=>e.dataset.id));renderPrices();document.querySelectorAll('.price-item details').forEach(el=>el.open=opened.has(el.dataset.id));$('scanWarnings').innerHTML=j.warnings.map(w=>`<div class="notice">${escapeHTML(w)}</div>`).join('');$('coverageRows').innerHTML=paginateList(j.pages,'coverage').map(p=>`<tr><td>${link(p.url,p.url)}<small>${escapeHTML(p.hospital)}</small></td><td>${badge(p.status)}</td><td>${escapeHTML(p.detail || (p.matches!==undefined?p.matches+' extracted candidates':''))}</td></tr>`).join('');}

function baselineDetail(r){const b=(currentJob.baseline_records||[]).find(row=>row.id===r.record_id);if(!b)return '';return `<strong>Original source quotes (not freshly verified)</strong><p>MRI: ${escapeHTML(b.original_mri||'Not recorded')}<br>CT: ${escapeHTML(b.original_ct||'Not recorded')}</p><strong>Existing scope & fee caveats</strong><p>${escapeHTML(b.scope||'Scope not specified')}<br>${escapeHTML(b.caveats||'No caveats recorded')}</p>`;}

function renderPrices(){if(!currentJob)return;const q=$('priceSearch').value.trim().toLowerCase(), type=$('priceType').value, review=$('reviewFilter').value;const records=currentJob.results.filter(r=>(!type||r.price_type===type)&&(!review||r.review===review)&&`${r.hospital} ${r.service} ${r.modality} ${r.cpt}`.toLowerCase().includes(q));const visible=paginateList(records,'prices');$('priceRows').innerHTML=records.length?visible.map(r=>`<article class="price-item" data-result-key="${escapeHTML(r.id)}"><div class="price-top"><div><small>${escapeHTML(r.hospital)}${r.record_id?' · '+escapeHTML(r.record_id):''}</small><h2>${escapeHTML(r.service)}</h2></div><div class="price-number">${escapeHTML(r.currency==='USD'?'$':r.currency+' ')}${escapeHTML(Number(r.amount).toLocaleString('en-US',{maximumFractionDigits:2}))}</div></div><div class="tags">${badge(r.price_type)}${badge(r.qualifier)}<span class="badge">${escapeHTML(r.modality)} · ${escapeHTML(r.contrast)}</span>${r.cpt?'<span class="badge">Code '+escapeHTML(r.cpt)+'</span>':''}</div><details data-id="${escapeHTML(r.id)}"><summary>View source evidence & existing price</summary><blockquote>${escapeHTML(r.evidence)}</blockquote><div class="evidence-grid"><div><strong>Source & extraction</strong><p>${link(r.source_url)}${r.page?' · PDF page '+r.page:''}<br>${escapeHTML(r.method)} · ${escapeHTML(new Date(r.retrieved_at).toLocaleString())}</p><p>${escapeHTML(r.context)}</p><p>${escapeHTML(r.reason)}</p></div><div><strong>Existing supported price scope</strong><p>${escapeHTML(r.baseline_scope||(r.record_id?'No current supported price recorded. See original quotes below.':'No directory baseline attached to this website scan.'))}</p>${baselineDetail(r)}<p></p><strong>Before accepting</strong><p>Confirm the exact exam, facility, contrast, eligibility and included fees. This finding does not automatically replace the existing price.</p></div></div></details><div class="review-actions"><button class="secondary" data-review="accepted" data-id="${escapeHTML(r.id)}" ${r.review==='accepted'?'disabled':''}>Accept evidence</button><button class="secondary" data-review="rejected" data-id="${escapeHTML(r.id)}" ${r.review==='rejected'?'disabled':''}>Reject</button>${r.review!=='unreviewed'?'<button class="text-button" data-review="unreviewed" data-id="'+escapeHTML(r.id)+'">Reset</button>':''}${badge(r.review)}</div></article>`).join(''):empty(currentJob.results.length?'No matching evidence':'No imaging prices extracted yet',currentJob.status==='running'?'The scan is still reading source pages.':'Check Page coverage for blocked pages, limits and external pricing sources. Missing evidence is not a zero price.');}

document.addEventListener('click',async event=>{const el=event.target.closest('button,a[data-view],a[data-job]');if(!el)return;if(el.tagName==='A'){if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault()}try{if(el.dataset.view)view(el.dataset.view);if(el.dataset.go)view(el.dataset.go);if(el.dataset.record)await showRecord(el.dataset.record);if(el.dataset.centerRecord)await expandDirectoryCenter(el);if(el.dataset.job)await openJob(el.dataset.job);if(el.dataset.tab){document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t===el));$('pricesPanel').hidden=el.dataset.tab!=='prices';$('coveragePanel').hidden=el.dataset.tab!=='coverage';$('centersPanel').hidden=el.dataset.tab!=='centers';if(el.dataset.tab==='centers')await loadScanCenters();}if(el.dataset.review){el.disabled=true;await api(`/api/scans/${currentJob.id}/results/${encodeURIComponent(el.dataset.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({review:el.dataset.review})});currentJob=await api('/api/scans/'+currentJob.id);renderJob();}}catch(err){el.disabled=false;error(err)}});

$('directoryRows').addEventListener('change',e=>{if(!e.target.dataset.select)return;const id=e.target.dataset.select;if(e.target.checked){selected.add(id)}else selected.delete(id);updateSelection();});

$('directorySearch').addEventListener('input',()=>{page=0;renderDirectory()});$('categoryFilter').addEventListener('change',()=>{page=0;renderDirectory()});$('previousPage').onclick=()=>{page--;renderDirectory();resetListScroll('directory')};$('nextPage').onclick=()=>{page++;renderDirectory();resetListScroll('directory')};$('scanSelected').onclick=()=>view('scan');

$('importButton').onclick=()=>$('importFile').click();

$('importFile').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try{clearError();const imported=await api('/api/directory',{method:'POST',headers:{'Content-Type':'text/csv'},body:file});await loadDirectory();$('directorySummary').insertAdjacentHTML('beforeend',`<span>Import: ${imported.added} added, ${imported.unchanged} unchanged, ${imported.conflicts_preserved} existing records preserved</span>`);}catch(err){error(err)}finally{e.target.value=''}});

function updateScanFiles(){const files=[...$('scanFiles').files];$('scanFileSummary').textContent=files.length?files.map(f=>f.name).join(' • '):'No files selected.';$('clearScanFiles').disabled=!files.length;}
$('scanFiles').addEventListener('change',updateScanFiles);
$('clearScanFiles').addEventListener('click',()=>{$('scanFiles').value='';updateScanFiles();});
async function scanWorkbooks(){
 const files=[...$('scanFiles').files];
 if(files.length>10)throw new Error('Select no more than 10 Excel files.');
 for(const file of files){if(!/\.xlsx$/i.test(file.name))throw new Error(file.name+': only .xlsx files are accepted.');if(file.size>2*1024*1024)throw new Error(file.name+': file exceeds 2 MB.');}
 return Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,content:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);})));}
$('scanForm').addEventListener('submit',async e=>{e.preventDefault();if(busy)return;busy=true;$('startScan').disabled=true;$('startScan').textContent='Starting scan…';clearError();try{const urls=$('urls').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);const files=await scanWorkbooks();const result=await api('/api/scans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,files,record_ids:[...selected],concurrency:readConcurrency($('scanConcurrency').value),max_pages:null,max_depth:null})});await openJob(result.id);}catch(err){error(err)}finally{busy=false;$('startScan').disabled=false;$('startScan').textContent='Start scan'}});

$('stopScan').onclick=async()=>{try{await api('/api/scans/'+currentJob.id+'/cancel',{method:'POST'});currentJob=await api('/api/scans/'+currentJob.id);renderJob()}catch(err){error(err)}};

for(const id of ['priceSearch','priceType','reviewFilter'])$(id).addEventListener(id==='priceSearch'?'input':'change',()=>{listPages.prices.page=0;renderPrices()});

(async()=>{try{const session=await api('/api/session');token=session.token;pauseSupported=!!session.pause_resume;await loadDirectory();await updateProposalCount();await restorePageRoute()}catch(err){error(err)}})();

$('gapFilter').addEventListener('change',()=>{page=0;renderDirectory()});

$('clearSelection').onclick=()=>{selected.clear();renderDirectory()};

$('selectAllRecords').onclick=()=>{filteredDirectory().filter(r=>r.urls.length).forEach(r=>selected.add(r.id));renderDirectory()};

let proposals=[],proposalPage=0,centerRecords=[],scanCenterPage=0,scanCenters=[];

const kindName={contact_add:'Additional contact',fill:'Fill missing field',price_evidence:'New price evidence',new_record:'New location lead',conflict:'Existing value differs'};

async function updateProposalCount(){const data=await api('/api/enrichment');$('proposalNav').textContent=data.counts.pending||0;}

function filteredProposals(ignoreStatus=false){const q=$('proposalSearch').value.trim().toLowerCase(),kind=$('proposalKind').value,status=$('proposalStatus').value,scan=$('proposalScan').value;return proposals.filter(p=>(ignoreStatus||!status||p.status===status)&&(!kind||p.kind===kind)&&(!scan||p.scan_id===scan)&&`${p.record_name} ${p.record_id} ${p.field} ${typeof p.value==='object'?JSON.stringify(p.value):p.value}`.toLowerCase().includes(q));}

async function loadProposals(){const data=await api('/api/enrichment');proposals=data.proposals;centerRecords=(await api('/api/centers')).centers;$('proposalNav').textContent=data.counts.pending||0;const current=$('proposalScan').value;const jobs=await api('/api/scans');$('proposalScan').innerHTML='<option value="">All scans</option>'+jobs.map(j=>`<option value="${j.id}">${escapeHTML(new Date(j.created_at).toLocaleString())} · ${escapeHTML(j.hospitals.map(h=>h.name).join(', '))}</option>`).join('');$('proposalScan').value=current;renderProposals();}

function matchingDetails(p){
 let html='';
 const match=p.exam_comparison;
 if(match){const fields={modality:'Modality',body_part:'Body part',contrast:'Contrast',cpt:'CPT / HCPCS',laterality:'Side',fees:'Fees',qualifier:'Price qualifier'};html+=`<details class="match-details"><summary>${escapeHTML(match.state==='different_scope'?'Exam details differ':match.state==='comparable'?'Compare matched exam details':'Exam details need review')}</summary><p>${escapeHTML(match.reason)}</p><div class="table-wrap"><table><thead><tr><th>Exam detail</th><th>Previously recorded</th><th>Found</th></tr></thead><tbody>${Object.entries(fields).map(([key,label])=>`<tr><th scope="row">${label}</th><td>${escapeHTML(match.previous[key]||'Not stated')}</td><td>${escapeHTML(match.found[key]||'Not stated')}</td></tr>`).join('')}</tbody></table></div></details>`;}
 if(p.duplicate_candidates?.length)html+=`<div class="notice"><strong>Possible existing location — no automatic merge</strong><p>Check these directory records before adding another location. Different providers can share a building.</p><ul>${p.duplicate_candidates.map(c=>`<li><strong>${escapeHTML(c.id)} · ${escapeHTML(c.name)}</strong> — ${escapeHTML(c.address||'Address not recorded')}<br>${escapeHTML(c.reason)}</li>`).join('')}</ul></div>`;
 return html;
}
function proposalActions(p){
 if(!p)return '';
 if(['applied','invalidated'].includes(p.status))return `<div class="review-actions"><button disabled>Approve</button><button class="secondary" disabled>Reject</button></div><small>${escapeHTML(p.invalidation_reason||(p.status==='applied'?'Applied; use Change history to undo.':'Source invalidated.'))}</small>`;
 const blocked=!!p.duplicate_candidates?.length;
 const label=p.kind==='contact_add'?'Approve addition':p.kind==='conflict'?'Approve replacement':p.kind==='new_record'?'Approve location':'Approve';
 return `<div class="review-actions"><button data-proposal-review="approved" data-proposal-id="${escapeHTML(p.id)}" data-proposal-ids="${escapeHTML(JSON.stringify(p.group_ids||[p.id]))}" ${p.kind==='conflict'?'data-replace-existing="true"':''} ${p.status==='approved'||blocked?'disabled':''}>${label}</button><button class="secondary" data-proposal-review="rejected" data-proposal-id="${escapeHTML(p.id)}" data-proposal-ids="${escapeHTML(JSON.stringify(p.group_ids||[p.id]))}" ${p.status==='rejected'?'disabled':''}>Reject</button>${p.status!=='pending'?`<button class="text-button" data-proposal-review="pending" data-proposal-id="${escapeHTML(p.id)}" data-proposal-ids="${escapeHTML(JSON.stringify(p.group_ids||[p.id]))}">Reset</button>`:''}</div>${blocked?`<small>New location blocked: possible existing record.</small>${p.duplicate_candidates.map(c=>`<button class="secondary" data-review-existing="${escapeHTML(c.id)}">Review ${escapeHTML(c.id)} updates</button>`).join('')}`:p.kind==='conflict'?'<small>Replaces after Apply.</small>':p.kind==='new_record'?'<small>Location fields reviewed together.</small>':''}`;
}
function observationActions(o,p,actionable){
 if(p&&actionable)return proposalActions(p);
 if(p)return '<div class="review-actions"><button disabled>Approve</button><button class="secondary" disabled>Reject</button></div><small>Outside current filter.</small>';
 if(o.review_target)return `<div class="review-actions"><button data-observation-review="accepted" data-observation="${escapeHTML(JSON.stringify(o.review_targets?.length?o.review_targets:[o.review_target]))}" ${o.status==='accepted'?'disabled':''}>Approve evidence</button><button class="secondary" data-observation-review="rejected" data-observation="${escapeHTML(JSON.stringify(o.review_targets?.length?o.review_targets:[o.review_target]))}" ${o.status==='rejected'?'disabled':''}>Reject</button></div><small>Evidence only; directory unchanged.${o.issues?.length?' Not eligible to apply.':''}</small>`;
 return '<div class="review-actions"><button disabled>Approve</button><button class="secondary" disabled>Reject</button></div><small>No source. Rescan required.</small>';
}
function centerOldCell(g,fields){
 return fields.map(field=>{
  const observed=g.observations.filter(o=>o.field===field);
  const values=[...new Set(observed.length?observed.map(o=>o.old_value||''): [g.current[field]||''])];
  return `<div class="center-old-field">${fields.length>1?`<small>${escapeHTML(field)}</small>`:''}${values.map(value=>`<p>${escapeHTML(value||'Blank / not recorded')}</p>`).join('')}</div>`;
 }).join('')||'<span class="muted">Blank / not recorded</span>';
}
function compactPriceValue(value){
 let text=String(value||'').replace(/\b(?:code|CPT\/HCPCS) not stated\b/gi,'').replace(/\bnot specified\b/gi,'').replace(/\blisted\b/gi,'').replace(/\(\s*[;,]?\s*/g,'(').replace(/;\s*;/g,';').replace(/[;,]\s*\)/g,')').replace(/\(\s*\)/g,'').replace(/\s+/g,' ').trim();
 const match=text.match(/^(.+?):\s*((?:USD|CAD|EUR|GBP)\s*[\d,.]+)(.*)$/i);
 if(match){let service=match[1].trim();const unclear=service.length>100||/running the|versus|would have|how much|paying cash|insurance co-pay/i.test(service);if(unclear)service='Exam unclear — verify source';text=service+' · '+match[2]+match[3];}
 return text;
}
function centerCell(g,fields,actionIds){
 const findings=g.observations.filter(o=>fields.includes(o.field)&&(!actionIds||!(o.proposal_ids||[o.proposal_id]).filter(Boolean).length||(o.proposal_ids||[o.proposal_id]).some(id=>actionIds.has(id))));
 if(!findings.length)return '<span class="center-no-find">No new value</span><div class="review-actions"><button disabled>Approve</button><button class="secondary" disabled>Reject</button></div>';
 return findings.map(o=>{
  const groupIds=(o.proposal_ids||[o.proposal_id]).filter(id=>actionIds?.has(id));
  const original=proposals.find(p=>p.id===(groupIds[0]||o.proposal_id));
  const p=original?{...original,group_ids:groupIds}:null;
  const actionable=groupIds.length>0;
  const isPrice=/price|offer/i.test(o.field);
  const displayValue=isPrice?compactPriceValue(o.value):o.value;
  const same=o.label==='Unchanged';
  const mismatch=!same&&(o.label==='Old → Different'||p?.comparison_tone==='different');
  const shortLabel=same?'Already present':mismatch?'Mismatch — review required':o.label==='New price evidence — compare scope'?'Price evidence':o.label==='Needs source review'?'Verify source':o.label;
  const evidence={value:o.value,scan_id:o.scan_id,field:o.field,sources:o.sources||[{source_url:o.source_url,crawl_route:o.crawl_route||[]}],issues:o.issues||[],evidence:p?.evidence||'',scope:p?.scope||'',proposal_id:o.proposal_id};
  return `<div class="center-observation ${mismatch?'value-mismatch':same?'value-present':''}"><span class="change-label ${o.label==='Blank → New'?'filled':o.label==='Old → Different'?'different':'evidence'}">${escapeHTML(shortLabel)}</span> ${badge(o.status)}${fields.length>1&&!isPrice?`<small>${escapeHTML(o.field)}</small>`:''}<p class="fetched-value">${escapeHTML(displayValue)}</p><button class="text-button" data-center-evidence="${escapeHTML(JSON.stringify(evidence))}">Source & route</button>${same?'<small class="present-check">Checked against existing data · No update needed</small>':observationActions(o,p,actionable)}</div>`;
 }).join('');
}
function centerTable(groups,actionIds=null){
 const categories=[{label:'Address / location',fields:['Current supported address']},{label:'Phone',fields:['Current supported call number']},{label:'Email',fields:['Current usable email']},{label:'Prices / offers',fields:['MRI self-pay (USD)','CT self-pay (USD)','Current supported offer / price scope','New self-pay price evidence']},{label:'Other details',fields:null}];
 const known=categories.flatMap(c=>c.fields||[]);
 return `<div class="center-table-scroll"><table class="center-table center-paired"><caption>Old: recorded · New: fetched</caption><thead><tr><th rowspan="2" scope="col" class="center-name">Center</th>${categories.map(c=>`<th colspan="2" scope="colgroup">${c.label}</th>`).join('')}</tr><tr>${categories.map(c=>'<th scope="col" class="old-heading">Old</th><th scope="col" class="new-heading">New</th>').join('')}</tr></thead><tbody>${groups.map(g=>`<tr><th scope="row" class="center-name">${actionIds?`<label><input type="checkbox" data-review-center="${escapeHTML(g.key)}" aria-label="Select updates for ${escapeHTML(g.name)}" ${g.proposal_ids.filter(id=>actionIds.has(id)&&proposals.some(p=>p.id===id&&['pending','approved'].includes(p.status))).length&&g.proposal_ids.filter(id=>actionIds.has(id)&&proposals.some(p=>p.id===id&&['pending','approved'].includes(p.status))).every(id=>reviewSelected.has(id))?'checked':''}> Select</label>`:''}<strong>${escapeHTML(g.name)}</strong><small>${escapeHTML(g.record_id||'New / unassigned location')}</small><small>${escapeHTML([g.city,g.state].filter(Boolean).join(', '))}</small><span>${g.observations.length} fetched details</span>${g.latest_extraction_version<7?'<small>Older scan. Rescan recommended.</small>':''}${g.record_id?`<button class="text-button" data-rescan-center="${escapeHTML(g.record_id)}">Rescan</button>`:''}</th>${categories.map(c=>{const fields=c.fields||[...new Set(['City / area','State',...g.observations.map(o=>o.field)].filter(f=>!known.includes(f)))];return `<td class="old-value"><div class="center-cell-content">${centerOldCell(g,fields)}</div></td><td class="new-value"><div class="center-cell-content">${centerCell(g,fields,actionIds)}</div></td>`}).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function renderProposals(){
 const filtered=filteredProposals(),ids=new Set(filtered.map(p=>p.id));
 const eligible=bulkEligible(filtered.filter(p=>reviewSelected.has(p.id)));$('approveAll').disabled=bulkApproving||!eligible.length;$('approveAll').textContent=bulkApproving?'Approving updates...':`Approve selected (${eligible.length})`;
 const approved=filteredProposals(true).filter(p=>p.status==='approved');$('applyApproved').disabled=!approved.length;$('applyApproved').textContent=`Apply approved updates (${Math.min(approved.length,200)})`;
 const scan=$('proposalScan').value;
 let groups=centerRecords.filter(g=>g.proposal_ids.some(id=>ids.has(id)));
 if(!$('proposalStatus').value&&!$('proposalKind').value){const query=$('proposalSearch').value.trim().toLowerCase();groups=centerRecords.filter(g=>(!scan||g.observations.some(o=>(o.scan_ids||[o.scan_id]).includes(scan)))&&(!query||JSON.stringify(g).toLowerCase().includes(query)));}
 if(scan)groups=groups.map(g=>({...g,observations:g.observations.filter(o=>(o.scan_ids||[o.scan_id]).includes(scan)).map(o=>({...o,review_targets:(o.review_targets||[]).filter(t=>t.scan_id===scan),sources:(o.sources||[]).filter(t=>!t.scan_id||t.scan_id===scan)}))}));
 const size=Number($('proposalPageSize').value);proposalPage=Math.min(proposalPage,Math.max(0,Math.ceil(groups.length/size)-1));
 const visible=groups.slice(proposalPage*size,(proposalPage+1)*size);
 pageRejectIds=filtered.filter(p=>reviewSelected.has(p.id)&&['pending','approved'].includes(p.status)).map(p=>p.id);
 $('rejectPage').disabled=bulkApproving||!pageRejectIds.length;$('rejectPage').textContent=`Reject selected (${pageRejectIds.length})`;
 $('proposalRows').innerHTML=groups.length?centerTable(visible,ids):empty('No centers in this view','Change filters or run a scan. All states also shows fetched details that did not create updates.');
 $('proposalPageLabel').textContent=groups.length?`${proposalPage*size+1}–${Math.min((proposalPage+1)*size,groups.length)} of ${groups.length} centers · ${filtered.length} matching updates`:'0 centers';$('proposalPrevious').disabled=proposalPage===0;$('proposalNext').disabled=(proposalPage+1)*size>=groups.length;
}
async function loadScanCenters(){const id=currentJob?.id;if(!id)return;const data=await api('/api/centers?scan_id='+encodeURIComponent(id));if(currentJob?.id!==id)return;scanCenters=data.centers;renderScanCenters();}
function renderScanCenters(){const size=Number($('scanCenterSize').value);const q=$('scanCenterSearch').value.trim().toLowerCase();const rows=scanCenters.filter(g=>JSON.stringify(g).toLowerCase().includes(q));scanCenterPage=Math.min(scanCenterPage,Math.max(0,Math.ceil(rows.length/size)-1));$('scanCenterRows').innerHTML=rows.length?centerTable(rows.slice(scanCenterPage*size,(scanCenterPage+1)*size)):empty('No center findings yet','Refresh after pages have been read.');$('scanCenterLabel').textContent=`${rows.length?scanCenterPage*size+1:0}–${Math.min((scanCenterPage+1)*size,rows.length)} of ${rows.length} centers`;$('scanCenterPrevious').disabled=!scanCenterPage;$('scanCenterNext').disabled=(scanCenterPage+1)*size>=rows.length;}
$('scanCenterRefresh').onclick=()=>loadScanCenters().catch(error);
$('scanCenterSearch').oninput=()=>{scanCenterPage=0;renderScanCenters()};$('scanCenterSize').onchange=()=>{scanCenterPage=0;renderScanCenters()};
$('scanCenterPrevious').onclick=()=>{scanCenterPage--;renderScanCenters()};$('scanCenterNext').onclick=()=>{scanCenterPage++;renderScanCenters()};

for(const id of ['proposalSearch','proposalKind','proposalStatus','proposalScan'])$(id).addEventListener(id==='proposalSearch'?'input':'change',()=>{proposalPage=0;renderProposals()});

$('proposalPrevious').onclick=()=>{proposalPage--;renderProposals();resetListScroll('proposals')};$('proposalNext').onclick=()=>{proposalPage++;renderProposals();resetListScroll('proposals')};

$('openUpdates').onclick=async()=>{try{await api('/api/scans/'+currentJob.id+'/enrichment',{method:'POST'});view('enrichment',{load:false});$('proposalSearch').value='';$('proposalKind').value='';$('proposalStatus').value='pending';await loadProposals();$('proposalScan').value=currentJob.id;proposalPage=0;renderProposals()}catch(err){error(err)}};

$('applyApproved').onclick=async()=>{if(bulkApproving)return;const ids=filteredProposals(true).filter(p=>p.status==='approved').slice(0,200).map(p=>p.id);if(!ids.length)return;$('applyApproved').disabled=true;try{const result=await api('/api/enrichment/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});const applied=result.outcomes.filter(o=>o.status==='applied').length;const blocked=result.outcomes.filter(o=>o.status==='blocked');$('applyNotice').textContent=`${applied} updates applied. ${blocked.length} blocked. ${blocked.map(o=>o.reason).join(' ')}`;$('applyNotice').hidden=false;await loadDirectory();await loadProposals()}catch(err){error(err);renderProposals()}};

async function loadChanges(){const changes=await api('/api/changes');const visible=paginateList(changes,'changes');$('changeRows').innerHTML=changes.length?visible.map(c=>`<div class="history-item"><div><strong>${escapeHTML(c.record_name||c.record_id)}: ${escapeHTML(c.field)}</strong><p>${escapeHTML(new Date(c.at).toLocaleString())} - ${c.undone?'Undone':'Applied'}</p><div class="evidence-grid"><div><strong>Before</strong><p>${escapeHTML(c.old_value||'Blank / not recorded')}</p></div><div class="${c.undone?'':escapeHTML(c.comparison_tone||'')}"><strong>After${c.undone?' (undone)':''}</strong><p>${escapeHTML(c.new_value||'')}</p></div></div></div><button class="secondary" data-undo="${c.id}" ${c.undone?'disabled':''}>Undo change</button></div>`).join(''):empty('No changes applied yet','Apply an update to see its history.');}

document.addEventListener('click',async event=>{const el=event.target.closest('button');if(!el)return;try{if(el.dataset.proposalReview){if(bulkApproving)return;el.disabled=true;await api('/api/enrichment/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:JSON.parse(el.dataset.proposalIds||JSON.stringify([el.dataset.proposalId])),status:el.dataset.proposalReview,replace_existing:el.dataset.replaceExisting==='true'})});await loadProposals()}if(el.dataset.undo){el.disabled=true;await api('/api/changes/'+el.dataset.undo+'/undo',{method:'POST'});await loadDirectory();await loadChanges();await updateProposalCount()}}catch(err){el.disabled=false;error(err)}});

function routeLine(record,type){const status=record.verification_status||'Earlier scan — not rechecked';return `<div class="route-line"><a href="${escapeHTML(record.source_url)}" title="${escapeHTML(record.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(record.source_url)}</a><button class="secondary" data-inspect-type="${type}" data-inspect-key="${escapeHTML(record.id)}">Source & route</button></div>${type==='result'||record.kind==='price_evidence'?`<div class="verification-note">${escapeHTML(status)}${record.extraction_issues?.length?' · Ambiguous extraction — excluded from automatic proposals':''}</div>`:''}`;}

const drawPrices=renderPrices;

renderPrices=function(){drawPrices();document.querySelectorAll('[data-result-key]').forEach(el=>{const r=currentJob.results.find(r=>r.id===el.dataset.resultKey);if(r)el.querySelector('.tags').insertAdjacentHTML('afterend',routeLine(r,'result'))})};

async function showEvidence(type,key){let record=type==='result'?currentJob?.results.find(r=>r.id===key):proposals.find(p=>p.id===key);if(!record)return;let route=record.crawl_route||[];let related=[];

if(type==='proposal'&&record.scan_id){const job=await api('/api/scans/'+record.scan_id);related=(job.results||[]).filter(r=>(record.source_result_ids||[]).includes(r.id));if(!route.length){route=related[0]?.crawl_route||job.pages.find(p=>p.url===record.source_url)?.crawl_route||[];}}

const src=record.source_url+(record.page?'#page='+record.page:'');

$('evidenceTitle').textContent=type==='result'?record.service:record.field;

$('evidenceBody').innerHTML=`<strong>Exact source</strong><p>${link(src,record.source_url)}</p><div class="inspect-actions"><button class="secondary" id="copySource">Copy source URL</button></div><p class="source-meta">Retrieved ${escapeHTML(record.retrieved_at?new Date(record.retrieved_at).toLocaleString():'See original scan')} ${record.page?' · PDF page '+record.page:''}</p><h2>Discovery route</h2>${route.length?`<ol class="route-list">${route.map(step=>`<li>${link(step.url,step.url)}<small>${escapeHTML(step.via)}</small></li>`).join('')}</ol>`:'<p>Route unavailable. Rescan to capture it.</p>'}<h2>Published evidence</h2><blockquote>${escapeHTML(record.evidence)}</blockquote><p>${escapeHTML(record.scope||record.context||'')}</p><h2>Checks & limitations</h2><p>${escapeHTML(record.verification_status||'Earlier scan — not rechecked')}</p><p>${escapeHTML(record.verification_note||'Source association and final fees still require review.')}</p>${(record.extraction_issues||[]).map(issue=>`<p class="verification-note">${escapeHTML(issue)}</p>`).join('')}${related.length>1?'<h2>All contributing observations</h2>'+related.map(r=>`<p>${link(r.source_url,r.service)} — ${escapeHTML(r.verification_status||'Not rechecked')}</p>`).join(''):''}<p class="source-meta">Source content SHA-256: ${escapeHTML(record.content_sha256||related[0]?.content_sha256||'Not retained by earlier scan')}</p><p class="helper">Repeat checks confirm source consistency, not facility scope or final fees.</p>`;

$('evidenceDialog').showModal();$('copySource').onclick=async()=>{try{await navigator.clipboard.writeText(src);$('copySource').textContent='Copied'}catch{$('copySource').textContent='Select the source link to copy'}};

}

document.addEventListener('click',event=>{const button=event.target.closest('[data-center-evidence]');if(!button)return;try{const data=JSON.parse(button.dataset.centerEvidence);const p=proposals.find(p=>p.id===data.proposal_id);$('evidenceTitle').textContent=data.field;$('evidenceBody').innerHTML=`<h2>Fetched value</h2><p>${escapeHTML(data.value||'')}</p><p class="source-meta">Scan ${escapeHTML(data.scan_id||'')}</p>`+data.sources.map(src=>`<p>${link(src.source_url)}</p>${src.crawl_route?.length?`<ol>${src.crawl_route.map(step=>`<li>${link(step.url,step.via||step.url)}</li>`).join('')}</ol>`:'<p>Route unavailable. Verify source.</p>'}`).join('')+`<blockquote>${escapeHTML(data.evidence)}</blockquote><p>${escapeHTML(data.scope)}</p>`+data.issues.map(issue=>`<p>${escapeHTML(issue)}</p>`).join('')+(p?matchingDetails(p):'');$('evidenceDialog').showModal();}catch(err){error(err)}});

document.addEventListener('click',async event=>{const button=event.target.closest('[data-observation-review],[data-rescan-center]');if(!button)return;button.disabled=true;try{
 if(button.dataset.rescanCenter){const job=await api('/api/scans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({record_ids:[button.dataset.rescanCenter],max_pages:null,max_depth:null})});await openJob(job.id);}
 else{const parsed=JSON.parse(button.dataset.observation);for(const target of (Array.isArray(parsed)?parsed:[parsed]))await api('/api/scans/'+target.scan_id+'/observations/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...target,review:button.dataset.observationReview})});if(currentView==='results')await loadScanCenters();else if(currentView==='enrichment')await loadProposals();else{const row=button.closest('.directory-center-expanded');if(row){const id=row.previousElementSibling.querySelector('[data-center-record]').dataset.centerRecord;const groups=(await api('/api/centers?record_id='+encodeURIComponent(id))).centers;row.querySelector('td').innerHTML=centerTable(groups);}}}
 }catch(err){error(err)}finally{button.disabled=false;}});

$('closeEvidence').onclick=()=>$('evidenceDialog').close();

$('closeRecord').onclick=event=>{event.preventDefault();$('recordDetail').hidden=true};

document.addEventListener('click',event=>{const button=event.target.closest('[data-inspect-key]');if(button)showEvidence(button.dataset.inspectType,button.dataset.inspectKey).catch(error)});

for(const action of ['pause','resume']){$(action+'Scan').onclick=async()=>{const id=currentJob.id;try{await api('/api/scans/'+id+'/'+action,{method:'POST'});await openJob(id)}catch(err){error(err)}}}

let runningTimer, runningGeneration=0, runningBusy=false;
function scanDuration(seconds){const total=Math.max(0,Math.floor(Number(seconds)||0));return [Math.floor(total/3600),Math.floor(total/60)%60,total%60].map(n=>String(n).padStart(2,'0')).join(':');}
function scanTimeMarkup(j){
 const measured=Number.isFinite(j.active_seconds);
 const active=j.status==='running';
 const base=measured?j.active_seconds:Math.max(0,((Date.parse(j.finished_at)||Date.now())-(Date.parse(j.created_at)||Date.now()))/1000);
 const since=active?(Date.parse(measured?j.timer_updated_at:null)||Date.now()):0;
 return `<span class="scan-clock">${measured?'Active scan time':'Elapsed time (includes pauses)'} <strong data-scan-seconds="${base}" data-scan-since="${since}">${scanDuration(base+(since?Math.max(0,Date.now()-since)/1000:0))}</strong>${j.status==='paused'?' · Paused':''}</span>`;
}
const concurrencyDrafts=new Map();
function readConcurrency(value){const n=Number(value);if(!String(value).trim()||!Number.isInteger(n)||n<1)throw new Error('Enter a whole number of centers, 1 or more.');return n;}
function concurrencyMarkup(j){return `<form class="concurrency-controls" data-concurrency-form="${escapeHTML(j.id)}"><label>Centers at a time<input type="number" min="1" step="1" required data-concurrency-input="${escapeHTML(j.id)}" value="${escapeHTML(concurrencyDrafts.get(j.id)??j.concurrency??4)}"></label><button type="submit" class="secondary">Update</button><small>Lowering this lets active centers finish.</small><span role="status" data-concurrency-status="${escapeHTML(j.id)}"></span></form>`;}
function scanSourcesMarkup(j){
 const workers=j.active_centers||[];
 if(!workers.length)return `<p>${j.current_url?link(j.current_url,j.current_url):j.status==='paused'?'Progress saved locally. Ready to resume.':'Preparing the next source...'}</p>`;
 return `<div class="scan-workers">${workers.map(w=>`<div class="scan-worker"><strong>${escapeHTML(w.name)}</strong><small>${escapeHTML(w.status)} · ${w.pages||0} pages · ${w.prices||0} prices</small><p>${w.current_url?link(w.current_url,w.current_url):j.status==='paused'?'Checkpoint saved':'Checking website routes...'}</p></div>`).join('')}</div>`;
}
setInterval(()=>{document.querySelectorAll('[data-scan-seconds]').forEach(el=>{if(el.closest('[hidden]'))return;const since=Number(el.dataset.scanSince);el.textContent=scanDuration(Number(el.dataset.scanSeconds)+(since?Math.max(0,Date.now()-since)/1000:0));});},1000);
async function loadRunning(){
    clearTimeout(runningTimer);
    const generation=++runningGeneration;
    try{
        const jobs=await api('/api/scans');
        const active=jobs.filter(j=>['running','queued','paused'].includes(j.status));
        const scans=await Promise.all(active.map(j=>api('/api/scans/'+j.id)));
        if(generation!==runningGeneration||currentView!=='running')return;
        const count=state=>scans.filter(j=>j.status===state).length;
        $('runningSummary').textContent=`${count('running')+count('queued')} active | ${count('paused')} paused | Updated ${new Date().toLocaleTimeString()}`;
        const expanded=new Set([...document.querySelectorAll('.running-card details[open]')].map(d=>d.closest('.running-card').dataset.scan));
        if(document.activeElement?.matches?.('[data-concurrency-input]'))return;
        $('runningRows').innerHTML=scans.length?scans.map(j=>{
            const active=['running','queued'].includes(j.status);
            const processed=j.pages.filter(p=>p.status!=='reading'&&p.status!=='discovery warning').length;
            const attention=j.pages.filter(p=>['error','blocked','partial','unsupported'].includes(p.status)).length;
            const label=j.cancel_requested?'Stopping after current page':j.pause_requested&&active?'Pausing after current page':j.status;
            return `<article class="running-card" data-scan="${j.id}"><div class="running-heading"><div><h2>${escapeHTML(j.hospitals.length===1?j.hospitals[0].name:j.hospitals.length+'-provider scan')}</h2><p>Started ${escapeHTML(new Date(j.created_at).toLocaleString())}</p></div><span class="scan-state ${active?'is-live':''}">${escapeHTML(label)}</span></div>${scanTimeMarkup(j)}<div class="scan-metrics">${Number.isFinite(j.completed_centers)?`<span><strong>${j.completed_centers} / ${j.hospitals.length}</strong> centers processed</span>`:''}<span><strong>${processed}</strong> pages processed</span><span><strong>${j.results.length}</strong> price findings</span><span><strong>${(j.contacts||[]).length}</strong> contact findings</span><span><strong>${attention}</strong> pages need attention</span></div><div class="current-source"><strong>${j.status==='paused'?'Saved checkpoints':j.concurrency>1?'Active centers (up to '+Number(j.concurrency||1)+')':'Current source'}</strong>${scanSourcesMarkup(j)}</div><details class="scan-providers" ${expanded.has(j.id)?'open':''}><summary>Providers in this scan (${j.hospitals.length})</summary><ul>${j.hospitals.map(h=>`<li>${escapeHTML(h.name)}</li>`).join('')}</ul></details>${concurrencyMarkup(j)}<div class="running-controls"><a class="button" href="#/scans/${j.id}" data-job="${j.id}">View findings</a>${pauseSupported?`<button class="secondary" data-running-action="pause" data-running-id="${j.id}" ${!active||j.pause_requested||j.cancel_requested||runningBusy?'disabled':''}>${j.pause_requested?'Pause requested':'Pause scan'}</button>`:''}${pauseSupported?`<button class="secondary" data-running-action="resume" data-running-id="${j.id}" ${j.status!=='paused'||runningBusy?'disabled':''}>Resume scan</button>`:''}<button class="danger-button" data-running-action="cancel" data-running-id="${j.id}" ${j.cancel_requested||runningBusy?'disabled':''}>Stop scan</button></div></article>`;
        }).join(''):empty('No scans running','Start a scan from the Directory or New scan. Completed scans remain in Scan history.');
    }catch(err){if(generation===runningGeneration&&currentView==='running'){$('runningSummary').textContent='Could not refresh. Retrying in 5 seconds.';error(err)}}
    finally{if(generation===runningGeneration&&currentView==='running')runningTimer=setTimeout(loadRunning,5000)}
}
$('refreshRunning').onclick=()=>loadRunning();
document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-running-action]');
    if(!button||runningBusy)return;
    runningBusy=true;button.disabled=true;
    try{await api('/api/scans/'+button.dataset.runningId+'/'+button.dataset.runningAction,{method:'POST'});runningBusy=false;if(currentView==='running')await loadRunning()}
    catch(err){error(err);button.disabled=false}
    finally{runningBusy=false}
});

const pageRoutes={dashboard:'dashboard',directory:'directory',scan:'new-scan',enrichment:'review-updates',changes:'change-history',running:'running-scans',history:'scan-history',guide:'help'};
const pageNames={dashboard:'Dashboard',directory:'Directory',scan:'New scan',enrichment:'Review updates',changes:'Change history',running:'Running scans',history:'Scan history',guide:'Help',results:'Scan findings'};
function setNavigationState(element,active){element.classList.toggle('active',active);if(active)element.setAttribute('aria-current','page');else element.removeAttribute('aria-current')}
function parsePageRoute(hash){
    const path=hash.replace(/^#\/?/,'').replace(/\/$/,'');
    if(!path)return {view:'dashboard'};
    const entry=Object.entries(pageRoutes).find(([,route])=>route===path);
    if(entry)return {view:entry[0]};
    const scan=path.match(/^scans\/([a-zA-Z0-9_-]+)$/);
    return scan?{view:'results',id:scan[1]}:null;
}
function updatePageRoute(name){
    const target=name==='results'&&currentJob?'#/scans/'+encodeURIComponent(currentJob.id):'#/'+(pageRoutes[name]||'directory');
    if(location.hash!==target){if(!location.hash)history.replaceState(null,'',target);else history.pushState(null,'',target)}
    document.title=(pageNames[name]||'Directory')+' | Clearprice';
    $('currentPageLabel').textContent=pageNames[name]||'Directory';
    requestAnimationFrame(()=>{if(currentView!==name)return;const heading=document.querySelector('#'+name+' h1');if(heading){heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true})}})
}
async function restorePageRoute(){
    const route=parsePageRoute(location.hash);
    if(!route){view('directory');error(new Error('Page not found. Choose a page from the navigation.'));return}
    try{if(route.view==='results')await openJob(route.id);else view(route.view)}
    catch(err){view('history');error(err)}
}
window.addEventListener('popstate',restorePageRoute);
window.addEventListener('hashchange',()=>{const route=parsePageRoute(location.hash);if(!route||route.view!==currentView||(route.id&&route.id!==currentJob?.id))restorePageRoute()});

let dashboardTimer, dashboardGeneration=0;
function dashboardStats(records,jobs,counts){
    const gaps=[['Address','Current supported address'],['Phone','Current supported call number'],['Email','Current usable email'],['MRI price','MRI self-pay (USD)'],['CT price','CT self-pay (USD)']].map(([label,field])=>({label,field,count:records.filter(r=>[...(r.missing_fields||[]),...(r.missing_price_fields||[])].includes(field)).length}));
    const contactGaps=records.filter(r=>(r.missing_fields||[]).length).length;
    return {total:records.length,gaps,contactGaps,contactsPresent:records.length-contactGaps,pending:counts.pending||0,applied:counts.applied||0,active:jobs.filter(j=>['queued','running'].includes(j.status)).length,paused:jobs.filter(j=>j.status==='paused').length};
}
function dashboardBar(value,total,tone='purple'){
    const width=total>0?Math.max(0,Math.min(100,value/total*100)):0;
    return `<svg class="dashboard-bar ${tone}" viewBox="0 0 100 7" preserveAspectRatio="none" aria-hidden="true"><rect width="100" height="7" rx="3.5" class="bar-track"/><rect width="${width}" height="7" rx="3.5" class="bar-value"/></svg>`;
}
async function loadDashboard(){
    clearTimeout(dashboardTimer);const generation=++dashboardGeneration;
    try{
        if($('dashboardBody').contains(document.activeElement))return;
        const [data,jobs,review]=await Promise.all([api('/api/directory'),api('/api/scans'),api('/api/enrichment')]);
        if(generation!==dashboardGeneration||currentView!=='dashboard'||$('dashboardBody').contains(document.activeElement))return;
        directory=data.records.map(r=>({...r,missing_fields:r.missing_fields||[]}));
        const stats=dashboardStats(directory,jobs,review.counts),totalReviews=Object.values(review.counts).reduce((a,b)=>a+b,0);
        $('navCount').textContent=stats.total.toLocaleString();$('proposalNav').textContent=stats.pending;
        $('dashboardStatus').textContent=`Updated ${new Date().toLocaleTimeString()} | Refreshes every 15 seconds | Stored and processed on this PC`;
        $('dashboardBody').innerHTML=`<div class="dashboard-overview"><a href="#/directory" data-view="directory" class="overview-item"><span>Directory records</span><strong>${stats.total.toLocaleString()}</strong><small>Open provider directory</small></a><a href="#/review-updates" data-dashboard-review="pending" class="overview-item"><span>Awaiting review</span><strong>${stats.pending.toLocaleString()}</strong><small>Check values and sources</small></a><a href="#/running-scans" data-view="running" class="overview-item"><span>Active scans</span><strong>${stats.active}</strong><small>${stats.paused} paused, ready to resume</small></a><a href="#/change-history" data-view="changes" class="overview-item"><span>Applied updates</span><strong>${stats.applied.toLocaleString()}</strong><small>Inspect changes or undo</small></a></div><div class="dashboard-grid"><article class="dashboard-panel"><h2>Where information is missing</h2><div class="gap-chart">${stats.gaps.map(g=>`<button class="gap-row" data-dashboard-gap="${escapeHTML(g.field)}"><span>${g.label}</span><strong>${g.count.toLocaleString()}</strong>${dashboardBar(g.count,stats.total)}</button>`).join('')}</div><p class="chart-note">Gaps can overlap. Missing prices are unknown.</p></article><article class="dashboard-panel"><h2>Contact coverage</h2><div class="coverage-total"><strong>${stats.total?Math.round(stats.contactsPresent/stats.total*100):0}%</strong><span>have all three contact fields</span></div>${dashboardBar(stats.contactsPresent,stats.total,'blue')}<div class="chart-key"><span>All three present <strong>${stats.contactsPresent.toLocaleString()}</strong></span><span>At least one missing <strong>${stats.contactGaps.toLocaleString()}</strong></span></div><button class="secondary" data-dashboard-gap="any">Find contact gaps</button></article><article class="dashboard-panel"><h2>Review queue</h2><div class="review-chart">${Object.entries(review.counts).map(([state,count])=>`<button class="gap-row" data-dashboard-review="${escapeHTML(state)}"><span>${escapeHTML(state.charAt(0).toUpperCase()+state.slice(1))}</span><strong>${count.toLocaleString()}</strong>${dashboardBar(count,totalReviews)}</button>`).join('')}</div></article><article class="dashboard-panel"><div class="panel-heading"><h2>Recent scan findings</h2><a href="#/scan-history" data-view="history">All scans</a></div><p>Latest five scans; may include repeated findings.</p><div class="scan-chart">${jobs.length?jobs.slice(0,5).map(j=>`<a class="scan-chart-row" href="#/scans/${j.id}" data-job="${j.id}"><span>${escapeHTML(j.hospitals.length===1?j.hospitals[0].name:j.hospitals.length+' providers')}<small>${escapeHTML(new Date(j.created_at).toLocaleDateString())} | ${escapeHTML(j.status)}</small></span><strong>${j.prices.toLocaleString()}</strong>${dashboardBar(j.prices,Math.max(1,...jobs.slice(0,5).map(x=>x.prices)))}</a>`).join(''):'<p>No scans yet. Start a new scan.</p>'}</div></article></div>`;
    }catch(err){if(generation===dashboardGeneration&&currentView==='dashboard'){$('dashboardStatus').textContent='Dashboard could not refresh. Retrying in 15 seconds.';error(err)}}
    finally{if(generation===dashboardGeneration&&currentView==='dashboard')dashboardTimer=setTimeout(loadDashboard,15000)}
}
$('refreshDashboard').onclick=loadDashboard;
document.addEventListener('click',async event=>{
    const gap=event.target.closest('[data-dashboard-gap]');
    if(gap){$('directorySearch').value='';$('categoryFilter').value='';$('gapFilter').value=gap.dataset.dashboardGap;page=0;renderDirectory();view('directory')}
    const review=event.target.closest('[data-dashboard-review]');
    if(review){if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();$('proposalSearch').value='';$('proposalKind').value='';$('proposalScan').value='';$('proposalStatus').value=review.dataset.dashboardReview;proposalPage=0;view('enrichment')}
});

const listPages={prices:{page:0,size:10},coverage:{page:0,size:10},history:{page:0,size:10},changes:{page:0,size:10}};
function pageWindow(total,page,size){const pages=Math.max(1,Math.ceil(total/size));page=Math.max(0,Math.min(page,pages-1));return {page,pages,start:page*size,end:Math.min(total,(page+1)*size)}}
function paginateList(items,key){
    const state=listPages[key];
    if(['prices','coverage'].includes(key)&&state.scan!==currentJob?.id){state.page=0;state.scan=currentJob?.id}
    const window=pageWindow(items.length,state.page,state.size);state.page=window.page;
    $(key+'Pager').innerHTML=`<span>${items.length?window.start+1:0}-${window.end} of ${items.length.toLocaleString()} | Page ${window.page+1} of ${window.pages}</span><div><label class="page-size">Per page<select data-list-size="${key}" aria-label="${key} items per page">${[10,20].map(n=>`<option value="${n}" ${state.size===n?'selected':''}>${n}</option>`).join('')}</select></label><button class="secondary" data-list-page="${key}" data-step="-1" ${window.page===0?'disabled':''}>Previous</button><button class="secondary" data-list-page="${key}" data-step="1" ${window.page===window.pages-1?'disabled':''}>Next</button></div>`;
    return items.slice(window.start,window.end);
}
function resetListScroll(key){const el=key==='directory'?document.querySelector('#directory .table-wrap'):key==='coverage'?document.querySelector('#coveragePanel .table-wrap'):$(key==='prices'?'priceRows':key==='history'?'historyRows':key==='proposals'?'proposalRows':'changeRows');if(el){el.scrollTop=0;el.scrollIntoView({block:'start',inline:'nearest'})}}
async function rerenderList(key){if(key==='prices')renderPrices();else if(key==='coverage')renderJob();else if(key==='history')await loadHistory();else await loadChanges();resetListScroll(key)}
document.addEventListener('click',async event=>{const button=event.target.closest('[data-list-page]');if(!button)return;const key=button.dataset.listPage;listPages[key].page+=Number(button.dataset.step);try{await rerenderList(key)}catch(err){error(err)}});
document.addEventListener('change',async event=>{const key=event.target.dataset.listSize;if(!key)return;listPages[key].size=Number(event.target.value);listPages[key].page=0;try{await rerenderList(key)}catch(err){error(err)}});
$('directoryPageSize').onchange=()=>{page=0;renderDirectory();resetListScroll('directory')};

$('proposalPageSize').onchange=()=>{proposalPage=0;renderProposals();resetListScroll('proposals')};
$('resetDirectoryFilters').onclick=()=>{for(const id of ['directorySearch','categoryFilter','gapFilter'])$(id).value='';page=0;renderDirectory()};
$('resetPriceFilters').onclick=()=>{for(const id of ['priceSearch','priceType','reviewFilter'])$(id).value='';listPages.prices.page=0;renderPrices()};
$('resetProposalFilters').onclick=()=>{for(const id of ['proposalSearch','proposalKind','proposalScan'])$(id).value='';$('proposalStatus').value='pending';proposalPage=0;renderProposals()};

let bulkApproving=false;
let pageRejectIds=[];
function rejectablePageIds(groups,items){const allowed=new Set(items.filter(p=>['pending','approved'].includes(p.status)).map(p=>p.id));return [...new Set(groups.flatMap(g=>g.proposal_ids).filter(id=>allowed.has(id)))];}
function bulkEligible(items){return items.filter(p=>p.status==='pending'&&!p.duplicate_candidates?.length&&['fill','contact_add','price_evidence','new_record'].includes(p.kind))}
async function approveProposalBatches(ids,onProgress=()=>{}){
    for(let offset=0;offset<ids.length;offset+=200){
        const batch=ids.slice(offset,offset+200);
        await api('/api/enrichment/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:batch,status:'approved'})});
        onProgress(offset+batch.length);
    }
}
$('approveAll').onclick=async()=>{
    if(bulkApproving)return;
    const ids=[...new Set(bulkEligible(filteredProposals().filter(p=>reviewSelected.has(p.id))).map(p=>p.id))];
    if(!ids.length)return;
    bulkApproving=true;let completed=0;
    const controls=[...document.querySelectorAll('#enrichment button,#enrichment input,#enrichment select')];
    const previous=controls.map(control=>control.disabled);controls.forEach(control=>control.disabled=true);
    $('approveAll').textContent=`Approving 0 of ${ids.length}...`;
    try{
        await approveProposalBatches(ids,count=>{completed=count;$('approveAll').textContent=`Approving ${count} of ${ids.length}...`});
        $('applyNotice').textContent=`Approved ${completed} updates. Review Approved, then Apply to save.`;
    }catch(err){
        $('applyNotice').textContent=`${completed} updates approved before interruption. Retry remaining pending updates. Directory unchanged.`;
        error(err);
    }finally{
        bulkApproving=false;controls.forEach((control,index)=>control.disabled=previous[index]);$('applyNotice').hidden=false;
        try{await loadProposals()}catch(err){error(err);renderProposals()}
    }
};

$('rejectPage').onclick=async()=>{
 if(bulkApproving||!pageRejectIds.length)return;
 const ids=[...pageRejectIds];bulkApproving=true;let completed=0;
 const controls=[...document.querySelectorAll('#enrichment button,#enrichment input,#enrichment select')];
 const previous=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);
 try{
  for(let i=0;i<ids.length;i+=200){const batch=ids.slice(i,i+200);await api('/api/enrichment/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:batch,status:'rejected'})});completed+=batch.length;}
  $('applyNotice').textContent=`Rejected ${completed} selected updates. Directory unchanged. Use Rejected → Reset to restore.`;
 }catch(err){$('applyNotice').textContent=`${completed} updates confirmed rejected before the request stopped. Refresh and retry the remaining updates.`;error(err);}
 finally{bulkApproving=false;controls.forEach((c,i)=>c.disabled=previous[i]);$('applyNotice').hidden=false;await loadProposals();}
};

$('selectAllUpdates').onclick=()=>{filteredProposals().filter(p=>['pending','approved'].includes(p.status)).forEach(p=>reviewSelected.add(p.id));renderProposals();};
$('clearUpdateSelection').onclick=()=>{reviewSelected.clear();renderProposals();};
$('proposalRows').addEventListener('change',event=>{
 const key=event.target.dataset.reviewCenter;if(!key)return;
 const group=centerRecords.find(g=>g.key===key);if(!group)return;
 const ids=new Set(filteredProposals().filter(p=>['pending','approved'].includes(p.status)).map(p=>p.id));
 group.proposal_ids.filter(id=>ids.has(id)).forEach(id=>event.target.checked?reviewSelected.add(id):reviewSelected.delete(id));renderProposals();
});

document.addEventListener('click',event=>{
 const button=event.target.closest('[data-review-existing]');if(!button)return;
 $('proposalSearch').value=button.dataset.reviewExisting;
 $('proposalKind').value='';$('proposalScan').value='';$('proposalStatus').value='pending';
 proposalPage=0;renderProposals();resetListScroll('enrichment');
});

document.addEventListener('input',event=>{const id=event.target.dataset?.concurrencyInput;if(id)concurrencyDrafts.set(id,event.target.value)});
document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-concurrency-form]');if(!form)return;event.preventDefault();
 const button=form.querySelector('button'),input=form.querySelector('input'),status=form.querySelector('[role="status"]');
 if(button.disabled)return;button.disabled=true;status.textContent='Saving…';
 try{const concurrency=readConcurrency(input.value);await api('/api/scans/'+form.dataset.concurrencyForm+'/concurrency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({concurrency})});concurrencyDrafts.delete(form.dataset.concurrencyForm);status.textContent='Saved: '+concurrency+' centers at a time.';}
 catch(err){status.textContent=err.message;error(err)}finally{button.disabled=false}
});
