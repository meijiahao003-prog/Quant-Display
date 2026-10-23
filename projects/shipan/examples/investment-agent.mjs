/** Actual recorded 2026-10-21 market bundle replay. Never invokes a model.
 * Purpose/input/output and before/after: docs/INVESTMENT_AGENT.md. */
import fs from 'node:fs';
import {loadMarketLensProfile,createMarketLensResearch} from '../investment-agent/src/market-lens.mjs';
const bundle=JSON.parse(fs.readFileSync(new URL('./fixtures/council-market-real.json',import.meta.url),'utf8'));
const before=JSON.stringify(bundle),profile=loadMarketLensProfile();
const research=createMarketLensResearch({bundle,maxRounds:2},profile);
console.log(JSON.stringify({input:'2026-10-21 实际站点响应，固定记录回放，非当前行情',selected:bundle.selected,priceSourceTime:bundle.price_history.as_of,flowSourceTime:bundle.fund_flow_history.as_of,pricePoints:bundle.price_history.bars.length,flowPoints:bundle.fund_flow_history.bars.length,experts:research.experts.map(x=>({id:x.id,name:x.name,loadedChars:x.instructions.length})),knowledgeSummaries:profile.sources.length,materials:research.materials.length,evidenceChars:research.materials.reduce((n,x)=>n+x.text.length,0),inputUnchanged:before===JSON.stringify(bundle),modelCalls:0},null,2));
