'use strict';
// TradingView Lightweight Charts 5.x. No fabricated prices or daily forward fill.
(()=>{
const $=id=>document.getElementById(id),fmt=x=>Number(x).toLocaleString('zh-TW',{maximumFractionDigits:3});
let chart,lines=[],items=[],selected='',asOf,idleTooltip='滑鼠懸停／手指長按可查看報價';
const day=d=>typeof d==='string'?d:`${d.year}-${String(d.month).padStart(2,'0')}-${String(d.day).padStart(2,'0')}`;
function sharedState(s,points){
 const latest=s.points.at(-1),root=$('fhLatest');root.replaceChildren();
 const title=document.createElement('h3'),price=document.createElement('div'),date=document.createElement('p'),source=document.createElement('a');
 title.textContent=s.label+'最新價格';price.className='latest-price';price.textContent=fmt(latest.price)+' '+s.currency+'／'+s.unit;
 date.textContent='報價 '+(latest.quote_date||latest.date)+' · '+s.frequency+' · '+s.note;
 source.href=latest.source_url;source.target='_blank';source.rel='noopener noreferrer';source.textContent='查看'+s.label+'原始來源';root.append(title,price,date,source);
 root.dataset.instrument=s.id;root.dataset.price=latest.price;root.dataset.date=latest.quote_date||latest.date;
 $('priceTitle').textContent='1　'+s.label+'最新價格及歷史走勢';
 $('fhNote').textContent=`已保存${s.points.length}個實際日期，本期間顯示${points.length}筆；最新價格依全部已知報價。`;
 $('historyTitle').textContent=s.label+'價格日期與原始來源';$('historyUnit').textContent='價格（'+s.currency+'／'+s.unit+'）';
 const body=$('fhRows');body.replaceChildren();
 for(const p of [...points].reverse()){const row=document.createElement('tr');for(const text of [p.quote_date||p.date,fmt(p.price)]){const td=document.createElement('td');td.textContent=text;row.append(td);}const td=document.createElement('td'),a=document.createElement('a');a.href=p.source_url;a.textContent='原始報價';a.target='_blank';a.rel='noopener noreferrer';td.append(a);row.append(td);body.append(row);}
}
function show(){
 const s=items.find(x=>x.id===selected),box=$('fhChart'),note=$('chartNote');
 if(!s){box.hidden=true;$('fhLatest').textContent='尚無可核對的價格';note.textContent='尚無可核對的歷史價格；不繪製假走勢。';return;}
 const days=Number($('period').value),cutoff=Date.parse(asOf.slice(0,10))-days*86400000;
 const points=s.points.filter(p=>!days||Date.parse(p.date)>=cutoff);
 idleTooltip=points.length===1?`${points[0].quote_date||points[0].date} · ${fmt(points[0].price)} ${s.currency}／${s.unit} · 此期間僅一筆報價`:'滑鼠懸停／手指長按可查看報價';
 sharedState(s,points);$('showAllHistory').hidden=points.length!==0;
 $('chartMeta').textContent=`${s.label} · ${s.currency}／${s.unit} · ${s.frequency} · ${s.note}`;
 $('chartSource').replaceChildren();const latest=s.points.at(-1),a=document.createElement('a');
 a.href=latest.source_url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=`原始來源 · 最新報價 ${latest.quote_date||latest.date}`;$('chartSource').append(a);
 note.textContent=`所選期間有${points.length}個實際觀察值。移動滑鼠或長按圖表查看日期、價格及較前筆變化；滾輪縮放、拖曳平移。缺口不補價。`;
 if(!window.LightweightCharts){box.hidden=true;note.textContent='互動圖表元件載入失敗；下方原始報價與來源仍可查閱。';return;}
 box.dataset.points=String(points.length);box.hidden=points.length===0;
 if(!points.length){note.textContent='該期間沒有報價，最新價格卡片仍顯示最近已知值；可切換到全部歷史。';$('chartTooltip').textContent='該期間沒有報價';return;}
 if(!chart){
   chart=LightweightCharts.createChart(box,{autoSize:true,height:330,
    layout:{background:{type:'solid',color:'#ffffff'},textColor:'#425f55',attributionLogo:true},
    localization:{locale:'zh-TW'},grid:{vertLines:{color:'#edf3ef'},horzLines:{color:'#edf3ef'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
    handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},
    timeScale:{timeVisible:false,borderColor:'#dce5df'},rightPriceScale:{borderColor:'#dce5df'}});
   chart.subscribeCrosshairMove(param=>{
    if(!param.time){$('chartTooltip').textContent=idleTooltip;return;}
    const current=items.find(x=>x.id===selected),date=day(param.time),i=current.points.findIndex(p=>p.date===date);
    if(i<0){$('chartTooltip').textContent=date+'：無報價';return;}
    const p=current.points[i],prev=current.points[i-1],change=prev?p.price-prev.price:null;
    $('chartTooltip').textContent=`${p.quote_date||p.date} · ${fmt(p.price)} ${current.currency}／${current.unit} · `+(prev?`較前筆 ${change>=0?'+':''}${fmt(change)}（${(change/prev.price*100).toFixed(2)}%）`:'缺少前筆價格');
   });
 }
 for(const l of lines)chart.removeSeries(l);lines=[];
 // Separate series at long gaps: never connect across missing weekly/monthly reports.
 let segments=[[]];
 for(const p of points){const seg=segments.at(-1);if(seg.length&&Date.parse(p.date)-Date.parse(seg.at(-1).time)>s.max_gap_days*86400000)segments.push([]);segments.at(-1).push({time:p.date,value:p.price});}
 for(const segment of segments){const line=chart.addSeries(LightweightCharts.LineSeries,{color:'#26776b',lineWidth:2,lineVisible:segment.length>1,pointMarkersVisible:true,pointMarkersRadius:segment.length===1?5:3,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:true});line.setData(segment);lines.push(line);}
 chart.timeScale().fitContent();
 if(points.length===1){chart.timeScale().setVisibleLogicalRange({from:-2,to:2});const p=points[0];$('chartTooltip').textContent=`${p.quote_date||p.date} · ${fmt(p.price)} ${s.currency}／${s.unit} · 此期間僅一筆報價`;note.textContent+=' 此期間僅一筆，顯示原始價格點，不補造走勢。';}
 else $('chartTooltip').textContent='滑鼠懸停／手指長按可查看報價';
}
window.HongzhouChart={render(data){
 items=data.series;asOf=data.as_of;
 const select=$('chartInstrument'),previous=selected;select.replaceChildren();
 for(const s of items){const option=document.createElement('option');option.value=s.id;option.textContent=s.label;select.append(option);}
 selected=items.some(x=>x.id===previous)?previous:items[0]?.id||'';select.value=selected;select.disabled=!items.length;
 $('futuresLimit').textContent=data.futures_status;show();
},select(){selected=$('chartInstrument').value;show();},period:show,all(){ $('period').value='0';$('period').dispatchEvent(new Event('change'));}};
})();
