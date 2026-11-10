const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../herdr-app/main.js'), 'utf8')
  .replace(/^import .*;\n/gm, '').replace('export default class ClaudeAccounts', 'class ClaudeAccounts');
let reply, requests = [], jobs = [], runs = [];
const context = {
  View: class {}, home: () => '/home/test',
  exec: async (...args) => { requests.push(args); return reply; },
  focused: () => ({space: 'local'}), spaces: () => [{id:'local', machine:null}],
  closeOverlay: () => {}, props: () => ({overlay: 'test'}),
  run: (...args) => runs.push(args),
  InputState: {new: () => ({value: () => 'New account', on() {}, focus() {}})},
};
vm.createContext(context);
vm.runInContext(code + '\nglobalThis.Accounts = ClaudeAccounts;', context);
const cx = { notify() {}, spawn(fn) { jobs.push(fn(cx)); }, focus_handle() { return {focus() {}}; }, timer: {every() {}} };
// Herdr click contexts expire when the callback returns; spawned contexts survive await.
function click(action) {
  let valid = true;
  const eventCx = {...cx, notify() { assert.ok(valid, 'cx is no longer valid'); }};
  try { action(eventCx); } finally { valid = false; }
}
function element() {
  const node = {items: []};
  const builder = new Proxy(node, {get(target, key) {
    if (key in target) return target[key];
    return (...args) => {
      if (key === 'child') node.items.push(args[0]);
      else if (key === 'children') node.items.push(...args[0]);
      else if (key === 'when') { if (args[0]) args[1](builder); }
      else if (key === 'id') node.name = args[0];
      else if (key === 'on_click') node.click = args[0];
      return builder;
    };
  }});
  return builder;
}
function find(node, id) {
  if (!node || typeof node !== 'object') return null;
  return node.name === id ? node : node.items?.map(n => find(n,id)).find(Boolean) || null;
}
(async () => {
  const status = {accounts:[{id:'work',label:'Work'}], selected:'work',
    system:{id:null,label:'System default',auth:{state:'saved',email:'system@example.com'}}};
  reply = {code:0, stdout:JSON.stringify(status)};
  const ui = new context.Accounts();
  ui.init({}, cx); await Promise.all(jobs);
  assert.equal(ui.rows.length, 1); assert.equal(ui.selected, 'work');
  assert.match(ui.describe(ui.system), /system@example.com/);
  assert.equal(ui.usageColor(69.9),'#2563eb');
  assert.equal(ui.usageColor(70),'#eab308');
  assert.equal(ui.usageColor(80),'#f97316');
  assert.equal(ui.usageColor(90),'#dc2626');
  const stamp=new Date(2026,10,10,8,9).getTime()/1000;
  assert.equal(ui.stamp(stamp),'08:09 10.11.2026');
  assert.equal(ui.usageLabel('Неделя · Fable'), 'Weekly · Fable');
  assert.equal(ui.usageLabel('5 часов'), '5 hours');
  assert.equal(ui.usageLabel('Weekly · Fable'), 'Weekly · Fable');
  reply = {code:2, stderr:'Settings conflict'};
  ui.choose(null,cx); await Promise.all(jobs);
  assert.equal(ui.selected,'work'); assert.match(ui.error,/Settings conflict/);
  reply = {code:0, stdout:'{"selected":null}'};
  ui.choose(null,cx); await Promise.all(jobs);
  assert.equal(ui.selected,null); assert.equal(ui.error,'');
  assert.equal(requests.at(-1)[1].join(','),'select,system');
  ui.busy = true; const count=requests.length;
  ui.choose('work',cx); assert.equal(requests.length,count);
  ui.openClaude('work'); assert.equal(runs.at(-1)[0],'tab.new');
  assert.equal(runs.at(-1)[1].space,'local');
  assert.equal(runs.at(-1)[1].argv.slice(-2).join(','), 'launch,work');
  context.spaces=()=>[{id:'local',machine:'remote'}];
  ui.openClaude(null); assert.equal(runs.at(-1)[0],'space.new');
  assert.equal(runs.at(-1)[1].machine,'');
  assert.equal(runs.at(-1)[1].argv.at(-1),'system');
  ui.busy=false; ui.at=1; ui.selected='work';
  ui.requestRemove(cx);
  assert.equal(ui.pendingRemoval.id,'work');
  reply={code:2,stderr:'Could not save'};
  ui.remove(cx); await Promise.all(jobs);
  assert.equal(ui.selected,'work'); assert.equal(ui.rows.length,1);
  assert.equal(ui.pendingRemoval.id,'work');
  reply={code:0,stdout:'{"selected":null}'};
  ui.remove(cx); await Promise.all(jobs);
  assert.equal(ui.selected,null); assert.equal(ui.rows.length,0);
  assert.equal(ui.pendingRemoval,null);
  ui.requestRemove(cx); assert.equal(ui.pendingRemoval,null);
  context.exec = async (_path, args) => {
    requests.push([_path,args]);
    if (args[0] === 'add') return {code:0,stdout:'{"id":"new","label":"New account"}'};
    if (args[0] === 'prepare-login') return {code:0,stdout:'{"attempt":"ticket"}'};
    if (args[0] === 'login') return {code:2,stderr:'Browser sign-in timed out; profile retained'};
    return {code:0,stdout:JSON.stringify({...status,accounts:[{id:'new',label:'New account'}]})};
  };
  ui.adding=true;
  click(cx => ui.add(cx)); await Promise.all(jobs);
  assert.match(ui.error,/timed out/);
  assert.equal(ui.rows[0].id,'new');
  assert.equal(ui.adding,false);
  assert.ok(requests.some(r => r[1].join(',')==='login,new,--browser,--attempt,ticket'));
  context.exec = async (_path,args) => {
    requests.push([_path,args]);
    return {code:0,stdout:JSON.stringify(args[0] === 'status' ? status : {})};
  };
  ui.restore('old',cx); await Promise.all(jobs);
  assert.ok(requests.some(r=>r[1].join(',')==='restore,old'));
  ui.removed=[{account:{id:'old',label:'Old'}}];
  context.exec = async (_path,args) => args[0] === 'restore' ?
    {code:2,stderr:'Profile restored, but settings locked'} : {code:0,stdout:JSON.stringify(status)};
  ui.restore('old',cx); await Promise.all(jobs);
  assert.equal(ui.removed,null);
  assert.equal(ui.rows[0].id,'work');
  assert.match(ui.error,/restored/);
  let finishLogin;
  context.exec = async (_path,args) => {
    requests.push([_path,args]);
    if(args[0]==='prepare-login') return {code:0,stdout:'{"attempt":"cancel-ticket"}'};
    if(args[0]==='login') return await new Promise(resolve=>{finishLogin=resolve;});
    if(args[0]==='cancel-login') {
      finishLogin({code:0,stdout:'{"state":"cancelled"}'});
      return {code:0,stdout:'{"cancelled":true}'};
    }
    return {code:0,stdout:JSON.stringify(status)};
  };
  click(cx => ui.login('work',cx));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ui.loginAttempt,'cancel-ticket');
  assert.equal(ui.busy,true);
  click(cx => ui.cancelLogin(cx)); await Promise.all(jobs);
  assert.equal(ui.busy,false);
  assert.equal(ui.loginAttempt,null);
  assert.match(ui.message,/cancelled/);
  assert.equal(ui.selected,'work');
  click(cx => ui.login('work',cx));
  await new Promise(resolve=>setImmediate(resolve));
  const beforeClose=requests.length;
  await ui.close(); await Promise.all(jobs);
  assert.ok(requests.slice(beforeClose).some(r=>r[1][0]==='cancel-login'));
  assert.ok(!requests.slice(beforeClose).some(r=>r[1][0]==='status'));
  assert.equal(ui.loginAttempt,null);
  assert.match(ui.usageSummary({usage:{state:'unavailable',reason:'timeout',windows:[]}}),/unavailable/);
  assert.match(ui.usageSummary({usage:{state:'available',checked_at:1,windows:[{resets_at:2}]}}),/out of date/);
  ui.closed=false;
  ui.rows=[{id:'work',label:'Work',sessions:{available:true,sessions:[{id:'one',state:'idle',cwd:'/project'}]}}];
  ui.system={id:null,label:'System default',sessions:{available:true,sessions:[]}};
  ui.selected=null;
  context.div=context.v_flex=context.h_flex=element;
  context.theme=()=>({});
  let tree=ui.render();
  assert.equal(find(tree,'accounts-list').items.map(n=>n.name).join(','),'system,work');
  ui.rows.push({id:'later',label:'Added later'});
  assert.equal(find(ui.render(),'accounts-list').items.map(n=>n.name).join(','),'system,work,later');
  assert.equal(find(tree,'session-list-work'),null);
  let stopped=false;
  const clickCx={...cx,stop_propagation(){stopped=true;}};
  find(tree,'sessions-work').click({},clickCx);
  assert.equal(stopped,true);
  assert.equal(ui.selected,null);
  tree=ui.render();
  assert.ok(find(find(tree,'work'),'session-list-work'));
  find(tree,'sessions-work').click({},clickCx);
  assert.equal(find(ui.render(),'session-list-work'),null);
  const fresh={state:'available',checked_at:2000,windows:[{label:'Weekly',used_percent:10,resets_at:null}]};
  context.exec=async (_path,args)=>{
    requests.push([_path,args]);
    return {code:0,stdout:JSON.stringify(fresh)};
  };
  const beforeRefresh=requests.length;
  stopped=false;
  find(ui.render(),'refresh-usage-work').click({},clickCx);
  await Promise.all(jobs);
  assert.equal(stopped,true);
  assert.equal(requests.length,beforeRefresh+1);
  assert.equal(requests.at(-1)[1].join(','),'refresh-usage,work');
  assert.equal(ui.rows[0].usage.windows[0].used_percent,10);
  assert.equal(ui.system.usage,undefined);
  assert.equal(ui.selected,null);
  find(ui.render(),'refresh-usage-system').click({},clickCx);
  await Promise.all(jobs);
  assert.equal(requests.at(-1)[1].join(','),'refresh-usage,system');
  const compact = new context.Accounts();
  reply={code:0,stdout:JSON.stringify(status)};
  context.exec = async (...args) => { requests.push(args); return reply; };
  compact.init({compact:true},cx); await Promise.all(jobs);
  assert.equal(requests.at(-1)[1][0],'list');
  // Reloads track the highlighted account, not its old row number.
  ui.rows=[{id:'first',label:'First'},{id:'work',label:'Work'},{id:'last',label:'Last'}];
  ui.at=2;
  reply={code:0,stdout:JSON.stringify({...status,accounts:[...status.accounts,{id:'last',label:'Last'}]})};
  await ui.reload();
  assert.equal(ui.rows[ui.at-1].id,'work', 'removing an earlier row must retain highlight');

  // A delayed poll must leave inputs usable and never undo a completed mutation.
  function deferred() {
    let resolve;
    const promise=new Promise(done=>{resolve=done;});
    return {promise,resolve};
  }
  const ok=value=>({code:0,stdout:JSON.stringify(value)});
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  for (const mutation of ['select','select-with-poll-error','remove','refresh-usage','login','check']) {
    const poll=deferred();
    ui.rows=[{id:'work',label:'Work',usage:{state:'available',checked_at:1,windows:[]}}];
    ui.selected='work'; ui.at=1; ui.pendingRemoval=null; ui.pendingCheck=null;
    ui.message='Ready'; ui.error='';
    let polls=0;
    context.exec=async (_path,args)=>{
      if(args[0]==='status' && args.includes('--usage') && ++polls===1) return poll.promise;
      if(args[0]==='refresh-usage') return ok(fresh);
      if(args[0]==='prepare-login') return ok({attempt:'race-ticket'});
      if(args[0]==='login') return ok({state:'completed'});
      if(args[0]==='status') return ok({...status,accounts:[{id:'work',label:'Updated',usage:fresh}]});
      return ok({});
    };
    const start=jobs.length;
    ui.refresh(cx);
    assert.equal(ui.busy,false, 'background refresh must not lock actions');
    ui.refresh(cx);
    assert.equal(polls,1,'only one poll may be in flight');
    if(mutation.startsWith('select')) ui.choose(null,cx);
    if(mutation==='remove') {ui.requestRemove(cx); ui.remove(cx);}
    if(mutation==='refresh-usage') ui.refreshUsage(ui.rows[0],cx);
    if(mutation==='login') ui.login('work',cx);
    if(mutation==='check') {ui.pendingCheck=ui.rows[0]; ui.check(cx);}
    await tick();
    const message=ui.message;
    poll.resolve(mutation==='select-with-poll-error' ? {code:2,stderr:'Old poll failed'} : ok(status));
    await Promise.all(jobs.slice(start));
    assert.equal(ui.message,message, `${mutation}: stale poll must not replace action result`);
    assert.equal(ui.error,'', `${mutation}: stale poll error must not replace action result`);
    if(mutation.startsWith('select') || mutation==='remove') assert.equal(ui.selected,null);
    if(mutation==='remove') assert.equal(ui.rows.length,0,'poll must not resurrect removed row');
    if(mutation==='refresh-usage') assert.equal(ui.rows[0].usage.checked_at,2000);
    if(mutation==='login' || mutation==='check') assert.equal(ui.rows[0].label,'Updated');
  }

  // Fast list metadata renders before slow auth/usage, without erasing session counts.
  const delayedStatus=deferred();
  const initialCalls=[];
  context.exec=async (_path,args)=>{
    initialCalls.push(args.join(','));
    return args[0]==='list' ? ok({accounts:[{id:'work',label:'Work'}],selected:'work'}) : delayedStatus.promise;
  };
  const loading=new context.Accounts();
  const loadStart=jobs.length;
  loading.init({},cx);
  await tick();
  assert.equal(initialCalls[0],'list');
  assert.equal(loading.rows[0].id,'work');
  assert.equal(loading.busy,false);
  assert.equal(initialCalls[1],'status,--usage');
  delayedStatus.resolve(ok({...status,accounts:[{id:'work',label:'Work',sessions:{available:true,sessions:[{id:'one'}]},auth:{state:'saved'},usage:fresh}]}));
  await Promise.all(jobs.slice(loadStart));
  const displayedAccount=loading.rows[0];
  loading.compact=true;
  await loading.reload();
  assert.equal(loading.rows[0],displayedAccount,'refresh must preserve the card used by its action callbacks');
  assert.equal(loading.rows[0].sessions.sessions.length,1,'metadata refresh must preserve session count');
  assert.equal(loading.rows[0].auth.state,'saved');
  assert.equal(loading.rows[0].usage.checked_at,2000);
  assert.equal(loading.system.auth.email,'system@example.com');

  // Persisted add warnings are not failures that can be retried as duplicate creates.
  const warningCalls=[];
  context.exec=async (_path,args)=>{
    warningCalls.push(args[0]);
    if(args[0]==='add') return ok({id:'new',label:'New account',warning:'Profile saved, but settings sync failed'});
    return ok({...status,accounts:[...status.accounts,{id:'new',label:'New account'}]});
  };
  ui.adding=true; ui.pendingCheck=null;
  click(cx=>ui.add(cx)); await Promise.all(jobs);
  assert.equal(ui.adding,false);
  assert.equal(ui.rows[ui.at-1].id,'new');
  assert.match(ui.message,/Profile saved, but settings sync failed/);
  assert.equal(warningCalls.includes('prepare-login'),false);
  assert.equal(warningCalls.includes('login'),false);

  // Async completion after closing must not notify a dead owner.
  const closingPoll=deferred();
  context.exec=async()=>closingPoll.promise;
  let closedNotifies=0;
  const closedCx={...cx,notify(){if(ui.closed) closedNotifies++;},spawn(fn){jobs.push(fn(closedCx));}};
  const closeStart=jobs.length;
  ui.refresh(closedCx);
  await ui.close();
  closingPoll.resolve(ok(status));
  await Promise.all(jobs.slice(closeStart));
  assert.equal(closedNotifies,0);
  const afterClose=jobs.length;
  ui.refresh(closedCx);
  assert.equal(jobs.length,afterClose,'closed view must not schedule another poll');
  console.log('Herdr app: selection, responsive polling, stale response guards, sessions, partial add and close pass');
})().catch(e => { console.error(e); process.exit(1); });
