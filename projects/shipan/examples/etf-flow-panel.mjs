import {readFile} from 'node:fs/promises';
import {retainFlowSnapshot,flowPanelState} from '../public/flow-snapshot.mjs';

// These are saved genuine observations, not a fresh upstream request.
const {before_snapshot:actual}=JSON.parse(await readFile(new URL('./fixtures/index-watchlist-real.json',import.meta.url),'utf8'));
const now=Date.parse('2026-10-21T13:45:00+08:00');
console.log('真实历史输入',JSON.stringify({rows:actual.flows.length,first:actual.flows[0],fetched_at:actual.flow_fetched_at}));
// Explicitly simulated failure envelope; amounts/time in actual remain unchanged.
const failed={...actual,flows:[],flow_status:'unavailable',flow_fetched_at:null,flow_error:'模拟请求失败'};
const retained=retainFlowSnapshot(actual,failed,now);
console.log('保留后输出',JSON.stringify({rows:retained.flows.length,status:retained.flow_status,first:retained.flows[0]}));
const ranking=flowPanelState(retained,null,'ranking',now);
const structure=flowPanelState(retained,'510300','structure',now);
console.log('排行输出',JSON.stringify({rows:ranking.rows.length,date:ranking.date,excluded:ranking.excluded,status:ranking.status,cached_count:ranking.cached_count}));
console.log('结构输出',JSON.stringify({symbol:structure.selected.symbol,status:structure.status,as_of:structure.as_of,fetched_at:structure.fetched_at,main_net:structure.selected.main_net}));
