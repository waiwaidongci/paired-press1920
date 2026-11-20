import {DOMAIN} from '../constants/domain.js';
import {store,load,loadRows} from '../stores/research.js';
import {runs} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {runList} from '../components/common/run-list.js';
import {processNames} from '../components/common/process-select.js';
import {bindForm} from '../hooks/forms.js';
import {esc} from '../utils/format.js';
export async function render(){await loadRows();const names=processNames();const writable=localStorage.getItem('research-role')!=='viewer';const select=id=>`<select name="${id}" required>${names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('')}</select>`;return `<div class="eyebrow">02 / METHOD WORKSHEET</div><h1>${DOMAIN.field_label}</h1><p class="lead">${DOMAIN.algorithm}</p><div class="method-note">${DOMAIN.note}</div>${writable?`${names.length<2?'<div class="error">当前批次至少需要覆盖两道工艺的报告。</div>':''}<form id="run-form" class="worksheet">${studySelect()}<label>本次研究名称<input name="name" required minlength="2" value="${esc(DOMAIN.names[0])} · 核算"></label><div class="inline-form"><label>目标工艺（起点）${select('process_from')}</label><label>目标工艺（终点）${select('process_to')}</label></div><p>提交时冻结当前一版全部报告（含版本号）与目标工艺对，生成 SHA256。排队中的旧任务保留各自冻结的数据与目标；worker 独立执行，满载 30 秒。</p><button type="submit">冻结输入并排队计算 →</button></form>`:'<p class="method-note">观察员可阅读研究记录，分析师与负责人可以发起计算。</p>'}<h2>计算记录</h2>${runList()}`;}
export function bind(refresh){const sel=document.getElementById('study-select');if(sel)sel.onchange=async()=>{store.studyId=+sel.value;await loadRows();refresh();};bindForm('run-form',async data=>{const r=await runs.create({study_id:+data.study_id,name:data.name,target:{process_from:data.process_from,process_to:data.process_to},parameters:{}});store.runId=r.id;await load();location.hash='results/'+r.id;});}
