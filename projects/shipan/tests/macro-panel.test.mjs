import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {mergeMacroResponse,macroPanelMarkup,createMacroReader,initMacroPanel} from '../public/macro-panel.mjs';
import {workspaceView} from '../public/workspace-views.mjs';

// Protocol fixtures only: these are not real market conclusions or source evidence.
const brief={reportDate:'2026-10-23',generatedAt:'2026-10-23T08:30:00+08:00',title:'测试报告',stance:'证据不足',summary:'仅用于界面测试',dimensions:[{name:'测试维度',signal:'待验证',evidence:'协议示例'}],watchpoints:['待验证项目'],risks:['示例缺口'],sources:[{title:'资料空间',url:'/macro-source.html',asOf:'2026-09'}],dataStatus:'partial',sourcePageUrl:'/macro-source.html',handbookUrl:'/macro-handbook.html'};
const response={ok:true,brief};
const now=()=>Date.parse('2026-10-23T09:00:00+08:00');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('one market tab contains all three previous sections, advice owns the council',async()=>{
  const market=workspaceView('overview'),advice=workspaceView('advice');
  for(const id of ['chart-panel','watch-panel','flow-panel'])assert.equal(market.visible[id],true);
  assert.equal(market.visible['investment-view'],false);
  assert.equal(advice.visible['investment-view'],true);
  for(const id of ['chart-panel','watch-panel','flow-panel','ticker-grid'])assert.equal(advice.visible[id],false);
  assert.equal(workspaceView('unknown').key,'overview');
  const html=await readFile(new URL('../public/index.html',import.meta.url),'utf8');
  assert.equal((html.match(/id="investment-view"/g)||[]).length,1);
  assert.equal((html.match(/id="council-open"/g)||[]).length,1);
  assert.ok(html.indexOf('id="investment-view"')<html.indexOf('</main>'));
  assert.ok(html.indexOf('id="macro-heading"')>html.indexOf('<aside class="watch-rail">'));
  assert.doesNotMatch(html,/data-view="(?:etf|flow)"/);
  assert.match(html,/id="advice-symbol"/);
});

test('failed or older reads preserve newest source report without changing its date',()=>{
  const saved=mergeMacroResponse(null,response);
  assert.deepEqual(saved.brief,brief);
  const prior={...brief,reportDate:'2026-10-22',generatedAt:'2026-10-22T08:30:00+08:00'};
  assert.equal(mergeMacroResponse(saved,{ok:true,brief:prior}).brief.reportDate,'2026-10-23');
  assert.equal(mergeMacroResponse(saved,{ok:true,brief:null}).brief.generatedAt,brief.generatedAt);
  assert.equal(mergeMacroResponse(saved,{ok:true,brief:{...brief,generatedAt:'2026-10-23T08:20:00+08:00'}}).brief.generatedAt,brief.generatedAt);
  assert.throws(()=>mergeMacroResponse(saved,{ok:false}),/格式/);
  assert.throws(()=>mergeMacroResponse(saved,{ok:true,brief:{summary:'missing date'}}),/日期/);
});

test('Shanghai midnight marks an unchanged report stale and preserves monthly source period',()=>{
  const today=macroPanelMarkup(response,{now:now()});
  assert.match(today,/今日结论/);assert.match(today,/2026-09/);assert.match(today,/资料部分覆盖/);
  const tomorrow=macroPanelMarkup(response,{now:Date.parse('2026-10-23T16:00:01Z')});
  assert.match(tomorrow,/历史结论 · 待今日更新/);assert.match(tomorrow,/报告 2026-10-23/);
  assert.doesNotMatch(tomorrow,/报告 2026-10-24/);
  assert.match(macroPanelMarkup(response,{now:Date.parse('2026-10-22T09:00:00+08:00')}),/报告日期异常/);
});

test('malicious text is escaped and unsupported source protocols remain text',()=>{
  const html=macroPanelMarkup({ok:true,brief:{...brief,title:'<img src=x onerror=alert(1)>',summary:'<script>alert(1)</script>',sources:[{title:'<bad>',url:'javascript:alert(1)'},{title:'手册',url:'library-file:example'}],handbookUrl:'data:text/html,hi'}},{now:now()});
  assert.doesNotMatch(html,/<script>|<img|href="javascript:|href="data:|href="library-file:/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;bad&gt;/);
});

test('missing content and failed updates show honest state, not a fabricated neutral brief',()=>{
  assert.match(macroPanelMarkup({ok:true,brief:null},{now:now()}),/宏观结论待更新/);
  const failed=macroPanelMarkup({...response,latestAttempt:{status:'failed',reportDate:brief.reportDate,message:'源采集失败'}},{now:now()});
  assert.match(failed,/本轮更新失败 · 保留结论/);assert.match(failed,/源采集失败/);assert.match(failed,/仅用于界面测试/);
});

test('independent reader deduplicates in-flight reads and retains content after failure',async()=>{
  let resolve,calls=0,fail=false;const updates=[];
  const reader=createMacroReader({now,request:()=>{calls++;return fail?Promise.reject(new Error('模拟读取失败')):new Promise(done=>{resolve=done;});},render:(data,status)=>updates.push({data,status})});
  const first=reader.refresh(),second=reader.refresh();assert.equal(calls,1);
  resolve(response);await Promise.all([first,second]);assert.equal(reader.getPayload().brief.reportDate,brief.reportDate);
  fail=true;await reader.refresh();assert.equal(calls,2);assert.equal(reader.getPayload().brief.summary,brief.summary);assert.equal(reader.getPayload().readError,'模拟读取失败');
  assert.equal(updates.at(-1).status.loading,false);
});

test('hard timeout releases reader even when request ignores cancellation; next read recovers',async()=>{
  let calls=0,late;const reader=createMacroReader({now,timeoutMs:15,render:()=>{},request:()=>++calls===1?new Promise(done=>{late=done;}):Promise.resolve(response)});
  await reader.refresh();assert.match(reader.getPayload().readError,/超时/);
  await reader.refresh();assert.equal(reader.getPayload().brief.generatedAt,brief.generatedAt);assert.equal(reader.getPayload().readError,null);
  late({ok:true,brief:{...brief,reportDate:'2026-10-22'}});await tick();assert.equal(reader.getPayload().brief.reportDate,'2026-10-23');
});

test('panel always reads same-origin macro, never a custom market gateway',async()=>{
  const elements={'macro-content':{innerHTML:''},'macro-refresh':{disabled:false,classList:{toggle(){}}}};let path,options;
  const reader=initMacroPanel({document:{getElementById:id=>elements[id]},fetch:async(p,o)=>{path=p;options=o;return {ok:true,json:async()=>response};}});
  await reader.refresh();assert.equal(path,'/api/macro');assert.equal(options.cache,'no-store');assert.ok(options.signal instanceof AbortSignal);assert.match(elements['macro-content'].innerHTML,/测试报告/);assert.equal(elements['macro-refresh'].disabled,false);
});

test('same-origin handbook paths work while protocol-relative and backslash redirects are rejected',()=>{
  const relative=macroPanelMarkup({ok:true,brief:{...brief,handbookUrl:'/macro-handbook.html'}},{now:now()});
  assert.match(relative,/href="\/macro-handbook\.html"/);
  for(const url of ['//external.example/handbook','/\\external.example/handbook','javascript:alert(1)']){
    const html=macroPanelMarkup({ok:true,brief:{...brief,handbookUrl:url}},{now:now()});
    assert.doesNotMatch(html,/href="(?:\/\/external|\/\\external|javascript:)/);
  }
});

test('long dimension evidence and watchpoints are expandable, report dates stay visible',()=>{
  const html=macroPanelMarkup(response,{now:now()});
  assert.match(html,/<details class="macro-dimension" data-macro-detail="dimension-0"><summary>/);
  assert.match(html,/<details class="macro-watch" data-macro-detail="watchpoints">/);
  assert.match(html,/报告 2026-10-23/);
  assert.match(html,/结论生成/);
});
