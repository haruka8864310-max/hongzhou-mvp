'use strict';
(()=>{
 const $=id=>document?.getElementById(id),node=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;};
 const number=v=>Number(v).toLocaleString('zh-TW',{maximumFractionDigits:2});
 async function load(country='us'){
  const r=await fetch('/api/metals/briefing?country='+country,{cache:'no-store'});if(!r.ok)throw Error('市場摘要讀取失敗');const data=await r.json();if(!$('decisionCopper'))return;
  const copper=data.items.filter(s=>s.id==='wb_copper'),steel=data.items.filter(s=>s.provider.startsWith('tw_'));
  for(const [id,items] of [['decisionCopper',copper],['decisionSteel',steel]]){
   const root=$(id);root.replaceChildren();
   for(const s of items){const card=node('article','', 'card');card.append(node('h3',s.name),node('div',number(s.latest.value)+' '+s.unit,'decision-price'),node('p',s.specification),node('p',s.latest.period+'｜'+(s.stale?'資料已落後，勿作今日報價':'月度參考，非即時'),'muted'),node('p',s.change?'上期 '+(s.change.percent>=0?'+':'')+number(s.change.percent)+'%':'僅有單月，不能判斷漲跌','muted'));const a=node('a','查看原始來源');a.href=s.source_url;a.target='_blank';a.rel='noopener noreferrer';card.append(a);root.append(card);}
   if(!items.length)root.append(node('p','尚未取得資料，請按上方「更新鋼筋與銅資料」。','notice'));
  }
  $('countryMessage').textContent=data.message;$('countryRows').replaceChildren();
  const visible=country==='us'?data.items:steel;
  for(const s of visible){const tr=node('tr','');[s.provider==='tw_rebar'?'台灣':s.provider==='bls'?'美國':'國際基準',s.name+' · '+s.specification,number(s.latest.value)+' '+s.unit,s.latest.period,s.change?(s.change.percent>=0?'+':'')+number(s.change.percent)+'%':'歷史不足',s.stale?'僅作歷史參考':'月度延遲'].forEach(t=>tr.append(node('td',t)));$('countryRows').append(tr);}
  $('countryReference').replaceChildren();if(data.reference){const a=node('a','查閱'+data.reference[0]);a.href=data.reference[1];a.target='_blank';a.rel='noopener noreferrer';$('countryReference').append(a);}
  const root=$('decisionActions');root.replaceChildren();
  for(const s of data.radar.signals){const card=node('article','', 'card');let action='核對今日上下游報價後，再決定是否分批成交。';if(s.title.includes('連續上漲'))action='出售端：向至少兩家下游重詢報價，檢查是否仍沿用舊低價。採購端：先確認銷路再分批收料，避免追價。';else if(s.title.includes('連續下跌'))action='採購端：縮短報價有效期，分批收料。出售端：檢查持料期間的跌價曝險，勿只因歷史下跌就恐慌出售。';else if(s.title.includes('匯率'))action='核對台幣換算後的漲幅，再談收購與出售報價；美元漲幅不能全部加到台幣報價。';else if(s.title.includes('異常'))action='先重詢當日買賣雙方報價，縮短承諾價格的時間，核對異常是否持續。';card.append(node('span','報價查核提醒','badge'),node('h3',s.title),node('p',s.reason),node('p',action,'action-box'),node('p','資料：'+s.periods.join(' → ')+'；計算：'+s.formula,'muted'),node('p',s.limitations+' 尚未扣除運費、品質折讓與加工費，不能據此認定有可賺價差。','muted'));root.append(card);}
  if(!data.radar.signals.length)root.append(node('p','目前資料沒有觸發近期訊號。沒有足夠依據建議追價或囤貨。','notice'));
 }
 function init(){if(!$('decisionCopper'))return;let country='us';document.querySelectorAll('[data-country]').forEach(b=>b.addEventListener('click',()=>{country=b.dataset.country;document.querySelectorAll('[data-country]').forEach(x=>x.classList.toggle('active',x===b));load(country).catch(e=>$('countryMessage').textContent=e.message);}));$('decisionUpdate').addEventListener('click',async()=>{const b=$('decisionUpdate');b.disabled=true;$('decisionUpdateStatus').textContent='正在更新真實來源，請稍候…';try{const r=await fetch('/api/metals/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)throw Error('更新失敗，保留原資料');const result=await r.json();await load(country);$('decisionUpdateStatus').textContent=result.results.map(x=>x.provider+'：'+x.message).join('；');}catch(e){$('decisionUpdateStatus').textContent=e.message;}finally{b.disabled=false;}});load().catch(e=>$('decisionUpdateStatus').textContent=e.message);}
 async function extras(){
  const results=await Promise.allSettled([fetch('/api/metals/highlights').then(r=>{if(!r.ok)throw Error('市場重點讀取失敗');return r.json();}),fetch('/api/metals/status').then(r=>{if(!r.ok)throw Error('來源狀態讀取失敗');return r.json();})]);
  const root=$('marketHighlights');if(!root)return;root.replaceChildren();if(results[0].status==='fulfilled'){const data=results[0].value;for(const s of data.items){const card=node('article','', 'card');card.append(node('h3',s.label+'｜3個月'),node('div',s.change?(s.change.percent>=0?'+':'')+number(s.change.percent)+'%':'資料不足','decision-price'),node('p',s.change?s.change.base_period+' → '+s.period:s.reason,'muted'));if(s.value!==null)card.append(node('p',number(s.value)+' '+s.unit+' | '+s.period,'muted'));if(s.fx_impact)card.append(node('p','美元 '+number(s.fx_impact.usd_percent)+'%；匯率 '+number(s.fx_impact.fx_percent)+'%；台幣近似 '+number(s.fx_impact.twd_percent)+'%'),node('small',s.fx_impact.formula));else if(s.fx_reason)card.append(node('small',s.fx_reason));root.append(card);}$('highlightNote').textContent=data.note;}else root.append(node('p',results[0].reason.message,'error'));
  const sources=$('briefingSources');sources.replaceChildren();if(results[1].status==='fulfilled')for(const s of results[1].value){sources.append(node('p',s.name+'｜'+(s.error?'失敗：'+s.error:s.success_at?'已取得；最新 '+s.latest_period:'尚未更新')+'｜上次成功：'+(s.success_at?new Date(s.success_at).toLocaleString('zh-TW'):'無')));}else sources.append(node('p',results[1].reason.message,'error'));
 }
 const boot=()=>{init();extras().catch(e=>$('decisionUpdateStatus').textContent=e.message);};document.addEventListener('metal-data-updated',()=>{load().catch(e=>$('decisionUpdateStatus').textContent=e.message);extras();});
 $('decisionUpdate')?.addEventListener('click',()=>{const timer=setInterval(()=>{if(!$('decisionUpdate').disabled){clearInterval(timer);extras();}},500);});
 document.addEventListener('hongzhou-ready',boot,{once:true});if(document.documentElement.dataset.appReady==='1')boot();
})();
