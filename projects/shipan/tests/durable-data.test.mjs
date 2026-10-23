import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {asStoredMarket,selectDurableSnapshot,selectDurableHistory,isPersistablePriceObservation} from '../lib/durable-data.mjs';

const now=Date.parse('2026-10-23T10:30:00+08:00');
// Synthetic quote values below exercise recovery; they are never market observations.
const quote=(symbol,as_of,price)=>({symbol,kind:'etf',price,as_of,status:'live',source_status:'live',
  fetched_at:'2026-10-23T02:20:00.000Z',stale:false,unit:{price:'CNY'}});
const snapshot=rows=>({mode:'real',status:'live',instruments:rows,fetched_at:'2026-10-23T02:20:00.000Z'});

test('partial quote responses keep newer whole stored observations per instrument',()=>{
  const old=snapshot([quote('510300','2026-10-23T10:20:00+08:00',4.2),quote('510500','2026-10-23T10:19:00+08:00',6.1)]);
  const input=snapshot([quote('510300','2026-10-23T10:18:00+08:00',4.0)]);
  const frozen=JSON.stringify(old),result=selectDurableSnapshot(input,old,now);
  const selected=result.instruments.find(row=>row.symbol==='510300');
  assert.equal(selected.price,4.2);assert.equal(selected.as_of,'2026-10-23T10:20:00+08:00');
  assert.equal(selected.status,'cached');assert.equal(selected.fetched_at,'2026-10-23T02:20:00.000Z');
  assert.equal(result.instruments.find(row=>row.symbol==='510500').price,6.1);
  assert.equal(result.instruments.find(row=>row.symbol==='512480').price,null);
  assert.equal(JSON.stringify(old),frozen);assert.equal(result.status,'cached');
});

test('fresh quotes replace only their own observation and mixed dates remain explicitly stale',()=>{
  const old=snapshot([quote('510300','2026-10-22T15:00:00+08:00',4.1),quote('510500','2026-10-22T15:00:00+08:00',6.0)]);
  const result=selectDurableSnapshot(snapshot([quote('510300','2026-10-23T10:25:00+08:00',4.3)]),old,now);
  const fresh=result.instruments.find(row=>row.symbol==='510300'),cached=result.instruments.find(row=>row.symbol==='510500');
  assert.equal(result.status,'partial');assert.equal(fresh.status,'live');assert.equal(fresh.price,4.3);
  assert.equal(cached.status,'stale');assert.equal(cached.source_date,'2026-10-22');assert.equal(cached.price,6.0);
});

test('real recorded price series survives unavailable response without relabeling its date or acquisition time',()=>{
  const recorded=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/price-refresh-510300-real.json',import.meta.url),'utf8'));
  const unavailable={symbol:'510300',kind:'etf',period:'1m',status:'unavailable',bars:[],as_of:null,error:'fixture outage'};
  const result=selectDurableHistory(unavailable,recorded,now);
  assert.equal(result.status,'stale');assert.equal(result.source_status,'cached');assert.equal(result.source_date,'2026-10-22');
  assert.equal(result.fetched_at,recorded.fetched_at);assert.deepEqual(result.bars,recorded.bars);assert.equal(result.error,'fixture outage');
  assert.equal(selectDurableHistory({...unavailable,symbol:'510500'},recorded,now).status,'unavailable');
});

test('same-date daily candles use actual acquisition time; older response cannot replace later observation',()=>{
  const make=(fetched_at,close)=>({symbol:'510300',kind:'etf',period:'day',status:'live',as_of:'2026-10-23',fetched_at,bars:[{time:'2026-10-23',close}]});
  const latest=make('2026-10-23T02:25:00.000Z',4.3),older=make('2026-10-23T02:20:00.000Z',4.1);
  assert.equal(selectDurableHistory(older,latest,now).bars[0].close,4.3);
  assert.equal(selectDurableHistory(latest,older,now).bars[0].close,4.3);
  const stored=asStoredMarket(latest,now);assert.equal(stored.status,'cached');assert.equal(stored.fetched_at,latest.fetched_at);
});

test('newly fetched older source day can be persisted, while cache reads and future dates cannot',()=>{
  // Boundary fixture based on the observed Oct23 acquisition of 513100's Oct22 daily series.
  const oldDay={symbol:'513100',kind:'etf',period:'day',status:'stale',source_status:'live',freshness:'previous_day',
    stale:true,cache_hit:false,as_of:'2026-10-22',fetched_at:'2026-10-23T03:00:00.000Z',bars:[{time:'2026-10-22',close:1}]};
  assert.equal(isPersistablePriceObservation(oldDay),true);
  assert.equal(isPersistablePriceObservation({...oldDay,cache_hit:true}),false);
  assert.equal(isPersistablePriceObservation({...oldDay,source_status:'cached'}),false);
  assert.equal(isPersistablePriceObservation({...oldDay,freshness:'future_day'}),false);
  assert.equal(isPersistablePriceObservation({...oldDay,fetched_at:null}),false);
  const today={...oldDay,status:'live',source_status:'live',stale:false,freshness:'current_day',as_of:'2026-10-23',
    fetched_at:'2026-10-23T02:55:00.000Z',bars:[{time:'2026-10-23',close:2}]};
  // A newly fetched old date still loses to an existing newer source observation.
  assert.equal(selectDurableHistory(oldDay,today,now).as_of,'2026-10-23');
  assert.equal(selectDurableHistory(oldDay,today,now).bars[0].close,2);
});
