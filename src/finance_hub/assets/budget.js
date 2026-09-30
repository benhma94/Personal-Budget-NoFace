/* Budget view controller — fetches the payload from the hub API and renders
 * the same Chart.js dashboard the standalone budget_dashboard.html used to
 * embed inline as a global data object. init() is safe to call every time
 * the Budget tab is activated: DOM event listeners are wired once (wire()),
 * while data and charts refresh on every call, so a Journal post is
 * reflected as soon as you switch back to this tab.
 */
window.HubBudget = (() => {
  const CAD = new Intl.NumberFormat('en-CA', {style:'currency',currency:'CAD',maximumFractionDigits:0});
  const BALANCE_CAD = new Intl.NumberFormat('en-CA', {style:'currency',currency:'CAD',minimumFractionDigits:2,maximumFractionDigits:2});
  const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const $ = id => document.getElementById(id);
  const money = v => CAD.format(v||0);
  const signed = v => (v>=0?'+':'')+money(v);
  const color = v => v>=0?'good':'bad';
  const SPENDING_COLORS = ['#6c8cff','#42d6a4','#f6c85f','#ff7070','#a78bfa','#38bdf8','#fb923c','#94a3b8','#f472b6','#2dd4bf','#c084fc','#facc15','#60a5fa','#4ade80','#f87171','#a3e635','#e879f9','#22d3ee','#fbbf24','#818cf8'];
  const TX_PAGE_SIZE = 100;

  let D = null, period = null, mode = 'monthly', yoyBasis = 'monthly', kind = 'expense';
  let charts = {}, viewData = null, comparisonData = null, activeKeys = [], comparisonKeys = [];
  let categorySort = 'actual', categorySortDirection = 'desc';
  let drill = {title:'Transactions',start:'',end:'',category:'',account:'',kind:'',q:'',offset:0,total:0,request:0};
  let drillSearchTimer = null;
  let wired = false;

  function aggregate(keys){
    const first=D.monthly[keys[0]];
    const categories=first.categories.map(row=>({
      category:row.category,kind:row.kind,actual:0,
      budget:row.budget==null?null:0,variance:row.variance==null?null:0
    }));
    const byName=Object.fromEntries(categories.map(row=>[row.category,row]));
    let income=0,income_budget=0,spending=0,spending_budget=0,savings=0,investment_income=0;
    keys.forEach(key=>{
      const value=D.monthly[key];
      income+=value.income; income_budget+=value.income_budget;
      spending+=value.spending; spending_budget+=value.spending_budget;
      savings+=value.savings; investment_income+=value.investment_income;
      value.categories.forEach(row=>{
        byName[row.category].actual+=row.actual;
        if(row.budget!=null)byName[row.category].budget+=row.budget;
      });
    });
    categories.forEach(row=>{
      if(row.kind!=='investment')row.variance=row.kind==='income'?row.actual-row.budget:row.budget-row.actual;
    });
    return {income,income_budget,spending,spending_budget,surplus:income-spending,
      surplus_budget:income_budget-spending_budget,savings,
      savings_rate:income?savings/income:null,investment_income,categories};
  }

  function selectView(){
    $('window-error').textContent='';
    comparisonData=null; comparisonKeys=[];
    if(mode==='monthly'){activeKeys=[period];return D.monthly[period]}
    if(mode==='ytd'){
      activeKeys=D.periods.filter(key=>key.slice(0,4)===period.slice(0,4)&&key<=period);
      return D.ytd[period];
    }
    if(mode==='yoy'){
      const prior=`${+period.slice(0,4)-1}${period.slice(4)}`;
      if(yoyBasis==='ytd'){
        activeKeys=D.periods.filter(key=>key.slice(0,4)===period.slice(0,4)&&key<=period);
        comparisonKeys=D.periods.filter(key=>key.slice(0,4)===prior.slice(0,4)&&key<=prior);
        if(D.ytd[prior])comparisonData=D.ytd[prior];
      } else {
        activeKeys=[period];comparisonKeys=[prior];
        if(D.monthly[prior])comparisonData=D.monthly[prior];
      }
      if(!comparisonData) {
        comparisonKeys=[];
        $('window-error').textContent=`No data is available through ${MONTHS[+period.slice(5)-1]} ${+period.slice(0,4)-1}.`;
      }
      return yoyBasis==='ytd'?D.ytd[period]:D.monthly[period];
    }
    const start=$('custom-start').value,end=$('custom-end').value;
    activeKeys=D.periods.filter(key=>key>=start&&key<=end);
    if(!start||!end||start>end||!activeKeys.length){$('window-error').textContent='Choose a valid start and end month.';return null}
    return aggregate(activeKeys);
  }

  function setCard(id,value,comparison,goodWhenPositive=true){
    const el=$(id);el.textContent=money(value);el.className='value '+(goodWhenPositive?(value>=0?'good':'bad'):'');
    const compare=$(id+'-cmp');compare.textContent=comparison.text;compare.className='compare '+(comparison.value==null?'':color(comparison.value));
  }

  function yoyComparison(current,prior,goodWhenPositive=true){
    if(prior==null)return {text:'Prior-year comparison unavailable',value:null};
    const delta=current-prior,pct=prior?Math.abs(delta/prior)*100:null;
    return {text:`${signed(delta)}${pct==null?'':` (${pct.toFixed(1)}%)`} vs prior year`,value:goodWhenPositive?delta:-delta};
  }

  function investmentContext(value,operatingIncome){
    const combined=value+operatingIncome;
    if(value>0&&combined>0)return {text:`${(value/combined*100).toFixed(1)}% of combined economic income`,value:null};
    if(value<0&&operatingIncome>0)return {text:`Offsets ${(Math.abs(value/operatingIncome)*100).toFixed(1)}% of operating income`,value:null};
    if(value===0)return {text:'No investment gain or loss',value:null};
    return {text:'Investment result for the selected period',value:null};
  }

  function render(){
    viewData=selectView(); if(!viewData)return;
    const end=activeKeys[activeKeys.length-1],B=D.balances[end],label=`${MONTHS[+end.slice(5)-1]} ${end.slice(0,4)}`;
    const yoy=mode==='yoy';
    setCard('income',viewData.income,yoy?yoyComparison(viewData.income,comparisonData?.income):{text:`${signed(viewData.income-viewData.income_budget)} vs ${money(viewData.income_budget)} budget`,value:viewData.income-viewData.income_budget});
    setCard('investment-income',viewData.investment_income,yoy?yoyComparison(viewData.investment_income,comparisonData?.investment_income):investmentContext(viewData.investment_income,viewData.income));
    $('investment-income').className='value '+(viewData.investment_income>=0?'neutral':'bad');
    const combinedIncome=viewData.income+viewData.investment_income;
    $('income-combined').textContent=money(combinedIncome);
    $('income-combined').className=combinedIncome>=0?'good':'bad';
    setCard('spending',viewData.spending,yoy?yoyComparison(viewData.spending,comparisonData?.spending,false):{text:`${signed(viewData.spending_budget-viewData.spending)} remaining of ${money(viewData.spending_budget)}`,value:viewData.spending_budget-viewData.spending},false);
    setCard('surplus',viewData.surplus,yoy?yoyComparison(viewData.surplus,comparisonData?.surplus):{text:`${signed(viewData.surplus-viewData.surplus_budget)} vs plan`,value:viewData.surplus-viewData.surplus_budget});
    $('saving-rate').textContent=viewData.savings_rate==null?'—':(viewData.savings_rate*100).toFixed(1)+'%';
    $('saving-rate').className='value '+(viewData.savings_rate!=null&&viewData.savings_rate>=0?'good':'bad');
    if(yoy&&comparisonData&&viewData.savings_rate!=null&&comparisonData.savings_rate!=null){const delta=viewData.savings_rate-comparisonData.savings_rate;$('saving-cmp').textContent=`${delta>=0?'+':''}${(delta*100).toFixed(1)} pts vs prior year`;$('saving-cmp').className='compare '+color(delta)}
    else {$('saving-cmp').textContent=yoy?'Prior-year comparison unavailable':`${money(viewData.savings)} tagged savings`;$('saving-cmp').className='compare'}
    $('networth').textContent=money(B.net_worth);
    const previous=yoy?comparisonKeys[comparisonKeys.length-1]:D.periods[D.periods.indexOf(end)-1],delta=previous?B.net_worth-D.balances[previous].net_worth:null;
    $('networth-cmp').textContent=delta==null?(yoy?'Prior-year comparison unavailable':label):`${signed(delta)} from ${yoy?'prior year':'prior month'}`;
    $('networth-cmp').className='compare '+(delta==null?'':color(delta));
    renderTrend();renderSpending();renderCategories();renderBalance();
  }

  function chart(id,config){if(charts[id])charts[id].destroy();charts[id]=new Chart($(id),config)}
  const common={responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{boxWidth:10,usePointStyle:true}}},scales:{x:{grid:{display:false}},y:{grid:{color:'rgba(41,51,72,.65)'},ticks:{callback:v=>money(v)}}}};

  function renderTrend(){
    const end=activeKeys[activeKeys.length-1];
    if(mode==='yoy'){
      const year=end.slice(0,4),prior=String(+year-1),month=+end.slice(5);
      const currentKeys=Array.from({length:month},(_,i)=>`${year}-${String(i+1).padStart(2,'0')}`);
      const priorKeys=Array.from({length:month},(_,i)=>`${prior}-${String(i+1).padStart(2,'0')}`);
      const source=yoyBasis==='ytd'?D.ytd:D.monthly,value=(key,field)=>source[key]?.[field]??null,basisLabel=yoyBasis==='ytd'?'YTD ':'';
      $('trend-title').textContent=yoyBasis==='ytd'?'Cumulative cash flow year over year':'Monthly cash flow year over year';$('trend-note').textContent=`${year} compared with ${prior} · ${yoyBasis==='ytd'?'Year to date':'Monthly'}`;
      chart('trend-chart',{type:'line',data:{labels:currentKeys.map(key=>MONTHS[+key.slice(5)-1].slice(0,3)),datasets:[
        {label:`${basisLabel}Income ${year}`,data:currentKeys.map(key=>value(key,'income')),borderColor:'#42d6a4',backgroundColor:'#42d6a4',tension:.3,pointRadius:3,pointHitRadius:24},
        {label:`${basisLabel}Income ${prior}`,data:priorKeys.map(key=>value(key,'income')),borderColor:'#238767',backgroundColor:'#238767',borderDash:[5,4],tension:.3,pointRadius:2,pointHitRadius:24},
        {label:`${basisLabel}Spending ${year}`,data:currentKeys.map(key=>value(key,'spending')),borderColor:'#ff7070',backgroundColor:'#ff7070',tension:.3,pointRadius:3,pointHitRadius:24},
        {label:`${basisLabel}Spending ${prior}`,data:priorKeys.map(key=>value(key,'spending')),borderColor:'#a84848',backgroundColor:'#a84848',borderDash:[5,4],tension:.3,pointRadius:2,pointHitRadius:24}
      ]},options:{...common,interaction:{mode:'index',axis:'x',intersect:false},plugins:{...common.plugins,tooltip:{mode:'index',intersect:false}}}});return;
    }
    $('trend-title').textContent='Monthly cash flow';$('trend-note').textContent='Actual operating income and spending';
    const keys=mode==='custom'?activeKeys:D.periods.filter(key=>key.slice(0,4)===end.slice(0,4)&&key<=end);
    const labels=keys.map(key=>MONTHS[+key.slice(5)-1].slice(0,3)+(keys.length>12?' '+key.slice(2,4):''));
    chart('trend-chart',{type:'line',data:{labels,datasets:[
      {label:'Income',data:keys.map(key=>D.monthly[key].income),borderColor:'#42d6a4',backgroundColor:'#42d6a4',tension:.3,pointRadius:3,pointHitRadius:24,pointHoverRadius:6},
      {label:'Spending',data:keys.map(key=>D.monthly[key].spending),borderColor:'#ff7070',backgroundColor:'#ff7070',tension:.3,pointRadius:3,pointHitRadius:24,pointHoverRadius:6},
      {type:'bar',label:'Surplus',data:keys.map(key=>D.monthly[key].surplus),borderColor:'#6c8cff',backgroundColor:'rgba(108,140,255,.45)',borderWidth:1,borderRadius:4}
    ]},options:{...common,interaction:{mode:'index',axis:'x',intersect:false},plugins:{...common.plugins,tooltip:{mode:'index',intersect:false}}}});
  }

  function renderSpending(){
    const rows=viewData.categories.filter(row=>row.kind==='expense'&&row.actual>0).sort((a,b)=>b.actual-a.actual);
    const total=rows.reduce((sum,row)=>sum+row.actual,0),colors=rows.map(row=>spendingColor(row.category));
    $('spend-total').textContent=money(total);
    $('spending-legend').innerHTML=rows.map((row,index)=>`<button type="button" class="legend-item" data-drill-category="${escapeHtml(row.category)}"><span class="legend-swatch" style="background:${colors[index]}"></span><span>${escapeHtml(row.category)}</span><span class="legend-value">${money(row.actual)}</span></button>`).join('')||'<div class="empty">No positive spending</div>';
    chart('spending-chart',{type:'doughnut',data:{labels:rows.map(row=>row.category),datasets:[{data:rows.map(row=>row.actual),backgroundColor:colors,borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'62%',onClick:(_event,elements)=>{if(elements.length)openCategoryDrill(rows[elements[0].index].category,activeKeys)},plugins:{legend:{display:false},tooltip:{callbacks:{label:context=>{const share=total?context.parsed/total*100:0;return `${context.label}: ${money(context.parsed)} (${share.toFixed(1)}%)`}}}}}});
  }

  function spendingColor(category){
    let hash=0;
    for(const character of category)hash=(hash*31+character.charCodeAt(0))>>>0;
    return SPENDING_COLORS[hash%SPENDING_COLORS.length];
  }

  function categoryLegendLabels(chartInstance){
    return Chart.defaults.plugins.legend.labels.generateLabels(chartInstance).map(item=>item.datasetIndex===0
      ?{...item,fillStyle:'#6c8cff',strokeStyle:'#6c8cff'}
      :item);
  }

  function renderCategories(){
    if(!viewData)return;
    const matchesKind=row=>kind==='income'?(row.kind==='income'||row.kind==='investment'):row.kind===kind;
    const rows=viewData.categories.filter(row=>matchesKind(row)&&Math.abs(row.actual)+Math.abs(row.budget||0)>0);
    const yoy=mode==='yoy',priorYear=String(+period.slice(0,4)-1),currentYear=period.slice(0,4),yoySuffix=yoyBasis==='ytd'?' YTD':'';
    const priorByName=comparisonData?Object.fromEntries(comparisonData.categories.map(row=>[row.category,row])):{};
    const shownRows=yoy?viewData.categories.filter(row=>matchesKind(row)&&(Math.abs(row.actual)+Math.abs(priorByName[row.category]?.actual||0)>0)):rows;
    const sortedRows=shownRows.map(row=>{
      const compare=yoy?(priorByName[row.category]?.actual??0):row.budget;
      const delta=yoy?(kind==='income'?row.actual-compare:compare-row.actual):row.variance;
      return {row,compare,delta,ratio:Number.isFinite(compare)&&compare>0?row.actual/compare:0};
    }).sort((a,b)=>{
      const value=item=>categorySort==='actual'?item.row.actual:item[categorySort];
      let result=categorySort==='category'?a.row.category.localeCompare(b.row.category):(value(a)??0)-(value(b)??0);
      if(!result)result=a.row.category.localeCompare(b.row.category);
      return categorySortDirection==='asc'?result:-result;
    });
    $('category-title').textContent=yoy?(yoyBasis==='ytd'?'Category YTD year over year':'Category monthly year over year'):(kind==='income'?'Income sources: budget vs actual':'Budget vs actual');
    $('category-note').textContent=yoy?'Positive change is favourable':(kind==='income'?'Investment gain / loss is unbudgeted':'Positive variance is favourable');
    $('detail-current').textContent=yoy?currentYear+yoySuffix:'Actual';$('detail-compare').textContent=yoy?priorYear+yoySuffix:'Budget';$('detail-delta').textContent=yoy?'Change':'Variance';
    document.querySelectorAll('#view-budget [data-detail-sort]').forEach(button=>button.closest('th').setAttribute('aria-sort',button.dataset.detailSort===categorySort?(categorySortDirection==='asc'?'ascending':'descending'):'none'));
    chart('category-chart',{
      type:'bar',
      data:{
        labels:sortedRows.map(item=>item.row.kind==='investment'?'Investment gain / loss':item.row.category),
        datasets:[
          {label:yoy?currentYear+yoySuffix:'Actual',data:sortedRows.map(item=>item.row.actual),backgroundColor:context=>{
            const item=sortedRows[context.dataIndex];
            return item?.row.kind==='investment'?(item.row.actual>=0?'#f6c85f':'#ff7070'):'#6c8cff';
          },borderRadius:4},
          {label:yoy?priorYear+yoySuffix:'Budget',data:sortedRows.map(item=>yoy?(priorByName[item.row.category]?.actual??null):item.compare),backgroundColor:'#35425b',borderRadius:4}
        ]
      },
      options:{
        ...common,indexAxis:'y',
        onClick:(_event,elements)=>{
          if(!elements.length)return;
          const point=elements[0],item=sortedRows[point.index];
          if(point.datasetIndex===0)openCategoryDrill(item.row.category,activeKeys);
          else if(yoy&&comparisonKeys.length)openCategoryDrill(item.row.category,comparisonKeys);
        },
        plugins:{...common.plugins,legend:{...common.plugins.legend,labels:{...common.plugins.legend.labels,generateLabels:categoryLegendLabels}}},
        scales:{
          x:{grid:{color:'rgba(41,51,72,.65)'},ticks:{callback:value=>money(value)}},
          y:{grid:{display:false}}
        }
      }
    });
    $('category-body').innerHTML=sortedRows.map(({row,compare,delta,ratio})=>{
      const investment=row.kind==='investment',hasComparison=yoy?Boolean(comparisonData):compare!=null;
      const label=investment?'Investment gain / loss':row.category;
      const info=investment?'<span class="info-badge" title="Month-end portfolio reconciliation result. Excluded from operating income, surplus, and savings rate." aria-label="Investment gain or loss methodology">i</span>':'';
      const progressClass=hasComparison?(delta>0?'positive':delta<0?'negative':''):'';
      const progress=investment?'':`<div class="progress"><span class="${progressClass}" style="width:${Math.min(100,Math.max(0,ratio*100))}%"></span></div>`;
      const actualClass=investment?(row.actual>=0?'neutral':'bad'):'';
      const current=`<button type="button" class="drill-link" data-drill-category="${escapeHtml(row.category)}" data-drill-period="current">${money(row.actual)}</button>`;
      const compared=yoy&&hasComparison?`<button type="button" class="drill-link" data-drill-category="${escapeHtml(row.category)}" data-drill-period="comparison">${money(compare)}</button>`:(hasComparison?money(compare):'—');
      return `<tr class="${investment?'investment-row':''}"><td><button type="button" class="drill-link" data-drill-category="${escapeHtml(row.category)}" data-drill-period="current">${escapeHtml(label)}</button>${info}${progress}</td><td class="right ${actualClass}">${current}</td><td class="right">${compared}</td><td class="right ${hasComparison?color(delta):''}">${hasComparison?signed(delta):'—'}</td></tr>`;
    }).join('')||'<tr><td colspan="4" class="empty">No activity for this view</td></tr>';
  }

  function renderBalance(){
    const end=activeKeys[activeKeys.length-1],B=D.balances[end];
    $('balance-date').textContent='As at '+MONTHS[+end.slice(5)-1]+' '+end.slice(0,4);
    $('asset-total').textContent=BALANCE_CAD.format(B.total_assets||0);$('liability-total').textContent=BALANCE_CAD.format(B.total_liabilities||0);
    const rows=items=>items.filter(item=>Math.abs(item.balance)>.005).sort((a,b)=>b.balance-a.balance).map(item=>`<div class="account-row"><button type="button" class="drill-link" data-drill-account="${escapeHtml(item.account)}">${escapeHtml(item.account)}</button><strong>${BALANCE_CAD.format(item.balance||0)}</strong></div>`).join('')||'<div class="empty">No balances</div>';
    $('assets').innerHTML=rows(B.assets);$('liabilities').innerHTML=rows(B.liabilities);
    const keys=D.periods.filter(key=>key<=end),step=Math.max(1,Math.floor(keys.length/60)),shown=keys.filter((_,index)=>index%step===0||index===keys.length-1);
    chart('networth-chart',{type:'line',data:{labels:shown,datasets:[{label:'Net worth',data:shown.map(key=>D.balances[key].net_worth),borderColor:'#42d6a4',backgroundColor:'rgba(66,214,164,.10)',fill:true,tension:.25,pointRadius:0,pointHitRadius:24,pointHoverRadius:5}]},options:{...common,interaction:{mode:'index',axis:'x',intersect:false},plugins:{...common.plugins,legend:{display:false},tooltip:{mode:'index',intersect:false}}}});
  }

  function escapeHtml(value){return String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]))}

  function monthRange(keys){
    if(!keys.length)return null;
    const first=keys[0],last=keys[keys.length-1],[year,month]=last.split('-').map(Number);
    const lastDay=new Date(Date.UTC(year,month,0)).getUTCDate();
    return {start:`${first}-01`,end:`${last}-${String(lastDay).padStart(2,'0')}`};
  }

  function periodLabel(keys){
    const range=monthRange(keys);
    if(!range)return '';
    return range.start.slice(0,7)===range.end.slice(0,7)?range.start.slice(0,7):`${range.start.slice(0,7)} to ${range.end.slice(0,7)}`;
  }

  function openCategoryDrill(category,keys){
    openDrill({title:category,category,keys});
  }

  function openDrill({title,keys,category='',account='',kind=''}){
    const range=monthRange(keys);
    if(!range)return;
    drill={...drill,title,start:range.start,end:range.end,category,account,kind,q:'',offset:0,total:0};
    $('tx-title').textContent=title;
    $('tx-start').value=drill.start;$('tx-end').value=drill.end;$('tx-search').value='';
    $('tx-category').value=category||(kind?`__${kind}`:'');
    $('tx-account').value=account;
    $('tx-backdrop').hidden=false;
    document.body.style.overflow='hidden';
    $('tx-close').focus();
    loadDrill();
  }

  function closeDrill(){
    $('tx-backdrop').hidden=true;
    document.body.style.overflow='';
  }

  async function loadDrill(){
    const sequence=++drill.request;
    $('tx-error').textContent='';
    $('tx-body').innerHTML='<tr><td colspan="7" class="empty">Loading transactions…</td></tr>';
    const params=new URLSearchParams({start:drill.start,end:drill.end,limit:String(TX_PAGE_SIZE),offset:String(drill.offset)});
    if(drill.category)params.set('category',drill.category);
    if(drill.account)params.set('account',drill.account);
    if(drill.kind)params.set('kind',drill.kind);
    if(drill.q)params.set('q',drill.q);
    try{
      const response=await fetch('/api/budget/transactions?'+params);
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Unable to load transactions.');
      if(sequence!==drill.request)return;
      drill.total=data.total;
      renderDrill(data.items);
    }catch(error){
      if(sequence!==drill.request)return;
      $('tx-error').textContent=error.message;
      $('tx-body').innerHTML='<tr><td colspan="7" class="empty">Transactions unavailable</td></tr>';
    }
  }

  function renderDrill(items){
    const totalImpact=items.reduce((sum,row)=>sum+row.impact_cad,0);
    $('tx-summary').textContent=`${periodLabel([drill.start.slice(0,7),drill.end.slice(0,7)])} · ${drill.total} matching row${drill.total===1?'':'s'} · ${money(totalImpact)} on this page`;
    $('tx-body').innerHTML=items.map(row=>`<tr><td>${row.row_number}</td><td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.debit)}</td><td>${escapeHtml(row.credit)}</td><td class="right">${money(row.amount)}</td><td class="right">${signed(row.impact_cad)}</td><td class="tx-note" title="${escapeHtml(row.note)}">${escapeHtml(row.note)||'—'}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">No transactions match these filters</td></tr>';
    const first=drill.total?drill.offset+1:0,last=Math.min(drill.offset+TX_PAGE_SIZE,drill.total);
    $('tx-page-summary').textContent=`Showing ${first}–${last} of ${drill.total}`;
    $('tx-prev').disabled=drill.offset===0;
    $('tx-next').disabled=drill.offset+TX_PAGE_SIZE>=drill.total;
  }

  function populateDrillFilters(){
    const categories=D.monthly[D.latest_period].categories.map(row=>row.category).sort();
    $('tx-category').innerHTML=['<option value="">All categories</option>','<option value="__expense">All expenses</option>','<option value="__income">All operating income</option>','<option value="__investment">Investment gain / loss</option>',...categories.map(value=>`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join('');
    const balance=D.balances[D.latest_period],accounts=[...balance.assets,...balance.liabilities].map(row=>row.account).sort();
    $('tx-account').innerHTML=['<option value="">All accounts</option>',...accounts.map(value=>`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join('');
  }

  function wire(){
    $('period').addEventListener('change',()=>{if(D.monthly[$('period').value]){period=$('period').value;render()}});
    $('custom-start').addEventListener('change',render);
    $('custom-end').addEventListener('change',render);
    document.querySelectorAll('#view-budget [data-mode]').forEach(button=>button.addEventListener('click',()=>{
      mode=button.dataset.mode;
      document.querySelectorAll('#view-budget [data-mode]').forEach(x=>x.classList.toggle('active',x===button));
      $('custom-controls').classList.toggle('visible',mode==='custom');
      $('yoy-controls').classList.toggle('visible',mode==='yoy');
      $('period-control').style.display=mode==='custom'?'none':'';
      render();
    }));
    document.querySelectorAll('#view-budget [data-yoy-basis]').forEach(button=>button.addEventListener('click',()=>{
      yoyBasis=button.dataset.yoyBasis;
      document.querySelectorAll('#view-budget [data-yoy-basis]').forEach(x=>x.classList.toggle('active',x===button));
      render();
    }));
    document.querySelectorAll('#view-budget [data-kind]').forEach(button=>button.addEventListener('click',()=>{
      kind=button.dataset.kind;
      document.querySelectorAll('#view-budget [data-kind]').forEach(x=>x.classList.toggle('active',x===button));
      renderCategories();
    }));
    document.querySelectorAll('#view-budget [data-detail-sort]').forEach(button=>button.addEventListener('click',()=>{
      const nextSort=button.dataset.detailSort;
      categorySortDirection=categorySort===nextSort?(categorySortDirection==='asc'?'desc':'asc'):(nextSort==='category'?'asc':'desc');
      categorySort=nextSort;
      renderCategories();
    }));
    $('income-drill').addEventListener('click',event=>openDrill({title:'Operating income',kind:'income',keys:mode==='yoy'&&event.target.id==='income-cmp'?comparisonKeys:activeKeys}));
    $('investment-income-drill').addEventListener('click',event=>openDrill({title:'Investment gain / loss',kind:'investment',keys:mode==='yoy'&&event.target.id==='investment-income-cmp'?comparisonKeys:activeKeys}));
    $('spending-drill').addEventListener('click',event=>openDrill({title:'Spending',kind:'expense',keys:mode==='yoy'&&event.target.id==='spending-cmp'?comparisonKeys:activeKeys}));
    $('view-budget').addEventListener('click',event=>{
      const categoryButton=event.target.closest('[data-drill-category]');
      if(categoryButton){
        const keys=categoryButton.dataset.drillPeriod==='comparison'?comparisonKeys:activeKeys;
        openCategoryDrill(categoryButton.dataset.drillCategory,keys);
        return;
      }
      const accountButton=event.target.closest('[data-drill-account]');
      if(accountButton){
        const end=activeKeys[activeKeys.length-1],keys=D.periods.filter(key=>key<=end);
        openDrill({title:accountButton.dataset.drillAccount,account:accountButton.dataset.drillAccount,keys});
      }
    });
    $('tx-close').addEventListener('click',closeDrill);
    $('tx-backdrop').addEventListener('click',event=>{if(event.target===$('tx-backdrop'))closeDrill()});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('tx-backdrop').hidden)closeDrill()});
    for(const id of ['tx-start','tx-end'])$(id).addEventListener('change',()=>{drill[id==='tx-start'?'start':'end']=$(id).value;drill.offset=0;loadDrill()});
    $('tx-category').addEventListener('change',event=>{
      const value=event.target.value;
      drill.category=value.startsWith('__')?'':value;drill.kind=value.startsWith('__')?value.slice(2):'';
      if(value){drill.account='';$('tx-account').value=''}
      drill.offset=0;loadDrill();
    });
    $('tx-account').addEventListener('change',event=>{
      drill.account=event.target.value;
      if(drill.account){drill.category='';drill.kind='';$('tx-category').value=''}
      drill.offset=0;loadDrill();
    });
    $('tx-search').addEventListener('input',event=>{
      drill.q=event.target.value.trim();drill.offset=0;clearTimeout(drillSearchTimer);drillSearchTimer=setTimeout(loadDrill,250);
    });
    $('tx-prev').addEventListener('click',()=>{drill.offset=Math.max(0,drill.offset-TX_PAGE_SIZE);loadDrill()});
    $('tx-next').addEventListener('click',()=>{drill.offset+=TX_PAGE_SIZE;loadDrill()});
  }

  async function init(){
    const res=await fetch('/api/budget/payload');
    if(!res.ok){const err=await res.json().catch(()=>({}));$('asof').textContent='Failed to load budget data: '+(err.error||res.status);return}
    D=await res.json();
    period=D.latest_period;
    const first=D.periods[0],last=D.periods[D.periods.length-1];
    ['period','custom-start','custom-end'].forEach(id=>{$(id).min=first;$(id).max=last});
    $('period').value=period;
    $('custom-start').value=period.slice(0,4)+'-01';
    $('custom-end').value=period;
    populateDrillFilters();
    if(!wired){wire();wired=true}
    $('asof').textContent=`Journal through ${D.latest_transaction_date} · ${D.workbook} is read only`;
    $('footer').textContent=`Generated ${new Date(D.generated_at).toLocaleString()} · Values use formulas last cached by Excel`;
    render();
  }

  return {init};
})();
