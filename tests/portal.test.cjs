// Exercise the actual portal request and scan-navigation functions without a browser.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const apiCode=source.slice(source.indexOf('async function api('),source.indexOf('\nfunction error('));
const jobCode=source.slice(source.indexOf('async function openJob('),source.indexOf('function renderJob()'));

test('plain text server errors remain readable',async()=>{
 const context=vm.createContext({fetch:async()=>new Response('Service unavailable',{status:503}),token:'t'});
 vm.runInContext(apiCode,context);
 await assert.rejects(context.api('/test'),/Service unavailable/);
});

test('expired app token refreshes once before retrying a rejected mutation',async()=>{
 const calls=[];const context=vm.createContext({token:'old',fetch:async(path,options)=>{
  calls.push([path,options?.headers?.['x-app-token']]);
  if(path==='/api/session')return new Response(JSON.stringify({token:'new'}));
  return options.headers['x-app-token']==='new'?new Response('{"ok":true}'):new Response('Refresh',{status:403});
 }});vm.runInContext(apiCode,context);
 assert.equal((await context.api('/save',{method:'POST'})).ok,true);
 assert.deepEqual(calls,[['/save','old'],['/api/session',undefined],['/save','new']]);
});

test('a slower old scan response cannot replace the newly selected scan',async()=>{
 const pending={};const fields=new Map();const context=vm.createContext({currentJob:null,currentView:'history',jobGeneration:0,pollTimer:null,clearTimeout(){},setTimeout(){},api:path=>new Promise(resolve=>pending[path]=resolve),view(name){context.currentView=name},renderJob(){},document:{querySelectorAll:()=>[]},$:id=>{if(!fields.has(id))fields.set(id,{});return fields.get(id)}});
 vm.runInContext(jobCode,context);
 const old=context.openJob('old'),fresh=context.openJob('fresh');
 pending['/api/scans/fresh']({id:'fresh',status:'completed'});await fresh;
 pending['/api/scans/old']({id:'old',status:'completed'});await old;
 assert.equal(context.currentJob.id,'fresh');
});

test('in-flight poll cannot overwrite a different scan',async()=>{
 let callback,resolve;const context=vm.createContext({currentJob:{id:'old',status:'running'},currentView:'results',jobGeneration:1,pollTimer:null,clearTimeout(){},setTimeout(fn){callback=fn},api:()=>new Promise(r=>resolve=r),renderJob(){throw Error('stale render')},error(){}});
 vm.runInContext(jobCode,context);context.schedulePoll();const work=callback();
 context.currentJob={id:'new',status:'completed'};context.jobGeneration=2;resolve({id:'old',status:'running'});await work;
 assert.equal(context.currentJob.id,'new');
});
