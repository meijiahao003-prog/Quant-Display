/** Replay the existing recorded real input; never fetch or manufacture quotes. */
import fs from 'node:fs';
import {withMarketFreshness} from '../public/market-time.mjs';
import {createRefreshController} from '../public/refresh-controller.mjs';
const recorded=JSON.parse(fs.readFileSync(new URL('./fixtures/council-market-real.json',import.meta.url)));
const price=recorded.price_history,now=Date.parse('2026-10-22T10:00:00+08:00');
const result=withMarketFreshness(price,now);
console.log(JSON.stringify({kind:'recorded-real-input-replay',before:{as_of:price.as_of,status:price.status,bars:price.bars.length},after:{as_of:result.as_of,today:result.today,source_date:result.source_date,status:result.status,freshness:result.freshness,bars:result.bars.length},observations_unchanged:JSON.stringify(price.bars)===JSON.stringify(result.bars)},null,2));
let clock=Date.parse('2026-10-21T23:59:59+08:00'),calls=0;
const controller=createRefreshController({now:()=>clock,refresh:async()=>{calls++;}});
clock+=1000;await controller.tick();
console.log(JSON.stringify({kind:'synthetic-midnight-clock',refresh_calls:calls,elapsed_ms:1000,model_calls:0}));
