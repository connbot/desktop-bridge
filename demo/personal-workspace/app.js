/* A real, reusable local tool generated during the scripted capability demo. */
const $ = id => document.getElementById(id);
let freeAfternoon = false;
let activityCost = 30;
const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(value);
function updateBudget() {
  const budget = Number($('budget').value || 0);
  const total = 340 + activityCost;
  $('food-activities').textContent = '$' + (64 + activityCost);
  $('remaining').textContent = money(budget - total).replace('.00', '');
  $('budget-fill').style.width = Math.min(100, budget ? total / budget * 100 : 100) + '%';
  $('remaining').style.color = budget < total ? '#bc674d' : '#5e854b';
  localStorage.setItem('weekend-budget', String(budget));
}
$('budget').value = localStorage.getItem('weekend-budget') || '420';
$('budget').addEventListener('input', updateBudget);
$('free-afternoon').addEventListener('click', () => {
  freeAfternoon = !freeAfternoon;
  activityCost = freeAfternoon ? 0 : 30;
  $('afternoon-title').textContent = freeAfternoon ? 'Follow the coastal path' : 'Visit a local museum';
  $('afternoon-desc').textContent = freeAfternoon ? 'Fresh air, an unhurried walk, and no ticket needed.' : 'An easy afternoon with room to explore.';
  $('activity-price').textContent = freeAfternoon ? 'FREE' : '$30';
  $('free-afternoon').replaceChildren(document.createTextNode(freeAfternoon ? 'Bring back the museum plan ' : 'Choose a free afternoon '));
  const arrow = document.createElement('span'); arrow.textContent='→'; $('free-afternoon').append(arrow);
  updateBudget();
});
updateBudget();
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.view').forEach(view => view.hidden = view.id !== button.dataset.tab);
  document.querySelectorAll('[data-tab]').forEach(nav => {nav.classList.toggle('active',nav === button);nav.setAttribute('aria-pressed',String(nav === button));});
  $('current-label').textContent = {weekend:'Weekend planner',spending:'Spending snapshot',packing:'Packing checklist'}[button.dataset.tab];
}));
const initialItems = ['Walking shoes','Water bottle','Phone charger','Travel tickets','Light jacket'];
let items;
try {items=JSON.parse(localStorage.getItem('packing-items')) || initialItems.map(text => ({text,done:false}));} catch {items=initialItems.map(text => ({text,done:false}));}
function renderChecklist() {
  $('checklist').replaceChildren();
  items.forEach((item,index) => {
    const label=document.createElement('label'); label.className='check-row';
    const input=document.createElement('input');input.type='checkbox';input.checked=item.done;input.setAttribute('aria-label',item.text);
    input.addEventListener('change',()=>{items[index].done=input.checked;renderChecklist();});
    const text=document.createElement('span');text.textContent=item.text;
    label.append(input,text);$('checklist').append(label);
  });
  $('packing-progress').textContent=`${items.filter(item=>item.done).length} / ${items.length} ready`;
  localStorage.setItem('packing-items',JSON.stringify(items));
}
$('packing-form').addEventListener('submit',event=>{
  event.preventDefault();const text=$('packing-note').value.trim();if(!text)return;
  items.push({text,done:false});$('packing-note').value='';renderChecklist();$('packing-feedback').textContent='Added to your checklist. Saved in this browser.';
});
renderChecklist();
fetch('../outputs/spending-summary.json').then(response=>{if(!response.ok)throw new Error('Sample analysis unavailable');return response.json();}).then(data=>{
  $('total-spending').textContent=money(data.total);
  $('transaction-count').textContent=data.transactions.length;
  for(const [name,value] of Object.entries(data.categories)){
    const row=document.createElement('div');row.className='bar-row';
    const label=document.createElement('span');label.textContent=name;
    const track=document.createElement('div');track.className='bar-track';
    const fill=document.createElement('span');fill.style.width=(value/data.total*100)+'%';track.append(fill);
    const amount=document.createElement('strong');amount.textContent=money(value);row.append(label,track,amount);$('category-bars').append(row);
  }
  for(const transaction of data.transactions){
    const row=document.createElement('tr');
    for(const value of [transaction.date.slice(5),transaction.description,transaction.category,money(transaction.amount)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
    $('transaction-rows').append(row);
  }
}).catch(error=>{$('total-spending').textContent='Unavailable';$('transaction-count').textContent='Analysis error';console.error(error);});
