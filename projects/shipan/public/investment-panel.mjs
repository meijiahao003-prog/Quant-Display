/** Hosted UI for the existing local expert council. Real-input workflow and
 * observable effects: docs/INVESTMENT_AGENT.md. No hosted LLM or simulated opinion.
 */
import {COUNCIL_PROTOCOL,councilOrigin,acceptsCouncilMessage,newCouncilId} from './council-client.mjs';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusNames={running:'会审进行中',completed:'会审完成',disputed:'保留分歧',needs_evidence:'待补充证据',failed:'会审失败',cancelled:'已停止',interrupted:'会审中断'};

/** Bind explicit export/open/cancel actions; receive only this window's session. */
export function initInvestmentPanel({getBundle,download,toast}) {
  let session=null,lastBundle=null;
  const setStatus=(text)=>{$('council-status').textContent=text;};
  const send=(type,extra={})=>{if(session?.popup&&!session.popup.closed)session.popup.postMessage({protocol:COUNCIL_PROTOCOL,channel:session.channel,type,...extra},session.origin);};
  const sendBundle=()=>{if(session?.ready&&session.bundle&&!session.sent){send('bundle',{bundle:session.bundle});session.sent=true;setStatus('行情资料已送入，请在本地窗口点击「开始会审」。');}};
  const showBundle=bundle=>{$('council-frozen').hidden=false;$('council-frozen').textContent=`本次资料：${bundle.selected.name} · ${bundle.selected.symbol} · ${new Date(bundle.analysis_asof).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} 冻结（北京时间） · ${bundle.mode==='demo'?'合成演示':'源数据'} · 价格 ${bundle.price_history.bars.length} 点 / 资金 ${bundle.fund_flow_history.bars.length} 点 · 价格源 ${bundle.price_history.as_of??'未取得'} · 资金源 ${bundle.fund_flow_history.as_of??'未取得'}${bundle.mode!=='demo'&&!bundle.source_dates?.all_current_day?' · 含旧日或缺失资料，按原日期分析':''}`;};
  const renderRun=run=>{
    const rounds=Array.isArray(run.rounds)?run.rounds:[],latest=rounds.at(-1),opinions=latest?.opinions??[];
    $('council-results').hidden=false;
    $('council-progress').textContent=`${statusNames[run.status]??run.status} · 第 ${run.round??0}/${run.maxRounds??3} 轮 · ${run.calls??0} 次模型调用`;
    $('council-opinions').innerHTML=opinions.map(op=>`<article class="council-opinion"><b>${esc(op.expertName)}</b><span>${esc({support:'证据支持',oppose:'提出反对',uncertain:'尚不确定'}[op.stance]??op.stance)}</span><p>${esc(op.summary)}</p>${(op.arguments??[]).map(a=>`<p class="council-evidence">${esc(a.claim)} <small>${esc((a.evidenceIds??[]).join('、'))}</small></p>`).join('')}${op.missing?.length?`<details><summary>资料缺口 ${op.missing.length} 项</summary><ul>${op.missing.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:''}${op.risks?.length?`<details><summary>风险与异议</summary><ul>${op.risks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:''}</article>`).join('')||'<p class="muted">正在等待三位专家的首轮意见…</p>';
    const final=run.final;
    $('council-conclusion').innerHTML=final?`<b>主持人综合 · ${esc(statusNames[final.status]??final.status)}</b><p>${esc(final.summary)}</p>${[['共识',final.agreement],['保留的分歧',final.disagreements],['待补充证据',final.missing],['下一步',final.nextSteps]].filter(([,xs])=>xs?.length).map(([label,xs])=>`<details ${label==='保留的分歧'?'open':''}><summary>${label} · ${xs.length}</summary><ul>${xs.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`).join('')}`:run.error?`<p>${esc(run.error)}</p>`:'';
    $('council-cancel').hidden=run.status!=='running';
    if(run.status!=='running')setStatus(run.error?`未完成：${run.error}`:`${statusNames[run.status]??run.status}。完整讨论与报告保存在本地投资 Agent。`);
  };
  $('council-open').onclick=async()=>{
    if(session?.run?.status==='running'&&!session.popup?.closed){session.popup.focus();toast('本次会审仍在进行，可先停止或等待结果。');return;}
    let origin;try{origin=councilOrigin($('council-url').value.trim());}catch(error){toast(error.message);return;}
    const channel=newCouncilId(),url=new URL('/market-lens.html',origin);url.hash=new URLSearchParams({origin:location.origin,channel}).toString();
    const popup=window.open(url.href,`market-lens-council-${channel}`,'popup,width=1050,height=850');
    if(!popup){setStatus('浏览器拦截了窗口。允许本站弹窗后重试，或导出资料到本地 Agent。');return;}
    session={popup,origin,channel,ready:false,sent:false,bundle:null,run:null};const started=session;$('council-results').hidden=true;$('council-cancel').hidden=true;$('council-frozen').hidden=true;
    $('council-open').disabled=true;setStatus('正在冻结当前标的，并打开本地投资 Agent…');
    try{const bundle=await getBundle($('agent-question').value);if(started!==session)return;session.bundle=bundle;lastBundle=bundle;showBundle(bundle);sendBundle();if(!session.ready)setStatus('资料已准备。等待本地 Agent；请确认已启动新版工作站。');}
    catch(error){if(started===session)setStatus(`资料未送出：${error.message}`);}
    finally{$('council-open').disabled=false;}
    setTimeout(()=>{if(started===session&&!session.ready)setStatus('尚未连上本地 Agent。请下载启动新版工作站，再重试；也可导出资料后导入。');},20000);
  };
  $('council-export').onclick=async()=>{const button=$('council-export');button.disabled=true;try{const bundle=await getBundle($('agent-question').value);lastBundle=bundle;showBundle(bundle);download(`market-lens-${bundle.selected.symbol}-council.json`,JSON.stringify(bundle,null,2));toast('已导出冻结资料，可在本地投资 Agent 的看盘接入页导入。');}catch(error){toast(error.message);}finally{button.disabled=false;}};
  $('council-cancel').onclick=()=>{send('cancel',{bundle_id:session?.bundle?.bundle_id});setStatus('已请求停止本次会审，等待本地确认。');};
  $('council-save-result').onclick=()=>{if(!session?.run){toast('尚无本次会审结果。');return;}download(`council-${session.run.id}.json`,JSON.stringify({bundle:lastBundle,run:session.run},null,2));};
  window.addEventListener('message',event=>{
    if(!acceptsCouncilMessage(event,session))return;
    const data=event.data;
    if(data.type==='ready'){session.ready=true;sendBundle();}
    else if(data.type==='state'){const status=data.state??data;setStatus(status.message??(status.verified?'本地 Agent 已通过连接验证。':status.connected?'已登录，请在本地完成连接验证。':'请在本地投资 Agent 完成 ChatGPT 登录。'));}
    else if(data.type==='error')setStatus(`本地 Agent：${data.message??'连接失败'}`);
    else if(data.type==='run'&&session.bundle&&data.bundle_id===session.bundle.bundle_id&&data.run&&typeof data.run.id==='string'){
      session.run=data.run;renderRun(data.run);
    }
  });
  setInterval(()=>{if(session?.popup?.closed&&!session.disconnected){session.disconnected=true;$('council-cancel').hidden=true;setStatus('本地窗口已关闭，结果回传已断开。若会审仍在运行，请到本地工作站查看或停止。');}},2000);
}
