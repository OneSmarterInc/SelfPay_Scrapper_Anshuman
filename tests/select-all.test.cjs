const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
test('directory select all covers filtered records beyond one page and skips missing URLs',()=>{
const nodes={directorySearch:{value:'Center'},categoryFilter:{value:''},gapFilter:{value:''}};
const c=vm.createContext({directory:Array.from({length:45},(_,i)=>({id:String(i),name:'Center '+i,urls:i===0?[]:['https://example.com'],missing_fields:[],state:'',city:''})),selected:new Set(),$:id=>nodes[id]||(nodes[id]={}),renderDirectory:()=>{}});
vm.runInContext(source.slice(source.indexOf('function filteredDirectory(){'),source.indexOf('function renderDirectory(){')),c);
const line=source.split('\n').find(line=>line.startsWith("$('selectAllRecords').onclick="));vm.runInContext(line,c);
nodes.selectAllRecords.onclick();assert.equal(c.selected.size,44);assert.ok(c.selected.has('44'));assert.ok(!c.selected.has('0'));
});
test('review select all covers pending and approved across pages only',()=>{
const nodes={};const c=vm.createContext({reviewSelected:new Set(),filteredProposals:()=>[{id:'p',status:'pending'},{id:'a',status:'approved'},{id:'x',status:'applied'}],$:id=>nodes[id]||(nodes[id]={}),renderProposals:()=>{}});
vm.runInContext(source.split('\n').find(line=>line.startsWith("$('selectAllUpdates').onclick=")),c);nodes.selectAllUpdates.onclick();assert.equal([...c.reviewSelected].join(','),'p,a');
});
