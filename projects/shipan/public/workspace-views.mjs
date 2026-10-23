/** Navigation changes visibility only; the market data controllers stay mounted. */
export function workspaceView(value) {
  const key=['overview','advice','alerts','research'].includes(value)?value:'overview';
  const titles={overview:['市场总览','总览、自选与资金集中看盘，宏观先导提供每日研究背景。'],advice:['投资建议','选择研究标的，交给三位专家讨论；完整意见与分歧在这里查看。'],alerts:['预警中心','把需要持续关注的变化，变成明确的规则。'],research:['开源研究台','成熟组件、真实来源，以及可以继续扩展的接口。']};
  return {key,title:titles[key][0],subtitle:titles[key][1],visible:{'ticker-grid':key==='overview','chart-panel':key==='overview','watch-panel':key==='overview','flow-panel':key==='overview','investment-view':key==='advice','alerts-panel':key==='alerts','research-panel':key==='research'}};
}
