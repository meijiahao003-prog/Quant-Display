/** Display persisted macro conclusions without changing their dates or evidence. */
import {shanghaiDate} from './market-time.mjs';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SOURCE_PAGE='/macro-source.html';
const timeText=value=>Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'时间未知';
const validBrief=value=>value&&/^\d{4}-\d{2}-\d{2}$/.test(value.reportDate)&&Number.isFinite(Date.parse(value.generatedAt))&&typeof value.summary==='string'&&value.summary.trim();
const safeUrl=value=>{const raw=String(value??'').trim();if(/^\/(?!\/)/.test(raw)&&!/[\\\r\n\t]/.test(raw))return raw;try{const url=new URL(raw);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}};
const sourceLink=(label,url)=>safeUrl(url)?`<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:`<span>${esc(label)}</span>`;

/** Keep the newest successful brief through missing, failed, or out-of-order reads. */
export function mergeMacroResponse(previous,incoming) {
  if(!incoming||incoming.ok!==true||!('brief' in incoming))throw new Error('宏观结论响应格式无效');
  if(incoming.brief!==null&&!validBrief(incoming.brief))throw new Error('宏观结论缺少报告日期、生成时间或结论');
  const old=previous?.brief,next=incoming.brief;
  const oldWins=validBrief(old)&&(!next||old.reportDate>next.reportDate||(old.reportDate===next.reportDate&&Date.parse(old.generatedAt)>Date.parse(next.generatedAt)));
  return {...incoming,brief:oldWins?old:next,...(oldWins?{retained:true}:{}),readError:null};
}

/** Dates describe the report, not the macro indicator's monthly/quarterly period. */
export function macroPanelMarkup(payload,{now=Date.now(),loading=false}={}) {
  const brief=validBrief(payload?.brief)?payload.brief:null,today=shanghaiDate(now);
  const readError=payload?.readError,attempt=payload?.latestAttempt,failed=attempt?.status==='failed';
  const attemptFailedApplies=failed&&(!brief||!attempt.reportDate||attempt.reportDate>=brief.reportDate);
  const error=readError||(attemptFailedApplies?attempt.message:null);
  if(!brief)return `<div class="macro-empty"><span>◌</span><b>${loading?'正在读取宏观研究结论':'宏观结论待更新'}</b><p>${esc(error||'尚未收到资料空间与研究手册形成的有效结论。缺失内容不作为中性或利好信号。')}</p><small>今天 ${esc(today)} · 北京时间</small></div><div class="macro-source-footer">${sourceLink('打开宏观资料空间',SOURCE_PAGE)}</div>`;
  const todayReport=brief.reportDate===today,future=brief.reportDate>today;
  const stateLabel=readError?'读取失败 · 保留已存结论':attemptFailedApplies?'本轮更新失败 · 保留结论':payload.retained?'保留较新结论':future?'报告日期异常':todayReport?'今日结论':'历史结论 · 待今日更新';
  const lists=(items,className)=>Array.isArray(items)&&items.length?`<ul class="${className}">${items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'';
  const sources=Array.isArray(brief.sources)?brief.sources:[];
  return `<div class="macro-report-meta"><span class="macro-state ${todayReport&&!error?'ok':'warn'}">${esc(stateLabel)}</span><span>报告 ${esc(brief.reportDate)}</span><small>结论生成 ${esc(timeText(brief.generatedAt))} · 北京时间</small></div>
    ${error?`<p class="macro-error" role="status">${esc(error)}</p>`:''}
    <div class="macro-verdict"><span class="macro-stance">${esc(brief.stance||'综合研判')}</span><h3>${esc(brief.title||'宏观市场研究')}</h3><p>${esc(brief.summary)}</p></div>
    ${Array.isArray(brief.dimensions)&&brief.dimensions.length?`<div class="macro-dimensions">${brief.dimensions.map((x,index)=>`<details class="macro-dimension" data-macro-detail="dimension-${index}"><summary><b>${esc(x.name)}</b><span>${esc(x.signal)}</span></summary><p>${esc(x.evidence)}</p></details>`).join('')}</div>`:''}
    ${Array.isArray(brief.watchpoints)&&brief.watchpoints.length?`<details class="macro-watch" data-macro-detail="watchpoints"><summary>今日观察与验证 · ${brief.watchpoints.length}</summary>${lists(brief.watchpoints,'macro-points')}</details>`:''}
    ${Array.isArray(brief.risks)&&brief.risks.length?`<details class="macro-risks" data-macro-detail="risks"><summary>风险与资料缺口 · ${brief.risks.length}</summary>${lists(brief.risks,'macro-points')}</details>`:''}
    <div class="macro-evidence-status"><i></i>${brief.dataStatus==='complete'?'资料覆盖完整':'资料部分覆盖'} · 指标沿用各自所属期</div>
    <details class="macro-sources" data-macro-detail="sources"><summary>依据与来源 · ${sources.length} 项</summary>${sources.map(x=>`<article>${sourceLink(x.title||'研究来源',x.url)}<small>源所属期 / 时点：${esc(x.asOf||'未知')}</small></article>`).join('')||'<p>未提供可核验来源。</p>'}</details>
    <div class="macro-source-footer">${sourceLink('宏观资料空间',brief.sourcePageUrl||SOURCE_PAGE)}${safeUrl(brief.handbookUrl)?sourceLink('研究手册',brief.handbookUrl):'<span>研究手册 · 见资料来源</span>'}</div>`;
}

/** Independent bounded reader: slow market requests cannot hold this panel. */
export function createMacroReader({request,render,now=Date.now,timeoutMs=15000}={}) {
  let payload=null,pending=null;
  const redraw=loading=>render(payload,{now:now(),loading});
  async function refresh() {
    if(pending)return pending;
    redraw(true);
    pending=(async()=>{
      const controller=new AbortController();let timer;
      try{
        const response=await Promise.race([request(controller.signal),new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('宏观结论读取超时'));},timeoutMs);})]);
        payload=mergeMacroResponse(payload,response);
      }catch(error){payload={...payload,readError:String(error?.message??'宏观结论读取失败')};}
      finally{clearTimeout(timer);pending=null;redraw(false);}
      return payload;
    })();
    return pending;
  }
  return {refresh,redraw:()=>redraw(false),getPayload:()=>payload};
}

/** Bind same-origin reads only; optional market gateways do not own macro briefs. */
export function initMacroPanel({document:doc=globalThis.document,fetch:fetcher=globalThis.fetch}={}) {
  const content=doc.getElementById('macro-content'),button=doc.getElementById('macro-refresh');
  const reader=createMacroReader({request:async signal=>{
    const response=await fetcher('/api/macro',{cache:'no-store',signal});
    if(!response.ok)throw new Error(`宏观结论暂不可用（${response.status}）`);
    return response.json();
  },render:(payload,options)=>{
    const html=macroPanelMarkup(payload,options);
    if(content.innerHTML!==html){
      const expanded=new Set([...(content.querySelectorAll?.('details[open][data-macro-detail]')??[])].map(node=>node.dataset.macroDetail));
      content.innerHTML=html;
      for(const node of content.querySelectorAll?.('details[data-macro-detail]')??[])if(expanded.has(node.dataset.macroDetail))node.open=true;
    }
    button.disabled=options.loading;button.classList.toggle('loading',options.loading);
  }});
  button.onclick=()=>reader.refresh();
  return reader;
}
