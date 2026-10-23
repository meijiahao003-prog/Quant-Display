/** Offline replay of untouched production responses captured 2026-10-22.
 * Every candidate remains the same instrument. No QMT, D1 or live fetch occurs. */
import fs from 'node:fs';
import {persistIndexFlowHistories,scheduleIndexFlowStorage,selectFlowFallback} from '../lib/flow-recovery.mjs';
const read=symbol=>JSON.parse(fs.readFileSync(new URL(`./fixtures/flow-recovery-${symbol}-real.json`,import.meta.url)));
const today=read('000001'),old=read('000300');
for(const input of [today,old]){
  const result=selectFlowFallback({upstream:input,stored:null,sampled:null,item:{symbol:input.symbol,kind:'index',name:input.name},now:Date.parse('2026-10-22T10:20:00+08:00')});
  console.log(JSON.stringify({kind:'real-response-selection-replay',symbol:input.symbol,before:{source_date:input.source_date,as_of:input.as_of,bars:input.bars.length,status:input.status},after:{source_date:result.source_date,as_of:result.as_of,bars:result.bars.length,status:result.status,source_type:result.source_type},observations_unchanged:JSON.stringify(input.bars)===JSON.stringify(result.bars)},null,2));
}
// The production route passes saveIntradayFlowHistory here. This offline adapter
// prints the real would-be input and explicitly does NOT execute a database write.
const dryRun=await persistIndexFlowHistories([today],async input=>{
  console.log(JSON.stringify({kind:'real-storage-input-dry-run',symbol:input.symbol,bars:input.bars.length,as_of:input.as_of,first_bar:input.bars[0],database_write_executed:false}));
  return {status:'not_executed',saved:0,error:'离线示例没有调用生产 D1'};
});
console.log(JSON.stringify({kind:'dry-run-result',status:dryRun.status,saved:dryRun.saved,writer_status:dryRun.results[0].status}));
let callback;
const scheduled=scheduleIndexFlowStorage([today],{after:task=>callback=task,saveHistory:async()=>({status:'not_executed',saved:0}),logError:()=>{}});
console.log(JSON.stringify({kind:'local-scheduling-only',...scheduled,callback_registered:typeof callback==='function',callback_executed:false,database_write_executed:false}));
