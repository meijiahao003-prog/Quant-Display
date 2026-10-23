import fs from 'node:fs';
import {selectDurableHistory} from '../lib/durable-data.mjs';

// Replay an actual saved October 22 response, not a new quote or a fabricated series.
const saved=JSON.parse(fs.readFileSync(new URL('./fixtures/price-refresh-510300-real.json',import.meta.url),'utf8'));
const unavailable={symbol:saved.symbol,kind:saved.kind,period:saved.period,status:'unavailable',bars:[],as_of:null,error:'回放模拟源失败'};
const output=selectDurableHistory(unavailable,saved,Date.parse('2026-10-23T10:30:00+08:00'));
console.log(JSON.stringify({before:{status:unavailable.status,bars:unavailable.bars.length},after:{status:output.status,
  source_status:output.source_status,source_date:output.source_date,as_of:output.as_of,fetched_at:output.fetched_at,
  bars:output.bars.length,bars_unchanged:JSON.stringify(output.bars)===JSON.stringify(saved.bars)}},null,2));
