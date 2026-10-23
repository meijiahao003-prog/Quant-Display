/** 将真实保存的会话转为可携带报告；没有最终结论时不生成替代答案。 */
export function renderReport(run) {
  const lines = ['# 投资专家圆桌 · 研究记录', '', `问题：${run.question}`, '',
    `创建：${run.createdAt}`, `状态：${run.status}`, `模型：${run.model || 'Codex 默认'}`,
    `轮次：${run.round}/${run.maxRounds}；调用次数：${run.calls}`, '',
    '> 本记录来自同一模型的不同角色。引用检查只验证资料编号存在，不等同事实核验。',
    '> 本版没有自动行情、外部检索或交易执行；结论基于用户提供的材料。', ''];
  if (run.marketLens) {
    lines.push('## 看盘快照与专家方法来源', '', `冻结时间：${run.marketLens.analysis_asof}（${run.marketLens.timezone}）`, `数据模式：${run.marketLens.mode === 'demo' ? '合成演示，非真实行情' : '真实源快照；状态与延迟以输入为准'}`, `标的：${run.marketLens.selected.name} ${run.marketLens.selected.symbol}`, `快照SHA-256：${run.marketLens.bundle_sha256}`, `方法配置：${run.marketLens.profile_id}；SHA-256：${run.marketLens.profile_sha256}`, '', ...run.marketLens.notes.map(item => `- ${item}`), '', '方法包：', ...run.marketLens.experts.map(item => `- ${item.name}：${item.package}；版本${item.skill_version}；ZIP SHA-256 ${item.package_sha256}`), '', `方法知识摘要：${run.marketLens.knowledge.count}条；SHA-256 ${run.marketLens.knowledge.sha256}。核验标记来自原包，未独立回源复核；不是当前市场事实、训练或历史PIT批准。`, '');
  }
  for (const round of run.rounds || []) {
    lines.push(`## 第 ${round.round} 轮`, '');
    for (const opinion of round.opinions || []) {
      lines.push(`### ${opinion.expertName}`, '', `立场：${opinion.stance}`, '', opinion.summary, '');
      for (const arg of opinion.arguments || []) lines.push(`- ${arg.claim}（材料：${arg.evidenceIds.join(', ') || '未引用'}）`);
      if (opinion.risks?.length) lines.push('', '风险：', ...opinion.risks.map(x => `- ${x}`));
      if (opinion.missing?.length) lines.push('', '待补充：', ...opinion.missing.map(x => `- ${x}`));
      lines.push('');
    }
  }
  if (run.final) {
    if (['failed', 'cancelled'].includes(run.status)) lines.push('> 当前会话已失败或取消；下面保留的是此前轮次结果，不代表本次讨论成功完成。', '');
    lines.push('## 综合报告', '', run.final.summary, '');
    for (const [key, title] of [['agreement','共同判断'],['disagreements','保留分歧'],['missing','证据缺口'],['nextSteps','下一步']]) {
      if (run.final[key]?.length) lines.push(`### ${title}`, '', ...run.final[key].map(x => `- ${x}`), '');
    }
  } else lines.push('## 综合报告', '', '尚未生成最终结论。', '');
  if (run.error) lines.push(`运行说明：${typeof run.error === 'string' ? run.error : run.error.message || JSON.stringify(run.error)}`, '');
  lines.push('## 输入材料', '');
  for (const material of run.materials || []) lines.push(`### ${material.id} · ${material.title}`, '', material.text, '');
  return lines.join('\n');
}
