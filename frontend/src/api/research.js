import {request,post} from './client.js';
export const studies={list:()=>request('/studies'),create:x=>post('/studies',x),rows:id=>request(`/studies/${id}/reports`),save:(id,x,row)=>row?request(`/studies/${id}/reports/${row}`,{method:'PUT',body:JSON.stringify(x)}):post(`/studies/${id}/reports`,x),remove:(id,row)=>request(`/studies/${id}/reports/${row}`,{method:'DELETE'})};
export const runs={list:()=>request('/runs'),create:x=>post('/runs',x),get:id=>request(`/runs/${id}`),review:(id,note)=>post(`/runs/${id}/review`,{note}),export:id=>request(`/runs/${id}/export`)};
