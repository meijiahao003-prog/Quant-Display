import test from 'node:test';
import assert from 'node:assert/strict';
import {createMarketDataClient} from '../lib/market-data.mjs';
import {createFundFlowClient} from '../lib/fund-flow.mjs';
import {createIntradayFlowClient} from '../lib/intraday-flow.mjs';

// Explicit synthetic protocol fixtures, never recorded market observations.
function response(kind,day='2026-10-23',clock='09:32'){
  const time=`${day}T${clock}:00+08:00`;
  if(kind==='quotes'){
    const fields=Array(38).fill('');
    Object.assign(fields,{1:'Synthetic ETF',2:'510300',3:'4.2',4:'4.1',5:'4.1',6:'100',30:day.replaceAll('-','')+clock.replace(':','')+'00',32:'2.4',33:'4.3',34:'4',37:'10'});
    return new Response(`v_sh510300="${fields.join('~')}";`);
  }
  if(kind==='prices')return Response.json({code:0,data:{sh510300:{data:{date:day.replaceAll('-',''),data:[`${clock.replace(':','')} 4.2 100 420`]}}}});
  if(kind==='funds')return Response.json({rc:0,data:{diff:[{f12:'510300',f13:1,f62:100,f66:60,f72:40,f78:-20,f84:-80,f124:Date.parse(time)/1000,f297:Number(day.replaceAll('-',''))}]}});
  return Response.json({rc:0,data:{code:'510300',market:1,name:'Synthetic ETF',klines:[`${day} ${clock},100,-80,-20,40,60`]}});
}
function reader(kind,fetchImpl,now,timeoutMs=200){
  if(kind==='quotes'||kind==='prices'){
    const client=createMarketDataClient({fetchImpl,now,timeoutMs});
    return ()=>kind==='quotes'?client.getSnapshot():client.getHistory('510300','etf','1m');
  }
  if(kind==='funds'){
    const client=createFundFlowClient({fetchImpl,now,timeoutMs});return ()=>client.getFundFlow();
  }
  const client=createIntradayFlowClient({fetchImpl,now,timeoutMs});return ()=>client.getIntradayFlow('510300','etf');
}

for(const kind of ['quotes','prices','funds','minutes']){
  test(`${kind}: a new request completes while an earlier owner's source promise remains unresolved`,{timeout:1000},async()=>{
    let calls=0,release;
    const read=reader(kind,async()=>++calls===1?new Promise(resolve=>release=resolve):response(kind),()=>Date.parse('2026-10-23T09:33:00+08:00'));
    const abandoned=read();
    const current=await read();
    assert.equal(calls,2);assert.equal(current.source_date,'2026-10-23');assert.ok(['live','partial'].includes(current.status));
    release(new Response(null,{status:400}));
    const oldResult=await abandoned;
    assert.equal(oldResult.as_of,current.as_of);assert.equal(oldResult.status,current.status);
    const cached=await read();assert.equal(cached.as_of,current.as_of);assert.equal(cached.status,current.status);assert.equal(calls,2);
  });

  test(`${kind}: an old-day read completing after midnight does not satisfy the new day's cache`,{timeout:1000},async()=>{
    let stamp=Date.parse('2026-10-22T23:59:59+08:00'),calls=0,release;
    const read=reader(kind,async()=>++calls===1?new Promise(resolve=>release=resolve):response(kind),()=>stamp);
    const crossing=read();stamp+=2000;release(response(kind,'2026-10-22','15:00'));
    const old=await crossing;assert.equal(old.status,'stale');assert.equal(old.source_date,'2026-10-22');
    const fresh=await read();assert.equal(calls,2);assert.equal(fresh.source_date,'2026-10-23');
    assert.equal(fresh.freshness,'current_day');assert.ok(['live','partial'].includes(fresh.status));
  });

  test(`${kind}: yesterday's delayed success cannot overwrite today's completed source read`,{timeout:1000},async()=>{
    let stamp=Date.parse('2026-10-22T23:59:59+08:00'),calls=0,release;
    const read=reader(kind,async()=>++calls===1?new Promise(resolve=>release=resolve):response(kind),()=>stamp);
    const old=read();stamp=Date.parse('2026-10-23T09:33:00+08:00');
    const fresh=await read();release(response(kind,'2026-10-22','15:00'));await old;
    const cached=await read();assert.equal(cached.as_of,fresh.as_of);assert.equal(cached.fetched_at,fresh.fetched_at);
    assert.equal(cached.source_date,'2026-10-23');assert.ok(['live','partial'].includes(cached.status));assert.equal(calls,2);
  });

  test(`${kind}: a sequential successful source regression retains genuine newer observations`,{timeout:1000},async()=>{
    let stamp=Date.parse('2026-10-23T09:33:00+08:00'),calls=0;
    const read=reader(kind,async()=>response(kind,++calls===1?'2026-10-23':'2026-10-22'),()=>stamp);
    const first=await read();stamp+=60_001;
    const older=await read();assert.equal(older.as_of,first.as_of);assert.equal(older.fetched_at,first.fetched_at);
    assert.equal(older.status,'cached');assert.equal(older.source_date,'2026-10-23');assert.match(older.error,/回退/);
  });
}

test('minute-flow deadlines bound an uncooperative source and then allow a later successful request',{timeout:1000},async()=>{
  let stamp=Date.parse('2026-10-23T09:33:00+08:00'),hang=true,calls=0;
  const read=reader('minutes',async()=>{calls++;return hang?new Promise(()=>{}):response('minutes');},()=>stamp,5);
  const failed=await read();assert.equal(failed.status,'unavailable');assert.equal(calls,2);
  stamp+=60_001;hang=false;const fresh=await read();assert.equal(fresh.source_date,'2026-10-23');assert.equal(calls,3);
});
