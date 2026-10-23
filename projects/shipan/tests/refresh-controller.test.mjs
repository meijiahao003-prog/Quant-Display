import test from 'node:test';
import assert from 'node:assert/strict';
import {createRefreshController} from '../public/refresh-controller.mjs';

// Synthetic clock/event scenarios, not a claim about a future market response.
test('Shanghai midnight refreshes before the ordinary minute interval',async()=>{
 let time=Date.parse('2026-10-21T23:59:59+08:00'),calls=0,days=[];
 const controller=createRefreshController({now:()=>time,refresh:async()=>calls++,onDateChange:(...x)=>days.push(x)});
 time+=1000;await controller.tick();
 assert.equal(calls,1);assert.deepEqual(days,[['2026-10-22','2026-10-21']]);
 time+=1000;await controller.tick();assert.equal(calls,1);
 time+=59000;await controller.tick();assert.equal(calls,2);
});
test('a hidden overnight page refreshes immediately when visible again',async()=>{
 let time=Date.parse('2026-10-21T14:00:00+08:00'),visible=false,calls=0;
 const controller=createRefreshController({now:()=>time,isVisible:()=>visible,refresh:async()=>calls++});
 time+=86400000;await controller.tick();assert.equal(calls,0);
 visible=true;await controller.resume();assert.equal(calls,1);
 await controller.resume();await controller.tick();assert.equal(calls,1);
});
test('resume during an active connection queues one retry without overlapping',async()=>{
 let time=Date.parse('2026-10-22T10:00:00+08:00'),busy=true,calls=0;
 const controller=createRefreshController({now:()=>time,isBusy:()=>busy,refresh:async()=>calls++});
 time+=5000;await controller.resume();assert.equal(calls,0);
 busy=false;time+=1000;await controller.tick();assert.equal(calls,1);
});
test('a suspended request spanning midnight cannot suppress the next day refresh',async()=>{
 let time=Date.parse('2026-10-21T23:58:59+08:00'),release,calls=0;
 const controller=createRefreshController({now:()=>time,refresh:()=>{calls++;return calls===1?new Promise(resolve=>release=resolve):Promise.resolve();}});
 time+=60000;const first=controller.tick();
 time+=1000;await controller.tick();assert.equal(calls,1);
 release();await first;time+=1000;await controller.tick();assert.equal(calls,2);
});
test('demo mode never triggers automatic live fetches',async()=>{
 let time=Date.parse('2026-10-21T23:59:59+08:00'),calls=0;
 const controller=createRefreshController({now:()=>time,isActive:()=>false,refresh:async()=>calls++});
 time+=86400000;await controller.tick();await controller.resume();assert.equal(calls,0);
});
