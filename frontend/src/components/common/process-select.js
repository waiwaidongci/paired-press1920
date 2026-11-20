import {store} from '../../stores/research.js';
import {esc} from '../../utils/format.js';
export function processNames(){return [...new Set(store.rows.flatMap(r=>[r.process_a,r.process_b]))].sort();}
export function processOptions(){return `<datalist id="process-list">${processNames().map(n=>`<option value="${esc(n)}">`).join('')}</datalist>`;}
