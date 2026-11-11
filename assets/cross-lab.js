'use strict';
window.CrossLab={inventory(data){
 const root=document.getElementById('crossInventory');root.replaceChildren();
 for(const s of data.series){const p=document.createElement('p');p.textContent=`${s.source}／${s.market}：${s.observations}筆，${s.first_date}～${s.last_date}；價格缺值${s.missing_price_ratio==null?'完整歷史未知':(s.missing_price_ratio*100).toFixed(2)+'%'}；歷史公布時間缺值${(s.missing_publication_ratio*100).toFixed(1)}%。`;root.append(p);}
 for(const f of data.imports?.nbs?.failures||[]){const p=document.createElement('p');p.textContent=f.url+' · '+(f.http_status?'HTTP '+f.http_status+' · ':'')+f.error;root.append(p);}
},render(r){
 const root=document.getElementById('crossResults');root.hidden=!r.comparisons;if(!r.comparisons)return;
 const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;},pct=x=>(x*100).toFixed(2)+'%';
 const body=document.getElementById('crossMetrics');body.replaceChildren();
 for(const [key,m] of Object.entries(r.comparisons)){const tr=document.createElement('tr');for(const value of [key+' '+m.name,m.samples,pct(m.accuracy),...['down','flat','up'].map(k=>pct(m.recall[k]))])tr.append(node('td',value));body.append(tr);}
 const leads=document.getElementById('crossLeads');leads.replaceChildren();
 for(const l of r.lead_analysis||[]){if(!l.samples)continue;const tr=document.createElement('tr');for(const value of [({japan:'日本東京製鐵',us:'美國BLS',china:'中國螺紋鋼',china_hrc:'中國熱軋旬價'})[l.source],l.weeks+'週',l.samples,l.correlation==null?'樣本／變動不足':l.correlation.toFixed(3),pct(l.direction_agreement)])tr.append(node('td',value));leads.append(tr);}
 document.getElementById('crossAudit').textContent=JSON.stringify({confusion_matrices:Object.fromEntries(Object.entries(r.comparisons).map(([k,v])=>[k,v.confusion_matrix])),effective_source_samples:r.effective_source_samples,lead_analysis:r.lead_analysis,feature_missing_ratio:r.feature_missing_ratio,imports:r.inventory.series},null,2);
 const evidence=document.getElementById('crossEvidence');evidence.replaceChildren();
 for(const e of r.evidence.C){const tr=document.createElement('tr');for(const value of [e.origin,e.target,({up:'上漲',flat:'平盤',down:'下跌'})[e.actual],({up:'上漲',flat:'平盤',down:'下跌'})[e.predicted]])tr.append(node('td',value));const td=document.createElement('td'),a=node('a','官方原始公告');a.href=e.source_url;a.target='_blank';a.rel='noopener noreferrer';td.append(a);tr.append(td);evidence.append(tr);}
}};
