import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {retainFlowSnapshot,flowPanelState} from '../public/flow-snapshot.mjs';
import {withMarketFreshness} from '../public/market-time.mjs';

// Actual previously recorded production observations. Only outage/rollover
// envelopes below are simulated; changed dates are never market observations.
const {before_snapshot:actual}=JSON.parse(await readFile(new URL('../examples/fixtures/index-watchlist-real.json',import.meta.url),'utf8'));
const now=Date.parse('2026-10-21T13:45:00+08:00');

test('HTTP 200 with unavailable flow preserves all genuine rows and their acquisition time',()=>{
  const incoming={...actual,flow_status:'unavailable',flow_fetched_at:null,flows:actual.flows.map(row=>({...row,status:'unavailable',main_net:null,as_of:null}))};
  const before=JSON.stringify({actual,incoming}),result=retainFlowSnapshot(actual,incoming,now);
  assert.equal(result.flows.length,10);assert.equal(result.flow_status,'cached');
  for(let i=0;i<actual.flows.length;i++){
    for(const key of ['main_net','super_net','large_net','medium_net','small_net','as_of'])assert.equal(result.flows[i][key],actual.flows[i][key]);
    assert.equal(result.flows[i].fetched_at,actual.flow_fetched_at);assert.equal(result.flows[i].status,'cached');
  }
  assert.equal(JSON.stringify({actual,incoming}),before);
  assert.deepEqual(result.instruments.map(row=>row.price),actual.instruments.map(row=>row.price));
});

test('older successful responses cannot replace newer observations; fresh zero is valid',()=>{
  const older={...actual,flows:actual.flows.map(row=>({...row,as_of:'2026-10-21T09:31:00+08:00'}))};
  assert.equal(retainFlowSnapshot(actual,older,now).flows[0].as_of,actual.flows[0].as_of);
  const fresh={...actual,flows:actual.flows.map(row=>({...row,as_of:'2026-10-21T13:44:00+08:00',main_net:0,super_net:0,large_net:0}))};
  const result=retainFlowSnapshot(actual,fresh,now);
  assert.equal(result.flows[0].main_net,0);assert.equal(result.flows[0].status,'live');
});

test('partial new-day recovery ranks only the new date; selected older ETF structure stays labeled stale',()=>{
  const nextDay=Date.parse('2026-10-22T09:32:00+08:00');
  const incoming={...actual,instruments:actual.instruments.map(row=>({...row,as_of:'2026-10-22T09:31:00+08:00'})),flows:[{...actual.flows[0],as_of:'2026-10-22T09:31:00+08:00',trade_date:'2026-10-22'}]};
  const result=retainFlowSnapshot(actual,incoming,nextDay),ranking=flowPanelState(result,null,'ranking',nextDay);
  assert.equal(ranking.date,'2026-10-22');assert.equal(ranking.rows.length,1);assert.equal(ranking.excluded,9);assert.equal(ranking.status,'partial');
  const structure=flowPanelState(result,'510500','structure',nextDay);
  assert.equal(structure.status,'stale');assert.equal(structure.selected.main_net,actual.flows[1].main_net);
  assert.equal(structure.as_of,actual.flows[1].as_of);
  assert.equal(result.instruments.find(row=>row.symbol==='510500').main_net,null);
  assert.equal(result.instruments.find(row=>row.symbol==='510500').flow_status,'unavailable');
});

test('transport failures mark each retained ETF cached, including structure status',()=>{
  const result=retainFlowSnapshot(actual,{...actual,flows:[],flow_status:'unavailable',flow_error:'simulated HTTP failure'},now);
  assert.equal(flowPanelState(result,'510300','structure',now).status,'cached');
  assert.equal(flowPanelState(result,null,'ranking',now).cached_count,10);
  assert.equal(result.flows[0].error,'simulated HTTP failure');
});

test('per-row provenance and fetched time override the batch metadata',()=>{
  const sample={...actual,flows:[{...actual.flows[0],source:'recorded minute source',fetched_at:'2026-10-21T05:42:00Z'}]};
  const structure=flowPanelState(sample,'510300','structure',now);
  assert.equal(structure.source,'recorded minute source');assert.equal(structure.fetched_at,'2026-10-21T05:42:00Z');
});

test('demo and legacy external snapshots bypass retention and identities remain ETF-only',()=>{
  const demo={...actual,mode:'demo'};assert.equal(retainFlowSnapshot(actual,demo,now),demo);
  const external={...actual};delete external.flows;assert.equal(retainFlowSnapshot(actual,external,now),external);
  const result=retainFlowSnapshot(actual,{...actual,flows:[{...actual.flows[0],symbol:'000001'}]},now);
  assert.equal(result.flows.some(row=>row.symbol==='000001'),false);
  assert.deepEqual(result.instruments.filter(row=>row.kind==='index'),withMarketFreshness(actual,now).instruments.filter(row=>row.kind==='index'));
});
