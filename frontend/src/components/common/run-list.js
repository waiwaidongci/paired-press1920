import {store} from '../../stores/research.js';
import {table} from './table.js';
import {status} from './status.js';
import {esc} from '../../utils/format.js';
export function runList(){return table(['编号 / 运行','状态','冻结报告','目标工艺对','复核'],store.runs.map(r=>[{html:`<a href="#results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},`${r.snapshot?.reports?.length ?? '—'}份 · ${r.checksum.slice(0,12)}`,`${esc(r.snapshot?.target?.from ?? '—')} → ${esc(r.snapshot?.target?.to ?? '—')}`,r.review?'已复核':'待负责人复核']));}
