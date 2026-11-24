const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/app.js','utf8');
const helpers=source.slice(source.indexOf('function proposalActions('),source.indexOf('function renderProposals('));
function context(){const c=vm.createContext({proposals:[],escapeHTML:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),badge:s=>s,link:()=>'<a>Source</a>',matchingDetails:()=>''});vm.runInContext(helpers,c);return c;}
test('one center row contains current and fetched fields without injecting HTML',()=>{const c=context();const g={name:'<script>bad</script>',record_id:'A',city:'',state:'',current:{'Current usable email':'old@example.com'},observations:[{field:'Current usable email',value:'new@example.com',old_value:'old@example.com',label:'Old → Different',status:'pending',scan_id:'scan',source_url:'https://example.com',crawl_route:[],issues:[]}]};const html=c.centerTable([g]);assert.ok(html.includes('old@example.com')&&html.includes('new@example.com')&&html.includes('Mismatch — review required'));assert.ok(!html.includes('<script>'));assert.equal((html.match(/<tr>/g)||[]).length,3);});
test('context-only findings never get approval controls',()=>{const c=context();const g={current:{},observations:[{field:'Phone',value:'123',old_value:'',label:'Blank → New',status:'observed',scan_id:'s',source_url:'',crawl_route:[],issues:[]}]};assert.ok(!c.centerCell(g,['Phone'],null).includes('data-proposal-review'));});

test('every field has Old/New headers and empty new values are explicit',()=>{const c=context();const g={name:'Center',record_id:'A',city:'',state:'',current:{'Current supported address':'12 Main St'},observations:[]};const html=c.centerTable([g]);assert.equal((html.match(/class="old-heading"/g)||[]).length,5);assert.equal((html.match(/class="new-heading"/g)||[]).length,5);assert.ok(html.includes('12 Main St'));assert.equal((html.match(/No new value/g)||[]).length,5);assert.ok(!html.includes('<details'));});
test('Old keeps pre-application value instead of showing the new current value',()=>{const c=context();const g={current:{Email:'new@example.com'},observations:[{field:'Email',old_value:'old@example.com',value:'new@example.com'}]};assert.ok(c.centerOldCell(g,['Email']).includes('old@example.com'));assert.ok(!c.centerOldCell(g,['Email']).includes('new@example.com'));});

test('compact prices omit empty metadata and retain scope and qualifications',()=>{const c=context();const value=c.compactPriceValue('MRI brain: USD 499 (starting at; without contrast; code not stated)');assert.ok(value.includes('MRI brain')&&value.includes('499')&&value.includes('starting at')&&value.includes('without contrast'));assert.ok(!value.includes('code not stated'));});
test('narrative labels are not rewritten into invented exam names',()=>{const c=context();const value=c.compactPriceValue('Running the real numbers on paying cash for an MRI versus using insurance: USD 399 (listed; not specified; code not stated)');assert.ok(value.includes('Exam unclear')&&value.includes('399'));assert.ok(!value.includes('Running the real numbers'));});
test('raw findings have evidence review and rejection without directory approval',()=>{const c=context();const html=c.observationActions({review_target:{scan_id:'s',collection:'contacts',observation_id:'abc'},status:'unreviewed',issues:[]},null,false);assert.ok(html.includes('Approve evidence')&&html.includes('Reject')&&html.includes('directory unchanged'));assert.ok(!html.includes('data-proposal-review'));});
test('conflicting field has an explicit replacement action',()=>{const c=context();const html=c.proposalActions({id:'p',kind:'conflict',status:'pending'});assert.ok(html.includes('Approve replacement')&&html.includes('data-replace-existing="true"')&&html.includes('Reject'));});

test('review center checkbox shows selected group',()=>{const c=context();c.reviewSelected=new Set(['p']);c.proposals=[{id:'p',status:'pending',kind:'fill'}];const html=c.centerTable([{key:'record:A',name:'Center',record_id:'A',current:{},observations:[],proposal_ids:['p']}],new Set(['p']));assert.ok(html.includes('data-review-center="record:A"'));assert.ok(html.includes('checked'));});

test('duplicate location links to existing record without enabling new-location approval',()=>{
 const c=context();const html=c.proposalActions({id:'p',kind:'new_record',status:'pending',duplicate_candidates:[{id:'D-18',name:'Reno'}]});
 assert.ok(html.includes('data-review-existing="D-18"'));
 assert.ok(html.includes('disabled>Approve location'));
 const fill=c.proposalActions({id:'address',kind:'fill',status:'pending',record_id:'D-18'});
 assert.ok(!fill.includes('disabled>Approve'));
});

test('existing values are checked without approval controls',()=>{
 const c=context();const html=c.centerCell({observations:[{field:'Phone',value:'123',old_value:'123',label:'Unchanged',status:'unreviewed'}]},['Phone']);
 assert.ok(html.includes('Already present')&&html.includes('No update needed'));
 assert.ok(!html.includes('Approve'));
});
