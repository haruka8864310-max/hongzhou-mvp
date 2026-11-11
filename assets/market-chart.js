'use strict';
// TradingView Lightweight Charts 5.x. No fabricated prices or daily forward fill.
(()=>{
const $=id=>document.getElementById(id),fmt=x=>Number(x).toLocaleString('zh-TW',{maximumFractionDigits:3});
let chart,lines=[],items=[],selected='',asOf;
const day=d=>typeof d==='string'?d:`${d.year}-${String(d.month).padStart(2,'0')}-${String(d.day).padStart(2,'0')}`;
function show(){
 const s=items.find(x=>x.id===selected),box=$('fhChart'),note=$('chartNote');
 if(!s){box.hidden=true;note.textContent='尚無可核對的歷史價格；不繪製假走勢。';return;}
 const days=Number($('period').value),cutoff=Date.parse(asOf.slice(0,10))-days*86400000;
 const points=s.points.filter(p=>!days||Date.parse(p.date)>=cutoff);
 $('chartMeta').textContent=`${s.label} · ${s.currency}／${s.unit} · ${s.frequency} · ${s.note}`;
 $('chartSource').replaceChildren();const latest=s.points.at(-1),a=document.createElement('a');
 a.href=latest.source_url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=`原始來源 · 最新報價 ${latest.quote_date||latest.date}`;$('chartSource').append(a);
 note.textContent=`所選期間有${points.length}個實際觀察值。移動滑鼠或長按圖表查看日期、價格及較前筆變化；滾輪縮放、拖曳平移。缺口不補價。`;
 if(!window.LightweightCharts){box.hidden=true;note.textContent='互動圖表元件載入失敗；下方原始報價與來源仍可查閱。';return;}
 box.hidden=points.length<2;box.dataset.points=String(points.length);
 if(points.length<2){note.textContent+=' 此期間不足兩筆，請選較長期間。';return;}
 if(!chart){
   chart=LightweightCharts.createChart(box,{autoSize:true,height:330,
    layout:{background:{type:'solid',color:'#ffffff'},textColor:'#425f55',attributionLogo:true},
    localization:{locale:'zh-TW'},grid:{vertLines:{color:'#edf3ef'},horzLines:{color:'#edf3ef'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
    handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},
    timeScale:{timeVisible:false,borderColor:'#dce5df'},rightPriceScale:{borderColor:'#dce5df'}});
   chart.subscribeCrosshairMove(param=>{
    if(!param.time){$('chartTooltip').textContent='滑鼠懸停／手指長按可查看報價';return;}
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
 for(const segment of segments){const line=chart.addSeries(LightweightCharts.LineSeries,{color:'#26776b',lineWidth:2,pointMarkersVisible:true,pointMarkersRadius:3,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:true});line.setData(segment);lines.push(line);}
 chart.timeScale().fitContent();$('chartTooltip').textContent='滑鼠懸停／手指長按可查看報價';
}
window.HongzhouChart={render(data){
 items=data.series;asOf=data.as_of;
 const select=$('chartInstrument'),previous=selected;select.replaceChildren();
 for(const s of items){const option=document.createElement('option');option.value=s.id;option.textContent=s.label;select.append(option);}
 selected=items.some(x=>x.id===previous)?previous:items[0]?.id||'';select.value=selected;select.disabled=!items.length;
 $('futuresLimit').textContent=data.futures_status;show();
},select(){selected=$('chartInstrument').value;show();},period:show};
})();
