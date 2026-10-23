/** Replay actual 2026-10-21 production responses, never current market quotes.
 * Purpose/input/call/output/changes: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 * Run: node examples/index-watchlist.mjs
 */
import {readFile} from 'node:fs/promises';
import {mergeWatchingSnapshot,getWatchingSnapshot} from '../lib/watching-data.mjs';
const fixture=JSON.parse(await readFile(new URL('./fixtures/index-watchlist-real.json',import.meta.url),'utf8'));
const before=fixture.before_snapshot;
const flow={rows:before.flows,status:before.flow_status,source:before.flow_source,as_of:before.flow_as_of,fetched_at:before.flow_fetched_at,stale:before.flow_stale,note:before.flow_note,error:before.flow_error,errors:before.flow_errors};
console.log(fixture.description);
console.log('真实输入示例',JSON.stringify({quote:before.instruments[0],history:fixture.index_histories[0]},null,2));
const after=mergeWatchingSnapshot(before,flow,fixture.index_histories);
console.table(after.instruments.filter(row=>row.kind==='index').map(row=>({symbol:row.symbol,name:row.name,before_main_net:before.instruments.find(old=>old.symbol===row.symbol&&old.kind===row.kind).main_net,main_net:row.main_net,flow_as_of:row.flow_as_of,flow_status:row.flow_status,unit:row.unit.main_net})));
const replay=await getWatchingSnapshot({getQuotes:async()=>before,getEtfFlow:async()=>flow,getIndexFlow:async symbol=>fixture.index_histories.find(history=>history.symbol===symbol)});
console.log('前后变化',JSON.stringify({before_rows:before.instruments.length,after_rows:after.instruments.length,index_amounts_available:after.instruments.filter(row=>row.kind==='index'&&Number.isFinite(row.main_net)).length,prices_unchanged:before.instruments.every((row,i)=>row.price===after.instruments[i].price&&row.as_of===after.instruments[i].as_of),etf_flows_unchanged:JSON.stringify(flow.rows)===JSON.stringify(after.flows),async_replay_matches:JSON.stringify(after)===JSON.stringify(replay.snapshot)}));
