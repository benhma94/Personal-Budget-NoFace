/* Budget-plan view controller. Values are staged in memory and written to the
 * Budget sheet only when the user presses Save changes. Empty cells display a
 * trend-adjusted suggestion dimmed until the user edits or fills them.
 */
window.HubBudgetPlan = (() => {
  const $ = id => document.getElementById(id);
  const MONEY_INPUT = new Intl.NumberFormat('en-CA', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const MONEY_TOTAL = new Intl.NumberFormat('en-CA', {style: 'currency', currency: 'CAD', maximumFractionDigits: 0});
  const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  let year = null;
  let plan = null;
  let staged = {};
  let invalid = {};
  let wired = false;
  let saving = false;
  let requestSequence = 0;
  let saveSequence = 0;

  // This view owns its toast because journal.js keeps its helper private.
  function toast(message, ok) {
    const element = $('toast');
    element.textContent = message;
    element.className = 'toast ' + (ok ? 'ok' : 'error');
    element.style.display = 'block';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { element.style.display = 'none'; }, ok ? 3500 : 6000);
  }

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function hasStagedEdit(category, month) {
    return (staged[category] && hasOwn(staged[category], month)) ||
      (invalid[category] && hasOwn(invalid[category], month));
  }

  function anyInvalid() {
    return Object.keys(invalid).some(category => Object.keys(invalid[category]).length > 0);
  }

  function allCategories() {
    return [...plan.income_categories, ...plan.expense_categories];
  }

  function cellState(category, month) {
    const cell = plan.cells[category][month];
    if (invalid[category] && hasOwn(invalid[category], month)) {
      return {value: invalid[category][month], isSuggestion: false, isInvalid: true};
    }
    if (staged[category] && hasOwn(staged[category], month)) {
      return {value: staged[category][month], isSuggestion: false, isInvalid: false};
    }
    if (cell.saved != null) return {value: cell.saved, isSuggestion: false, isInvalid: false};
    if (cell.suggested != null) return {value: cell.suggested, isSuggestion: true, isInvalid: false};
    return {value: null, isSuggestion: false, isInvalid: false};
  }

  // Annual totals only count values the user has actually saved or typed --
  // a dimmed trend suggestion doesn't count toward the total until accepted
  // (e.g. via "Fill blanks with suggestions", which stages it explicitly).
  function committedValue(category, month) {
    const state = cellState(category, month);
    if (state.isSuggestion || state.isInvalid || state.value == null) return 0;
    return state.value;
  }

  function categoryTotal(category) {
    return plan.months.reduce((sum, month) => sum + committedValue(category, month), 0);
  }

  function monthTotal(categories, month) {
    return categories.reduce((sum, category) => sum + committedValue(category, month), 0);
  }

  function computeSummary() {
    const income = plan.income_categories.reduce((sum, category) => sum + categoryTotal(category), 0);
    const expenses = plan.expense_categories.reduce((sum, category) => sum + categoryTotal(category), 0);
    return {income, expenses, net: income + expenses};
  }

  function updateSummary() {
    if (!plan) return;
    const {income, expenses, net} = computeSummary();
    $('bp-total-income').textContent = MONEY_TOTAL.format(income);
    $('bp-total-expenses').textContent = MONEY_TOTAL.format(-expenses);
    $('bp-net').textContent = MONEY_TOTAL.format(net);
    $('bp-net').className = 'bp-stat-value ' + (net >= 0 ? 'good' : 'bad');
  }

  function resetSummary() {
    for (const id of ['bp-total-income', 'bp-total-expenses', 'bp-net']) $(id).textContent = '—';
    $('bp-net').className = 'bp-stat-value';
  }

  function updateRowTotal(input) {
    const row = input.closest('tr');
    const totalCell = row && row.querySelector('.bp-total');
    if (totalCell) totalCell.textContent = MONEY_INPUT.format(categoryTotal(input.dataset.category));
  }

  function hasUnsavedChanges() {
    const stagedDiff = Object.keys(staged).some(category => Object.keys(staged[category]).some(month =>
      plan && plan.cells[category] && plan.cells[category][month] &&
      plan.cells[category][month].saved !== staged[category][month]
    ));
    return stagedDiff || anyInvalid();
  }

  function updateUnsavedIndicator() {
    $('bp-unsaved').hidden = !hasUnsavedChanges();
    $('bp-save').disabled = saving;
    $('bp-fill-suggestions').disabled = saving;
    $('bp-prev-year').disabled = saving;
    $('bp-next-year').disabled = saving;
  }

  function inputNumber(input) {
    return Number(input.value.trim().replace(/,/g, ''));
  }

  function formatMoneyInput(input) {
    if (input.classList.contains('invalid')) return;
    if (input.value.trim() === '') {
      const state = cellState(input.dataset.category, input.dataset.month);
      if (state.value != null && !state.isInvalid) {
        input.value = MONEY_INPUT.format(state.value);
        input.classList.toggle('suggestion', state.isSuggestion);
      }
      return;
    }
    const value = inputNumber(input);
    if (Number.isFinite(value)) input.value = MONEY_INPUT.format(value);
  }

  function setInvalidInput(input, message) {
    input.classList.toggle('invalid', Boolean(message));
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
    input.setCustomValidity(message || '');
    if (message) {
      input.title = message;
      $('bp-status').textContent = message;
    } else {
      input.title = referenceTitle(input.dataset.category, input.dataset.month);
      if (!anyInvalid()) {
        $('bp-status').textContent = `Editing ${plan.workbook}`;
      }
    }
  }

  function stageEdit(category, month, rawValue) {
    const trimmed = rawValue.trim();
    if (!staged[category]) staged[category] = {};
    if (!invalid[category]) invalid[category] = {};
    if (trimmed === '') {
      delete staged[category][month];
      delete invalid[category][month];
    } else {
      const parsed = Number(trimmed.replace(/,/g, ''));
      if (Number.isFinite(parsed)) {
        staged[category][month] = parsed;
        delete invalid[category][month];
      } else {
        delete staged[category][month];
        invalid[category][month] = rawValue;
      }
    }
    updateUnsavedIndicator();
    return trimmed === '' || Number.isFinite(Number(trimmed.replace(/,/g, '')));
  }

  function referenceTitle(category, month) {
    const cell = plan.cells[category][month];
    const budget = cell.prior_year_budget != null ? MONEY_INPUT.format(cell.prior_year_budget) : '—';
    const actual = cell.prior_year_actual != null ? MONEY_INPUT.format(cell.prior_year_actual) : '—';
    return `Prior year budget: ${budget} · Prior year actual: ${actual}`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[character]));
  }

  function categoryRowHtml(category) {
    const cells = plan.months.map(month => {
      const state = cellState(category, month);
      const value = state.value != null && !state.isInvalid ? MONEY_INPUT.format(state.value) : (state.value || '');
      const classes = ['bp-input'];
      if (state.isSuggestion) classes.push('suggestion');
      if (state.isInvalid) classes.push('invalid');
      return `<td class="bp-cell"><input type="text" inputmode="decimal" autocomplete="off" class="${classes.join(' ')}" data-category="${escapeHtml(category)}" data-month="${month}" value="${escapeHtml(value)}" title="${escapeHtml(state.isInvalid ? 'Enter a valid number.' : referenceTitle(category, month))}" aria-invalid="${state.isInvalid ? 'true' : 'false'}"></td>`;
    }).join('');
    const total = `<td class="bp-total">${MONEY_INPUT.format(categoryTotal(category))}</td>`;
    return `<tr><td class="bp-category">${escapeHtml(category)}</td>${cells}${total}</tr>`;
  }

  function sectionHtml(title, categories) {
    if (!categories.length) return '';
    return `<tr class="bp-section-row"><td colspan="${plan.months.length + 2}">${escapeHtml(title)}</td></tr>` +
      categories.map(categoryRowHtml).join('');
  }

  // Column subtotals -- one row per section plus a Net row, mirroring the
  // per-category Total column. Kept in raw signed values like every other
  // grid row (income positive, expenses negative); only the summary strip
  // above the grid flips expenses to a positive magnitude.
  function totalsRowHtml(label, categories, role) {
    if (!categories.length) return '';
    const cells = plan.months.map(month =>
      `<td class="bp-month-total">${MONEY_INPUT.format(monthTotal(categories, month))}</td>`
    ).join('');
    const annual = categories.reduce((sum, category) => sum + categoryTotal(category), 0);
    return `<tr class="bp-totals-row" data-role="${role}"><td class="bp-category">${escapeHtml(label)}</td>${cells}<td class="bp-total">${MONEY_INPUT.format(annual)}</td></tr>`;
  }

  function netRowHtml() {
    const cells = plan.months.map(month => {
      const net = monthTotal(plan.income_categories, month) + monthTotal(plan.expense_categories, month);
      return `<td class="bp-month-total ${net >= 0 ? 'good' : 'bad'}">${MONEY_INPUT.format(net)}</td>`;
    }).join('');
    const {net} = computeSummary();
    return `<tr class="bp-totals-row bp-net-row" data-role="net"><td class="bp-category">Net</td>${cells}<td class="bp-total ${net >= 0 ? 'good' : 'bad'}">${MONEY_INPUT.format(net)}</td></tr>`;
  }

  function monthTotalsHtml() {
    return totalsRowHtml('Total income', plan.income_categories, 'income-total') +
      totalsRowHtml('Total expenses', plan.expense_categories, 'expense-total') +
      netRowHtml();
  }

  function updateMonthTotals() {
    const tbody = $('bp-table').querySelector('tbody');
    if (!tbody) return;
    const oldRows = tbody.querySelectorAll('.bp-totals-row');
    if (!oldRows.length) return;
    oldRows[0].insertAdjacentHTML('beforebegin', monthTotalsHtml());
    oldRows.forEach(row => row.remove());
  }

  function render() {
    if (!plan) return;
    $('bp-year-label').textContent = String(year);
    const head = `<tr><th>Category</th>${MONTH_LABELS.map(month => `<th>${month}</th>`).join('')}<th>Total</th></tr>`;
    const body = sectionHtml('Income', plan.income_categories) + sectionHtml('Expenses', plan.expense_categories) + monthTotalsHtml();
    $('bp-table').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
    updateUnsavedIndicator();
    updateSummary();
  }

  function fillBlanksWithSuggestions() {
    if (!plan) {
      toast('Budget plan is still loading.', false);
      return;
    }
    let filled = 0;
    for (const category of allCategories()) {
      for (const month of plan.months) {
        const cell = plan.cells[category][month];
        if (cell.saved == null && cell.suggested != null) {
          if (hasStagedEdit(category, month)) continue;
          if (!staged[category]) staged[category] = {};
          staged[category][month] = cell.suggested;
          filled += 1;
        }
      }
    }
    render();
    toast(filled ? `Filled ${filled} blank cells` : 'No blank suggestions to fill', true);
  }

  function confirmDiscardIfDirty() {
    return !hasUnsavedChanges() || confirm('Discard unsaved budget changes?');
  }

  function discardChanges() {
    if (saving) return false;
    // Incrementing this token makes an in-flight save response harmless after
    // the user explicitly discards the edit session.
    saveSequence += 1;
    staged = {};
    invalid = {};
    if (plan) render();
    return true;
  }

  async function changeYear(delta) {
    if (saving || !confirmDiscardIfDirty()) return;
    const requested = year + delta;
    if (requested < 1900 || requested > 9999) return;
    year = requested;
    staged = {};
    invalid = {};
    await load();
  }

  function updatesForSave() {
    const updates = [];
    for (const category of Object.keys(staged)) {
      for (const month of Object.keys(staged[category])) {
        const value = staged[category][month];
        const original = plan.cells[category][month].saved;
        if (original !== value) updates.push({category, month, value});
      }
    }
    return updates;
  }

  function removeSavedStagedUpdates(updates) {
    for (const update of updates) {
      if (staged[update.category] && staged[update.category][update.month] === update.value) {
        delete staged[update.category][update.month];
      }
    }
  }

  async function save() {
    if (saving) return;
    if (Object.keys(invalid).some(category => Object.keys(invalid[category]).length > 0)) {
      toast('Fix invalid budget values before saving.', false);
      const firstInvalid = document.querySelector('.bp-input.invalid');
      if (firstInvalid) firstInvalid.focus();
      return;
    }
    const updates = updatesForSave();
    if (!updates.length) { toast('Nothing to save', true); return; }
    const currentSave = ++saveSequence;
    const savedYear = year;
    saving = true;
    updateUnsavedIndicator();
    try {
      const response = await fetch('/api/budget-plan/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({updates}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `Save failed (${response.status})`);
      if (currentSave !== saveSequence || savedYear !== year) return;
      // The write succeeded even if the following refresh fails. Keep the
      // baseline current so a later edit can revert a just-saved value.
      for (const update of updates) {
        plan.cells[update.category][update.month].saved = update.value;
      }
      removeSavedStagedUpdates(updates);
      const refreshed = await load();
      if (currentSave === saveSequence && savedYear === year) {
        if (refreshed) toast('Budget saved', true);
        else toast('Budget saved, but the grid could not refresh. Reopen Budget to retry.', false);
      }
    } catch (error) {
      if (currentSave === saveSequence) toast(error.message, false);
    } finally {
      if (currentSave === saveSequence) {
        saving = false;
        updateUnsavedIndicator();
      }
    }
  }

  async function load() {
    const currentRequest = ++requestSequence;
    const requestedYear = year;
    if (!plan || plan.year !== requestedYear) {
      plan = null;
      $('bp-table').innerHTML = '';
      resetSummary();
    }
    $('bp-status').textContent = 'Loading...';
    try {
      const response = await fetch(`/api/budget-plan/payload?year=${encodeURIComponent(requestedYear)}`);
      const data = await response.json().catch(() => ({}));
      if (currentRequest !== requestSequence || requestedYear !== year) return false;
      if (!response.ok) throw new Error(data.error || 'Unable to load budget plan.');
      if (Number(data.year) !== Number(requestedYear)) throw new Error('Loaded budget plan year does not match the requested year.');
      plan = data;
      $('bp-status').textContent = `Editing ${plan.workbook}`;
      render();
      return true;
    } catch (error) {
      if (currentRequest === requestSequence && requestedYear === year) {
        // Snap back to the last successfully loaded year so the label and
        // internal state can't drift apart after a failed prev/next click.
        if (plan) year = plan.year;
        $('bp-year-label').textContent = String(year);
        $('bp-status').textContent = 'Failed to load budget plan';
        toast(error.message, false);
      }
      return false;
    }
  }

  function wire() {
    $('bp-table').addEventListener('input', event => {
      const input = event.target.closest('input[data-category][data-month]');
      if (!input) return;
      input.classList.remove('suggestion');
      const valid = stageEdit(input.dataset.category, input.dataset.month, input.value);
      setInvalidInput(input, valid ? '' : 'Enter a valid number.');
      updateRowTotal(input);
      updateMonthTotals();
      updateSummary();
    });
    $('bp-table').addEventListener('focusout', event => {
      const input = event.target.closest('input[data-category][data-month]');
      if (input) formatMoneyInput(input);
    });
    $('bp-prev-year').addEventListener('click', () => changeYear(-1));
    $('bp-next-year').addEventListener('click', () => changeYear(1));
    $('bp-fill-suggestions').addEventListener('click', fillBlanksWithSuggestions);
    $('bp-save').addEventListener('click', save);
  }

  async function init() {
    if (!wired) { wire(); wired = true; }
    if (saving) return;
    if (year === null) {
      year = new Date().getFullYear();
      const loaded = await load();
      // If the current year is fully populated, start the planner on the next
      // year. The staged maps are still empty during this first-load choice.
      if (loaded && plan && plan.latest_saved_month === `${year}-12`) {
        year += 1;
        await load();
      }
      return;
    }
    await load();
  }

  // The public surface retains the usual {init, hasUnsavedChanges} API and
  // adds discardChanges so the shell can honor an explicit discard decision.
  return {init, hasUnsavedChanges, discardChanges, isSaving: () => saving};
})();
