/* Retirement planner controller: live defaults, v3 browser persistence,
 * scheduled cash flows, reproducible Monte Carlo ranges, and fixed stress cases. */
window.HubRetirement = (() => {
  const STORAGE_KEY = 'financeHub.retirement.v3';
  const LEGACY_STORAGE_KEY = 'financeHub.retirement.v2';
  const CAD = new Intl.NumberFormat('en-CA', {style:'currency', currency:'CAD', maximumFractionDigits:0});
  const MONEY_INPUT = new Intl.NumberFormat('en-CA', {minimumFractionDigits:2, maximumFractionDigits:2});
  const $ = id => document.getElementById(id);
  const money = value => CAD.format(Number(value) || 0);
  const percent = value => value == null ? '—' : (Number(value) * 100).toFixed(1) + '%';
  const FIELDS = {
    starting_portfolio: 'rt-portfolio',
    annual_spending: 'rt-spending',
    annual_retirement_income: 'rt-income',
    annual_contribution: 'rt-contribution',
    nominal_return: 'rt-return',
    annual_volatility: 'rt-volatility',
    inflation_rate: 'rt-inflation',
    effective_tax_rate: 'rt-tax',
  };
  const RATE_FIELDS = new Set(['nominal_return', 'annual_volatility', 'inflation_rate', 'effective_tax_rate']);
  const MONEY_FIELDS = new Set(['starting_portfolio', 'annual_spending', 'annual_retirement_income', 'annual_contribution']);
  const SCHEDULES = {
    income: {key:'income_streams', container:'rt-income-streams', empty:'rt-income-empty', amount:'annual_amount'},
    spending: {key:'spending_phases', container:'rt-spending-phases', empty:'rt-spending-empty', amount:'annual_amount'},
    event: {key:'one_time_events', container:'rt-one-time-events', empty:'rt-event-empty', amount:'amount'},
  };

  let live = null;
  let wired = false;
  let chart = null;
  let debounceTimer = null;
  let requestSequence = 0;
  let scheduleValues = {income:[], spending:[], event:[]};
  let benefitOverrides = {cppBasis:false, cppStart:false, oasBasis:false, oasStart:false};
  let lastForecastData = null;
  let chartHorizonYears = null;

  function formatDate(value, includeTime=false) {
    if (!value) return 'Unavailable';
    const parsed = new Date(includeTime ? value : value + 'T00:00:00');
    return includeTime
      ? parsed.toLocaleString('en-CA', {dateStyle:'medium', timeStyle:'short'})
      : parsed.toLocaleDateString('en-CA', {year:'numeric', month:'short', day:'numeric'});
  }

  function storedValue(key) {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      return value && typeof value === 'object' ? value : null;
    } catch (e) {
      return null;
    }
  }

  function loadSaved() {
    const current = storedValue(STORAGE_KEY);
    if (current) return current;
    const legacy = storedValue(LEGACY_STORAGE_KEY);
    if (!legacy) return null;
    const migrated = {
      ...legacy,
      annual_contribution: 0,
      date_of_birth: null,
      plan_through_age: 95,
      income_streams: [],
      spending_phases: [],
      one_time_events: [],
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
      localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch (e) { /* persistence is optional */ }
    return migrated;
  }

  function saveInputs(inputs) {
    const saved = {
      annual_retirement_income: inputs.annual_retirement_income,
      annual_contribution: inputs.annual_contribution,
      inflation_rate: inputs.inflation_rate,
      effective_tax_rate: inputs.effective_tax_rate,
      date_of_birth: inputs.date_of_birth,
      plan_through_age: inputs.plan_through_age,
      income_streams: inputs.income_streams,
      spending_phases: inputs.spending_phases,
      one_time_events: inputs.one_time_events,
    };
    if (!live || inputs.retirement_start_date !== live.defaults.retirement_start_date) {
      saved.retirement_start_date = inputs.retirement_start_date;
    }
    for (const name of ['starting_portfolio', 'annual_spending', 'nominal_return', 'annual_volatility']) {
      const tolerance = MONEY_FIELDS.has(name) ? .005 : .00005;
      if (!live || differs(inputs[name], live.defaults[name], tolerance)) saved[name] = inputs[name];
    }
    if (benefitOverrides.cppBasis && $('rt-cpp-basis').value.trim()) saved.cpp_basis_monthly = inputNumber($('rt-cpp-basis'));
    if (benefitOverrides.cppStart && $('rt-cpp-start').value) saved.cpp_start_date = $('rt-cpp-start').value;
    if (benefitOverrides.oasBasis && $('rt-oas-basis').value.trim()) saved.oas_basis_monthly = inputNumber($('rt-oas-basis'));
    if (benefitOverrides.oasStart && $('rt-oas-start').value) saved.oas_start_date = $('rt-oas-start').value;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(saved)); } catch (e) { /* optional */ }
  }

  function setInputs(values) {
    Object.entries(FIELDS).forEach(([name, id]) => {
      const value = values[name];
      $(id).value = value == null || !Number.isFinite(Number(value))
        ? ''
        : (RATE_FIELDS.has(name)
          ? (Number(value) * 100).toFixed(2)
          : MONEY_INPUT.format(Number(value)));
    });
    $('rt-birth-date').value = values.date_of_birth || '';
    $('rt-start-date').value = values.retirement_start_date || '';
    $('rt-plan-age').value = values.plan_through_age == null ? 95 : values.plan_through_age;
    benefitOverrides = {
      cppBasis:Object.prototype.hasOwnProperty.call(values, 'cpp_basis_monthly'),
      cppStart:Object.prototype.hasOwnProperty.call(values, 'cpp_start_date'),
      oasBasis:Object.prototype.hasOwnProperty.call(values, 'oas_basis_monthly'),
      oasStart:Object.prototype.hasOwnProperty.call(values, 'oas_start_date'),
    };
    const incomeBasis = live?.sources.retirement_income_basis;
    $('rt-cpp-basis').value = MONEY_INPUT.format(
      benefitOverrides.cppBasis ? values.cpp_basis_monthly : incomeBasis.cpp_average_monthly_at_65,
    );
    $('rt-oas-basis').value = MONEY_INPUT.format(
      benefitOverrides.oasBasis ? values.oas_basis_monthly : incomeBasis.oas_max_monthly_65_to_74,
    );
    $('rt-cpp-start').value = benefitOverrides.cppStart ? values.cpp_start_date : '';
    $('rt-oas-start').value = benefitOverrides.oasStart ? values.oas_start_date : '';
    setSchedules({
      income: values.income_streams || [],
      spending: values.spending_phases || [],
      event: values.one_time_events || [],
    });
  }

  function inputNumber(input) {
    return Number(input.value.trim().replace(/,/g, ''));
  }

  function formatMoneyInput(input) {
    const value = inputNumber(input);
    if (Number.isFinite(value)) input.value = MONEY_INPUT.format(value);
  }

  function readInputs() {
    const result = {};
    Object.entries(FIELDS).forEach(([name, id]) => {
      const raw = $(id).value.trim();
      const value = MONEY_FIELDS.has(name) ? inputNumber($(id)) : Number(raw);
      if (raw === '' || !Number.isFinite(value)) throw new Error('Complete every assumption with a valid number.');
      result[name] = value / (RATE_FIELDS.has(name) ? 100 : 1);
    });
    result.date_of_birth = $('rt-birth-date').value || null;
    result.retirement_start_date = $('rt-start-date').value;
    if (!result.retirement_start_date) throw new Error('Choose a retirement start date.');
    const planAge = Number($('rt-plan-age').value);
    if (!Number.isInteger(planAge) || planAge < 1 || planAge > 120) {
      throw new Error('Plan-through age must be a whole number from 1 to 120.');
    }
    result.plan_through_age = planAge;
    if (result.starting_portfolio < 0 || result.annual_spending < 0 ||
        result.annual_retirement_income < 0 || result.annual_contribution < 0) {
      throw new Error('Portfolio, spending, income, and contributions cannot be negative.');
    }
    if (result.inflation_rate < 0) throw new Error('Inflation cannot be negative in this forecast.');
    if (result.annual_volatility < 0) throw new Error('Volatility cannot be negative.');
    if (result.effective_tax_rate < 0 || result.effective_tax_rate >= 1) {
      throw new Error('Effective tax must be at least 0% and less than 100%.');
    }
    if (result.nominal_return - .02 <= -1) throw new Error('The conservative return must remain above -100%.');
    result.income_streams = readSchedule('income');
    result.spending_phases = readSchedule('spending');
    result.one_time_events = readSchedule('event');
    return result;
  }

  function differs(left, right, tolerance=1e-9) {
    if (left == null || right == null) return left !== right;
    return Math.abs(Number(left) - Number(right)) > tolerance;
  }

  function setSchedules(values) {
    for (const type of Object.keys(SCHEDULES)) {
      scheduleValues[type] = Array.isArray(values[type]) ? values[type].map(row => ({...row})) : [];
      renderSchedule(type);
    }
  }

  function addInput(fields, type, index, name, value, options={}) {
    const field = document.createElement('label');
    const caption = document.createElement('span');
    caption.className = 'schedule-field-label';
    caption.textContent = options.label || name;
    const input = document.createElement('input');
    input.dataset.field = name;
    input.setAttribute('aria-label', options.label || name);
    input.placeholder = options.placeholder || '';
    if (options.date) {
      input.type = 'date';
      input.value = value || '';
    } else if (options.money) {
      input.type = 'text';
      input.inputMode = 'decimal';
      const parsedValue = Number(String(value == null ? '' : value).replace(/,/g, ''));
      input.value = value === '' || value == null || !Number.isFinite(parsedValue)
        ? '' : MONEY_INPUT.format(parsedValue);
      input.addEventListener('blur', () => {
        formatMoneyInput(input);
        scheduleValues[type][index][name] = inputNumber(input);
      });
    } else {
      input.type = 'text';
      input.value = value || '';
    }
    if (options.wide) field.classList.add('wide');
    input.addEventListener('input', () => {
      scheduleValues[type][index][name] = input.value;
      scheduleForecast();
    });
    field.append(caption, input);
    fields.appendChild(field);
  }

  function renderSchedule(type) {
    const config = SCHEDULES[type];
    const container = $(config.container);
    container.replaceChildren();
    scheduleValues[type].forEach((row, index) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'schedule-row';
      const fields = document.createElement('div');
      fields.className = 'schedule-fields';
      addInput(fields, type, index, 'name', row.name, {wide:true, label:`${type} name`, placeholder:'Name'});
      addInput(fields, type, index, config.amount, row[config.amount], {
        wide:type !== 'event', money:true, label:type === 'event' ? 'Signed event amount' : 'Annual amount', placeholder:'Amount',
      });
      if (type === 'event') {
        addInput(fields, type, index, 'date', row.date, {date:true, label:'Event date'});
      } else {
        addInput(fields, type, index, 'start_date', row.start_date, {date:true, label:'Start date'});
        addInput(fields, type, index, 'end_date', row.end_date, {date:true, label:'End date (optional)'});
      }
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'schedule-remove';
      remove.setAttribute('aria-label', `Remove ${type} row`);
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        scheduleValues[type].splice(index, 1);
        renderSchedule(type);
        scheduleForecast();
      });
      wrapper.append(fields, remove);
      container.appendChild(wrapper);
    });
    $(config.empty).style.display = scheduleValues[type].length ? 'none' : 'block';
  }

  function addSchedule(type) {
    const start = $('rt-start-date').value || (live && live.defaults.retirement_start_date) || '';
    if (type === 'income') {
      scheduleValues.income.push({name:'Income stream', annual_amount:inputNumber($('rt-income')) || 0, start_date:start, end_date:null});
    } else if (type === 'spending') {
      scheduleValues.spending.push({name:'Spending phase', annual_amount:inputNumber($('rt-spending')) || 0, start_date:start, end_date:null});
    } else {
      scheduleValues.event.push({name:'One-time event', amount:0, date:start});
    }
    renderSchedule(type);
    const rows = $(SCHEDULES[type].container).querySelectorAll('.schedule-row');
    rows[rows.length - 1]?.querySelector('input')?.focus();
    scheduleForecast();
  }

  function readSchedule(type) {
    const config = SCHEDULES[type];
    return [...$(config.container).querySelectorAll('.schedule-row')].map((row, index) => {
      const get = name => row.querySelector(`[data-field="${name}"]`).value.trim();
      const amountRaw = get(config.amount);
      const amount = Number(amountRaw.replace(/,/g, ''));
      const label = `${type} row ${index + 1}`;
      if (!Number.isFinite(amount)) throw new Error(`${label} needs a valid amount.`);
      if (type !== 'event' && amount < 0) throw new Error(`${label} amount cannot be negative.`);
      const result = {name:get('name') || `${type} ${index + 1}`, [config.amount]:amount};
      if (scheduleValues[type][index]?.kind) result.kind = scheduleValues[type][index].kind;
      if (type === 'event') {
        result.date = get('date');
        if (!result.date) throw new Error(`${label} needs a date.`);
      } else {
        result.start_date = get('start_date');
        result.end_date = get('end_date') || null;
        if (!result.start_date) throw new Error(`${label} needs a start date.`);
        if (result.end_date && result.end_date <= result.start_date) {
          throw new Error(`${label} end date must be after its start date.`);
        }
      }
      return result;
    });
  }

  function dateAtAge(dateOfBirth, age) {
    const [year, month, day] = dateOfBirth.split('-').map(Number);
    const targetYear = year + age;
    const lastDay = new Date(Date.UTC(targetYear, month, 0)).getUTCDate();
    return `${targetYear}-${String(month).padStart(2, '0')}-${String(Math.min(day, lastDay)).padStart(2, '0')}`;
  }

  function clampBenefitStart(retirementStartDate, dateOfBirth, minimumAge, maximumAge) {
    const minimumDate = dateAtAge(dateOfBirth, minimumAge);
    const maximumDate = dateAtAge(dateOfBirth, maximumAge);
    if (retirementStartDate < minimumDate) return minimumDate;
    if (retirementStartDate > maximumDate) return maximumDate;
    return retirementStartDate;
  }

  function adjustmentMonths(dateOfBirth, startDate, standardAge) {
    const [birthYear, birthMonth, birthDay] = dateOfBirth.split('-').map(Number);
    const [startYear, startMonth, startDay] = startDate.split('-').map(Number);
    let elapsedMonths = (startYear - birthYear) * 12 + startMonth - birthMonth;
    const anniversaryDay = Math.min(
      birthDay, new Date(Date.UTC(startYear, startMonth, 0)).getUTCDate(),
    );
    if (startDay < anniversaryDay) elapsedMonths -= 1;
    return elapsedMonths - standardAge * 12;
  }

  function monthCount(value) {
    const absolute = Math.abs(value);
    return Number.isInteger(absolute) ? absolute.toFixed(0) : absolute.toFixed(1);
  }

  function ageAtDate(dateOfBirth, value) {
    if (!dateOfBirth || !isIsoDate(value) || value < dateOfBirth) return null;
    return adjustmentMonths(dateOfBirth, value, 0) / 12;
  }

  function renderAgeNote(id, dateOfBirth, value, label) {
    const age = ageAtDate(dateOfBirth, value);
    $(id).textContent = age == null ? '' : `${label}: ${age.toFixed(1)}`;
  }

  function isIsoDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!match) return false;
    const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return parsed.getUTCFullYear() === year
      && parsed.getUTCMonth() === month - 1
      && parsed.getUTCDate() === day;
  }

  function timingAdjustment(months, earlyRate, deferralRate) {
    if (Math.abs(months) < .001) return 'None (standard age)';
    if (months < 0) return `${monthCount(months)} mo early × −${percent(earlyRate)}/mo`;
    return `${monthCount(months)} mo deferred × +${percent(deferralRate)}/mo`;
  }

  function renderSourceStatus() {
    if (!live) return;
    renderIncomeSource();
    const messages = [];
    const source = live.sources;
    if (live.defaults.starting_portfolio == null) {
      messages.push('No cached portfolio is available; enter a starting value or refresh the Portfolio tab.');
    } else if (source.portfolio_stale_days != null && source.portfolio_stale_days >= 1) {
      messages.push(`The portfolio cache is ${source.portfolio_stale_days.toFixed(1)} days old.`);
    }
    try {
      const current = readInputs();
      if (differs(current.starting_portfolio, live.defaults.starting_portfolio, .005) ||
          differs(current.annual_spending, live.defaults.annual_spending, .005)) {
        messages.push('Using saved or edited portfolio/spending values; live source values remain above.');
      }
      if (differs(current.nominal_return, live.defaults.nominal_return, .00005)) {
        messages.push('Using a saved or edited growth rate instead of the portfolio-derived default.');
      }
      if (differs(current.annual_volatility, live.defaults.annual_volatility, .00005)) {
        messages.push('Using a saved or edited volatility instead of the portfolio-derived default.');
      }
      if (!current.date_of_birth) {
        messages.push('Add date of birth to use the selected plan-through age; until then the legacy 60-year horizon is retained.');
      }
      if (current.retirement_start_date !== live.defaults.retirement_start_date) {
        messages.push(`Retirement withdrawals are delayed until ${formatDate(current.retirement_start_date)}.`);
      }
    } catch (e) { /* forecast validation reports incomplete inputs */ }
    $('rt-source-status').textContent = messages.join(' ');
    $('rt-open-portfolio').style.display =
      live.defaults.starting_portfolio == null || (source.portfolio_stale_days != null && source.portfolio_stale_days >= 1)
        ? 'inline' : 'none';
  }

  function cppOasEstimate(dateOfBirth, cppStartDate, oasStartDate, cppBasis, oasBasis, basis) {
    const cppMonths = adjustmentMonths(dateOfBirth, cppStartDate, basis.cpp_standard_age);
    const cppRate = cppMonths < 0 ? basis.cpp_early_reduction_per_month : basis.cpp_deferral_increase_per_month;
    const cppMonthly = cppBasis * (1 + cppMonths * cppRate);
    const oasMonths = adjustmentMonths(dateOfBirth, oasStartDate, basis.oas_standard_age);
    const oasMonthly = oasBasis * (1 + oasMonths * basis.oas_deferral_increase_per_month);
    return {
      cppBasis, cppStartDate, cppMonths, cppRate, cppMonthly,
      oasBasis, oasStartDate, oasMonths, oasMonthly,
      annual:(cppMonthly + oasMonthly) * 12,
    };
  }

  function removeDuplicatedCppOasBaseIncome() {
    const generated = scheduleValues.income.filter(row => row.kind === 'cpp_oas_estimate');
    const duplicatedAnnual = generated.length
      ? generated.reduce((total, row) => total + Number(row.annual_amount || 0), 0)
      : Number(live?.defaults.annual_retirement_income);
    const baseAnnual = inputNumber($('rt-income'));
    if (Number.isFinite(duplicatedAnnual) && Number.isFinite(baseAnnual)
        && !differs(baseAnnual, duplicatedAnnual, .005)) {
      $('rt-income').value = MONEY_INPUT.format(0);
    }
  }

  function applyCppOasEstimate(estimate) {
    // Older saved values and live defaults put the same CPP/OAS estimate in the
    // undated base field. Remove that duplicate before adding the dated streams.
    removeDuplicatedCppOasBaseIncome();
    const otherIncome = scheduleValues.income.filter(row => row.kind !== 'cpp_oas_estimate');
    scheduleValues.income = [
      ...otherIncome,
      {
        kind:'cpp_oas_estimate', name:'CPP estimate', annual_amount:estimate.cppMonthly * 12,
        start_date:estimate.cppStartDate, end_date:null,
      },
      {
        kind:'cpp_oas_estimate', name:'OAS estimate', annual_amount:estimate.oasMonthly * 12,
        start_date:estimate.oasStartDate, end_date:null,
      },
    ];
    renderSchedule('income');
    scheduleForecast();
  }

  function clearBenefitOutputs() {
    for (const id of ['rt-cpp-adjustment', 'rt-cpp-monthly', 'rt-oas-adjustment', 'rt-oas-monthly', 'rt-benefit-annual']) {
      $(id).textContent = '—';
    }
  }

  function renderIncomeSource() {
    const basis = live.sources.retirement_income_basis;
    const dateOfBirth = $('rt-birth-date').value || null;
    const retirementStartDate = $('rt-start-date').value || null;
    const cppBasisRaw = $('rt-cpp-basis').value.trim();
    const oasBasisRaw = $('rt-oas-basis').value.trim();
    const cppBasis = inputNumber($('rt-cpp-basis'));
    const oasBasis = inputNumber($('rt-oas-basis'));
    renderAgeNote('rt-retirement-age-note', dateOfBirth, retirementStartDate, 'Retirement age');
    if (!dateOfBirth || !retirementStartDate || retirementStartDate < dateOfBirth) {
      if (!benefitOverrides.cppStart) $('rt-cpp-start').value = '';
      if (!benefitOverrides.oasStart) $('rt-oas-start').value = '';
      $('rt-cpp-start-age').textContent = '';
      $('rt-oas-start-age').textContent = '';
      $('rt-income-source').textContent = 'Add a date of birth to calculate benefit timing and adjustments.';
      clearBenefitOutputs();
      return null;
    }

    const cppMinimum = dateAtAge(dateOfBirth, basis.cpp_min_start_age);
    const cppMaximum = dateAtAge(dateOfBirth, basis.cpp_max_start_age);
    const oasMinimum = dateAtAge(dateOfBirth, basis.oas_standard_age);
    const oasMaximum = dateAtAge(dateOfBirth, basis.oas_max_start_age);
    if (!benefitOverrides.cppStart) {
      $('rt-cpp-start').value = clampBenefitStart(
        retirementStartDate, dateOfBirth, basis.cpp_min_start_age, basis.cpp_max_start_age,
      );
    }
    if (!benefitOverrides.oasStart) {
      $('rt-oas-start').value = clampBenefitStart(
        retirementStartDate, dateOfBirth, basis.oas_standard_age, basis.oas_max_start_age,
      );
    }
    const cppStartDate = $('rt-cpp-start').value;
    const oasStartDate = $('rt-oas-start').value;
    renderAgeNote('rt-cpp-start-age', dateOfBirth, cppStartDate, 'Payout age');
    renderAgeNote('rt-oas-start-age', dateOfBirth, oasStartDate, 'Payout age');
    if (!cppBasisRaw || !oasBasisRaw || !Number.isFinite(cppBasis) || cppBasis < 0 || !Number.isFinite(oasBasis) || oasBasis < 0) {
      $('rt-income-source').textContent = 'Enter non-negative monthly basis amounts for CPP and OAS.';
      clearBenefitOutputs();
      return null;
    }
    if (!isIsoDate(cppStartDate) || !isIsoDate(oasStartDate)) {
      $('rt-income-source').textContent = 'Enter benefit start dates as YYYY-MM-DD.';
      clearBenefitOutputs();
      return null;
    }
    if (cppStartDate < cppMinimum || cppStartDate > cppMaximum) {
      $('rt-income-source').textContent = 'CPP must start between the 60th and 70th birthdays.';
      clearBenefitOutputs();
      return null;
    }
    if (oasStartDate < oasMinimum || oasStartDate > oasMaximum) {
      $('rt-income-source').textContent = 'OAS must start between the 65th and 70th birthdays.';
      clearBenefitOutputs();
      return null;
    }

    const estimate = cppOasEstimate(
      dateOfBirth, cppStartDate, oasStartDate, cppBasis, oasBasis, basis,
    );
    $('rt-income-source').textContent = 'Adjust the monthly bases or start dates; dated income streams update automatically.';
    $('rt-cpp-adjustment').textContent = timingAdjustment(
      estimate.cppMonths, basis.cpp_early_reduction_per_month, basis.cpp_deferral_increase_per_month,
    );
    $('rt-cpp-monthly').textContent = `${money(estimate.cppMonthly)}/mo`;
    $('rt-oas-adjustment').textContent = timingAdjustment(
      estimate.oasMonths, 0, basis.oas_deferral_increase_per_month,
    );
    $('rt-oas-monthly').textContent = `${money(estimate.oasMonthly)}/mo`;
    $('rt-benefit-annual').textContent = `${money(estimate.annual)}/yr`;
    return estimate;
  }

  function syncCppOasForecast() {
    const estimate = renderIncomeSource();
    if (estimate) {
      applyCppOasEstimate(estimate);
      return;
    }
    if (!$('rt-birth-date').value) {
      const retained = scheduleValues.income.filter(row => row.kind !== 'cpp_oas_estimate');
      if (retained.length !== scheduleValues.income.length) {
        scheduleValues.income = retained;
        renderSchedule('income');
      }
    }
    scheduleForecast();
  }

  function renderContributionSource() {
    const defaults = live.defaults, source = live.sources;
    if (source.average_savings_rate == null) {
      $('rt-contribution-source').textContent =
        'No income history is available yet, so pre-retirement contributions default to $0.';
      return;
    }
    const months = source.savings_rate_window_months;
    $('rt-contribution-source').textContent =
      `Defaults to a ${percent(source.average_savings_rate)} average savings rate over the last `
      + `${months} month${months === 1 ? '' : 's'} × ${money(source.annualized_income)} annualized income `
      + `= ${money(defaults.annual_contribution)}/yr.`;
  }

  function renderSources() {
    const defaults = live.defaults, source = live.sources;
    const taxExempt = live.tax_exempt_portfolio;
    $('rt-source-portfolio').textContent = defaults.starting_portfolio == null ? '—' : money(defaults.starting_portfolio);
    $('rt-source-ytd').textContent = money(source.ytd_spending);
    $('rt-source-annualized').textContent = money(defaults.annual_spending);
    $('rt-portfolio-asof').textContent = source.portfolio_as_of
      ? `As of ${formatDate(source.portfolio_as_of)}` : 'No portfolio cache found';
    if (source.portfolio_generated_at) $('rt-portfolio-asof').title = `Generated ${formatDate(source.portfolio_generated_at, true)}`;
    $('rt-budget-asof').textContent = `Through ${formatDate(source.budget_as_of)}`;
    $('rt-annualization-note').textContent = `${source.elapsed_days} of ${source.days_in_year} calendar days`;
    $('rt-start-date').min = defaults.retirement_start_date;
    $('rt-birth-date').max = defaults.retirement_start_date;
    $('rt-growth-source').textContent = source.nominal_return_source.startsWith('portfolio')
      ? `Expected growth defaults to ${percent(defaults.nominal_return)}, matching the Portfolio since-inception annualized money-weighted return.`
      : `Since-inception portfolio growth is unavailable, so expected growth defaults to ${percent(defaults.nominal_return)}.`;
    $('rt-volatility-source').textContent = source.volatility_source.startsWith('portfolio')
      ? `Volatility defaults to ${percent(defaults.annual_volatility)} from Portfolio risk data.`
      : `Portfolio volatility is unavailable, so volatility defaults to ${percent(defaults.annual_volatility)}.`;
    $('rt-tax-exempt-total').textContent = taxExempt.total_value == null ? '—' : money(taxExempt.total_value);
    $('rt-tax-exempt-share').textContent = taxExempt.total_value == null
      ? 'No portfolio cache found'
      : `${percent(taxExempt.share_of_portfolio)} of portfolio · TFSA + non-registered cost base`;
    $('rt-tax-exempt-card').title = taxExempt.total_value == null ? ''
      : `TFSA ${money(taxExempt.tfsa_value)} + non-registered cost base ${money(taxExempt.non_registered_cost_base)}. Informational only; this does not change the forecast tax assumption.`;
    renderContributionSource();
    renderSourceStatus();
  }

  function runwayText(months, data) {
    if (months == null) {
      return data.plan_through_age == null
        ? `${Math.round(data.horizon_years)}+ years`
        : `Through age ${data.plan_through_age}`;
    }
    if (months === 0) return '0 months';
    const years = Math.floor(months / 12), remainder = months % 12;
    if (!years) return `${remainder} month${remainder === 1 ? '' : 's'}`;
    if (!remainder) return `${years} year${years === 1 ? '' : 's'}`;
    return `${years}y ${remainder}m`;
  }

  function renderScenario(scenario, data) {
    const prefix = 'rt-' + scenario.key;
    $(prefix + '-runway').textContent = runwayText(scenario.depletion_months, data);
    $(prefix + '-return').textContent = `${percent(scenario.nominal_return)} nominal · ${percent(scenario.real_return)} real`;
    $(prefix + '-note').textContent = scenario.depletion_months == null
      ? `${money(scenario.retirement_start_balance)} at retirement · ${money(scenario.ending_balance)} remains on ${formatDate(data.plan_end_date)}`
      : `${money(scenario.retirement_start_balance)} at retirement · reaches zero around ${formatDate(scenario.depletion_date)}`;
  }

  function elapsedYears(start, end) {
    return (new Date(end + 'T00:00:00') - new Date(start + 'T00:00:00')) / (365.2425 * 86400000);
  }

  const retirementMarkerPlugin = {
    id:'retirementMarker',
    afterDatasetsDraw(chartInstance, args, options) {
      if (!Number.isFinite(options.xValue)) return;
      const {ctx, chartArea, scales} = chartInstance;
      const x = scales.x.getPixelForValue(options.xValue);
      if (x < chartArea.left || x > chartArea.right) return;
      ctx.save();
      ctx.strokeStyle = options.color;
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
      ctx.setLineDash([2, 5]);
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = '600 11px system-ui, sans-serif';
      ctx.textBaseline = 'top';
      const padding = 5;
      const labelWidth = ctx.measureText(options.label).width + padding * 2;
      const labelX = Math.min(Math.max(x + 6, chartArea.left), chartArea.right - labelWidth);
      const labelY = chartArea.top + 6;
      ctx.fillStyle = 'rgba(19, 25, 37, .92)';
      ctx.fillRect(labelX, labelY, labelWidth, 21);
      ctx.fillStyle = options.color;
      ctx.fillText(options.label, labelX + padding, labelY + 4);
      ctx.restore();
    },
  };

  function renderZoomLabel(fullHorizonYears) {
    $('rt-chart-zoom-label').textContent = chartHorizonYears >= fullHorizonYears
      ? `Full · ${Math.round(fullHorizonYears)}y`
      : `First ${chartHorizonYears}y`;
  }

  function nearestBalanceRow(rows, startDate, targetYears) {
    let best = rows[0];
    let bestDiff = Infinity;
    for (const row of rows) {
      const diff = Math.abs(elapsedYears(startDate, row.date) - targetYears);
      if (diff < bestDiff) { bestDiff = diff; best = row; }
    }
    return best;
  }

  function updateEndingBalance(data, targetYears) {
    const rows = data.probabilistic.balance_percentiles;
    if (!rows.length) return;
    const row = nearestBalanceRow(rows, data.start_date, targetYears);
    renderPercentiles('rt-ending', row, money);
    $('rt-ending-note').textContent =
      `As of ${formatDate(row.date)}${row.age == null ? '' : ' · age ' + row.age.toFixed(1)}`;
  }

  function renderChart(data) {
    if (chart) chart.destroy();
    const rows = data.probabilistic.balance_percentiles;
    const fullHorizonYears = rows.length ? elapsedYears(data.start_date, rows[rows.length - 1].date) : 0;
    const zoomSlider = $('rt-chart-zoom');
    zoomSlider.max = Math.max(2, Math.ceil(fullHorizonYears));
    if (chartHorizonYears == null || chartHorizonYears > fullHorizonYears) chartHorizonYears = fullHorizonYears;
    zoomSlider.value = chartHorizonYears;
    renderZoomLabel(fullHorizonYears);
    updateEndingBalance(data, chartHorizonYears);
    const retirementOffset = elapsedYears(data.start_date, data.retirement_start_date);
    const retirementAge = ageAtDate($('rt-birth-date').value, data.retirement_start_date);
    const retirementLabel = `Retirement · ${formatDate(data.retirement_start_date)}`
      + (retirementAge == null ? '' : ` (Age: ${Math.round(retirementAge)})`);
    const markerColor = getComputedStyle(document.documentElement).getPropertyValue('--red').trim() || '#ff6b6b';
    const baseAge = rows.length ? rows[0].age : null;
    const definitions = [
      {key:'p10', label:'P10', color:'#f6c85f', dash:[6, 4]},
      {key:'p50', label:'Median', color:'#6c8cff', dash:[]},
      {key:'p90', label:'P90', color:'#42d6a4', dash:[6, 4]},
    ];
    chart = new Chart($('rt-chart'), {
      type:'line',
      data:{datasets:definitions.map(definition => ({
        label:definition.label,
        data:rows.map(row => ({
          x:elapsedYears(data.start_date, row.date), y:row[definition.key], date:row.date, age:row.age,
        })),
        borderColor:definition.color,
        backgroundColor:definition.color,
        borderDash:definition.dash,
        borderWidth:definition.key === 'p50' ? 3 : 2,
        pointRadius:0, pointHitRadius:20, pointHoverRadius:5, tension:.18, parsing:false,
      }))},
      plugins:[retirementMarkerPlugin],
      options:{
        responsive:true, maintainAspectRatio:false,
        interaction:{mode:'nearest', axis:'x', intersect:false},
        plugins:{
          retirementMarker:{xValue:retirementOffset, label:retirementLabel, color:markerColor},
          legend:{labels:{boxWidth:10, usePointStyle:true}},
          tooltip:{callbacks:{
            title:items => {
              if (!items.length) return '';
              const raw = items[0].raw;
              return raw.age == null ? formatDate(raw.date) : `${formatDate(raw.date)} · age ${raw.age.toFixed(1)}`;
            },
            label:context => `${context.dataset.label}: ${money(context.parsed.y)}`,
          }},
        },
        scales:{
          x:{
            type:'linear', min:0, max:chartHorizonYears,
            title:{display:true, text: baseAge == null ? 'Years from forecast start' : 'Age'},
            grid:{display:false},
            ticks:{callback:value => baseAge == null ? value + 'y' : Math.round(baseAge + value)},
          },
          y:{beginAtZero:true, grid:{color:'rgba(41,51,72,.65)'}, ticks:{callback:value => money(value)}},
        },
      },
    });
    $('rt-chart').setAttribute(
      'aria-label',
      `Monte Carlo portfolio range. A red dotted line marks retirement on ${formatDate(data.retirement_start_date)}.`,
    );
  }

  function renderPercentiles(prefix, values, formatter) {
    for (const key of ['p10', 'p50', 'p90']) {
      $(prefix + '-' + key).textContent = values == null ? '—' : formatter(values[key]);
    }
  }

  function renderForecast(data) {
    lastForecastData = data;
    const spending = data.summary.annual_spending_at_retirement;
    const income = data.summary.annual_income_at_retirement;
    $('rt-withdrawal-basis').textContent = spending != null && income != null
      ? `At retirement (${formatDate(data.retirement_start_date)}): ${money(spending)}/yr spending minus ${money(income)}/yr income, including CPP/OAS payable by that date. Benefits starting later reduce future draws from their start dates.`
      : `At retirement (${formatDate(data.retirement_start_date)}), using income active on that date. Restart run.bat and reload to show the spending and income breakdown.`;
    $('rt-net-gap').textContent = money(data.summary.net_spending_gap);
    $('rt-gross-draw').textContent = money(data.summary.gross_annual_draw);
    $('rt-withdrawal-rate').textContent = percent(data.summary.initial_withdrawal_rate);
    const results = data.probabilistic;
    $('rt-success-probability').textContent = percent(results.success_probability);
    $('rt-success-note').textContent = `${results.simulations.toLocaleString('en-CA')} paths · positive through ${formatDate(data.plan_end_date)}`;
    renderPercentiles('rt-depletion', results.depletion_age_percentiles, value => Number(value).toFixed(1));
    $('rt-depletion-note').textContent = results.depletion_age_percentiles == null
      ? (data.plan_through_age == null ? 'Add date of birth to calculate depletion ages.' : 'No paths depleted before the plan horizon.')
      : 'Calculated only among paths that depleted.';
    $('rt-chart-note').textContent =
      `Through ${formatDate(data.plan_end_date)} · retirement ${formatDate(data.retirement_start_date)} · today's CAD`;
    data.scenarios.forEach(scenario => renderScenario(scenario, data));
    renderChart(data);
  }

  async function forecast() {
    clearTimeout(debounceTimer);
    let inputs;
    try {
      inputs = readInputs();
    } catch (e) {
      $('rt-error').textContent = e.message;
      renderSourceStatus();
      return;
    }
    saveInputs(inputs);
    renderSourceStatus();
    const sequence = ++requestSequence;
    try {
      const response = await fetch('/api/retirement/forecast', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(inputs),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to calculate the forecast.');
      if (!data.probabilistic) {
        throw new Error('The running finance server predates the probabilistic planner. Close it, restart NOFACE, then reload this page.');
      }
      if (sequence !== requestSequence) return;
      $('rt-error').textContent = '';
      renderForecast(data);
    } catch (e) {
      if (sequence === requestSequence) $('rt-error').textContent = e.message;
    }
  }

  function scheduleForecast() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(forecast, 180);
  }

  function wire() {
    $('rt-form').addEventListener('submit', event => event.preventDefault());
    $('rt-form').querySelectorAll('input').forEach(input => input.addEventListener('input', scheduleForecast));
    for (const id of ['rt-birth-date', 'rt-start-date']) {
      $(id).addEventListener('input', syncCppOasForecast);
    }
    $('rt-form').querySelectorAll('input[data-money]').forEach(input => {
      input.addEventListener('blur', () => formatMoneyInput(input));
    });
    document.querySelectorAll('[data-benefit-field]').forEach(input => {
      const field = input.dataset.benefitField;
      if (field.endsWith('Start')) {
        input.addEventListener('change', () => {
          benefitOverrides[field] = true;
          syncCppOasForecast();
        });
        input.addEventListener('blur', () => {
          if (!input.value) benefitOverrides[field] = false;
          syncCppOasForecast();
        });
        return;
      }
      input.addEventListener('input', () => {
        benefitOverrides[field] = true;
        syncCppOasForecast();
      });
    });
    for (const id of ['rt-cpp-basis', 'rt-oas-basis']) {
      $(id).addEventListener('blur', () => formatMoneyInput($(id)));
    }
    document.querySelectorAll('[data-add-schedule]').forEach(button => {
      button.addEventListener('click', () => addSchedule(button.dataset.addSchedule));
    });
    $('rt-reset').addEventListener('click', () => {
      if (!live) return;
      try {
        localStorage.removeItem(STORAGE_KEY);
        localStorage.removeItem(LEGACY_STORAGE_KEY);
      } catch (e) { /* optional */ }
      setInputs(live.defaults);
      renderSourceStatus();
      syncCppOasForecast();
    });
    $('rt-open-portfolio').addEventListener('click', () => { location.hash = 'portfolio'; });
    $('rt-chart-zoom').addEventListener('input', event => {
      chartHorizonYears = Number(event.target.value);
      if (lastForecastData) renderChart(lastForecastData);
    });
  }

  async function init() {
    if (!wired) { wire(); wired = true; }
    try {
      const response = await fetch('/api/retirement/defaults');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to load retirement source data.');
      if (data.defaults.annual_volatility == null) {
        throw new Error('The running finance server is out of date. Close it, restart NOFACE, then reload this page.');
      }
      live = data;
      renderSources();
      const saved = loadSaved() || {};
      if (saved.retirement_start_date && saved.retirement_start_date < data.defaults.retirement_start_date) {
        delete saved.retirement_start_date;
      }
      setInputs({...data.defaults, ...saved});
      renderSourceStatus();
      syncCppOasForecast();
    } catch (e) {
      $('rt-error').textContent = e.message;
    }
  }

  return {init};
})();
