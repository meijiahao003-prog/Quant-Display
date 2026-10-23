import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPairedData,splitObservedSegments} from '../public/paired-chart.mjs';

// Synthetic protocol fixtures only; never returned to the production UI.
const item={symbol:'510300',kind:'etf'};
const history=bars=>({symbol:'510300',unit:'CNY',bars});
const point=(time,main_net=0)=>({time,main_net,super_net:-10,large_net:10});

test('Shanghai times align by minute while source seconds, zeros and absent periods survive',()=>{
  const input={item,priceBars:[{time:'2026-10-21 10:46',close:4.2}],flowHistory:history([point('2026-10-21T02:46:18Z')])};
  const before=JSON.stringify(input),result=buildPairedData(input),index=result.labels.indexOf('10:46');
  assert.equal(result.date,'2026-10-21');assert.equal(result.labels.length,242);
  assert.equal(result.prices[index],4.2);assert.equal(result.flows.main_net[index],0);
  assert.equal(result.flowObservations[index].time,'2026-10-21T02:46:18Z');
  assert.equal(result.flowObservations[index].source_time,'2026-10-21T10:46:18.000+08:00');
  assert.equal(result.flows.main_net[index-1],null);assert.equal(result.flows.main_net[index+1],null);
  assert.equal(result.flowCount,1);assert.equal(result.priceCount,1);assert.equal(JSON.stringify(input),before);
});

test('previous day and another ETF never appear under the selected current-day prices',()=>{
  const priceBars=[{time:'2026-10-21 10:46',close:4.2}];
  const old=buildPairedData({item,priceBars,flowHistory:history([point('2026-10-20T10:46:18+08:00',500)])});
  assert.equal(old.flowCount,0);assert.ok(old.flows.main_net.every(value=>value===null));assert.match(old.flowMessage,/未跨日拼接/);
  assert.equal(old.date,'2026-10-21');assert.equal(old.flowDate,'2026-10-20');assert.equal(old.dateMismatch,true);
  const another=buildPairedData({item,priceBars,flowHistory:{...history([point('2026-10-21T10:46:18+08:00')]),symbol:'159915'}});
  assert.equal(another.flowCount,0);assert.match(another.flowMessage,/标的不一致/);
  const index=buildPairedData({item:{symbol:'000300',kind:'index'},priceBars,flowHistory:history([point('2026-10-21T10:46:18+08:00')])});
  assert.equal(index.flowCount,0);assert.equal(index.flowUnit,null);assert.match(index.flowMessage,/标的不一致/);
});

test('bad dates, unknown currency and clock-only real inputs cannot masquerade as aligned data',()=>{
  const priceBars=['2026-02-30 09:30','2026-10-21 24:10','2026-10-21T09:30:00+14:30','09:30'].map(time=>({time,close:4.2}));
  const invalid=buildPairedData({item,priceBars,flowHistory:history([point('2026-02-30T09:30:00+08:00')])});
  assert.equal(invalid.priceCount,0);assert.equal(invalid.flowCount,0);assert.equal(invalid.date,null);
  const unknown=buildPairedData({item,priceBars:[{time:'2026-10-21 09:30',close:4.2}],flowHistory:{...history([point('2026-10-21T09:30:00+08:00')]),unit:'raw'}});
  assert.equal(unknown.flowCount,0);assert.equal(unknown.flowUnit,null);
  const demo=buildPairedData({item,demo:true,priceBars:[{time:'09:30',close:4.2}]});
  assert.equal(demo.date,'2026-10-20');assert.equal(demo.priceCount,1);
});

test('out-of-order duplicate minutes retain the latest observed point, gaps and missing components stay null',()=>{
  const result=buildPairedData({item,priceBars:[{time:'2026-10-21 10:46:59',close:4.3},{time:'2026-10-21 10:46:10',close:4.2}],flowHistory:history([
    {...point('2026-10-21T10:46:55+08:00',80),super_net:null},point('2026-10-21T10:46:15+08:00',40),point('2026-10-21T10:50:10+08:00',100)
  ])});
  const index=result.labels.indexOf('10:46');
  assert.equal(result.priceCount,1);assert.equal(result.flowCount,2);assert.equal(result.prices[index],4.3);
  assert.equal(result.flows.main_net[index],80);assert.equal(result.flows.super_net[index],null);
  assert.deepEqual(result.flows.main_net.slice(index+1,index+4),[null,null,null]);
});

test('source price date controls an outage day; observed flow supplies a date only without price evidence',()=>{
  const flowHistory=history([point('2026-10-20T10:46:18+08:00',-100)]);
  const sourceDate=buildPairedData({item,priceBars:[],priceAsOf:'2026-10-21T10:46:00+08:00',flowHistory});
  assert.equal(sourceDate.date,'2026-10-21');assert.equal(sourceDate.flowCount,0);
  const flowOnly=buildPairedData({item,priceBars:[],flowHistory});
  assert.equal(flowOnly.date,'2026-10-20');assert.equal(flowOnly.flowCount,1);assert.equal(flowOnly.priceCount,0);
});

test('short sample gaps share a drawable segment while long gaps split, without filling or losing zero',()=>{
  const base=Date.parse('2026-10-21T10:00:00+08:00');
  const values=[0,null,20,null,null,null,60];
  const observations=values.map((value,index)=>value===null?null:{source_epoch:base+index*60_000});
  const before=JSON.stringify({values,observations}),segments=splitObservedSegments(values,observations);
  assert.equal(segments.length,2);
  assert.deepEqual(segments[0],[0,null,20,null,null,null,null]);
  assert.deepEqual(segments[1],[null,null,null,null,null,null,60]);
  assert.equal(segments.flat().filter(value=>value!==null).length,3);
  assert.deepEqual(splitObservedSegments([null,null],[null,null]),[]);
  assert.deepEqual(splitObservedSegments([5],[{source_epoch:null}]),[]);
  assert.equal(JSON.stringify({values,observations}),before);
});


test('index day-minute flow is accepted only with matching symbol and explicit kind',()=>{
  const indexItem={symbol:'000300',kind:'index'},priceBars=[{time:'2026-10-21 09:31',close:4500}];
  const indexHistory={symbol:'000300',kind:'index',unit:'CNY',status:'live',bars:[point('2026-10-21 09:31',100)]};
  const aligned=buildPairedData({item:indexItem,priceBars,flowHistory:indexHistory});
  assert.equal(aligned.flowCount,1);assert.equal(aligned.flowUnit,'CNY');
  assert.equal(aligned.flows.main_net[aligned.labels.indexOf('09:31')],100);
  for(const kind of ['etf',undefined]){
    const rejected=buildPairedData({item:indexItem,priceBars,flowHistory:{...indexHistory,kind}});
    assert.equal(rejected.flowCount,0);assert.match(rejected.flowMessage,/标的不一致/);
  }
  const etfWrongKind=buildPairedData({item,priceBars,flowHistory:{...history([point('2026-10-21 09:31')]),kind:'index'}});
  assert.equal(etfWrongKind.flowCount,0);
});

test('provider minute curves break at absent minutes and lunch without fabricating observations',()=>{
  const indexItem={symbol:'000300',kind:'index'};
  const flowHistory={symbol:'000300',kind:'index',unit:'CNY',bars:[
    point('2026-10-21 11:28',10),point('2026-10-21 11:30',20),
    point('2026-10-21 13:01',30),point('2026-10-21 13:02',40)
  ]};
  const result=buildPairedData({item:indexItem,flowHistory});
  const segments=splitObservedSegments(result.flows.main_net,result.flowObservations,90000);
  assert.equal(segments.length,3);assert.equal(result.flowCount,4);
  assert.equal(result.flows.main_net[result.labels.indexOf('11:29')],null);
  assert.equal(result.flows.main_net[result.labels.indexOf('13:00')],null);
  assert.deepEqual(segments.map(segment=>segment.filter(value=>value!==null)),[[10],[20],[30,40]]);
});
