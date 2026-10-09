/* Top-level hub shell: switches between the Budget-plan / Budget / Portfolio /
 * Retirement / Journal views. Each data view is loaded when it is shown so a
 * Journal post or Portfolio refresh is reflected when the user returns.
 */
(() => {
  const NAV = document.getElementById('hub-nav');
  const VIEWS = ['budget-plan', 'budget', 'portfolio', 'retirement', 'journal'];
  const HEARTBEAT_INTERVAL_MS = 15000;
  let activeView = null;

  setInterval(() => {
    fetch('/api/heartbeat', {method: 'POST', keepalive: true}).catch(() => {});
  }, HEARTBEAT_INTERVAL_MS);

  function activate(view) {
    activeView = view;
    NAV.querySelectorAll('button').forEach(button => {
      button.classList.toggle('active', button.dataset.view === view);
    });
    VIEWS.forEach(candidate => {
      const section = document.getElementById('view-' + candidate);
      if (section) section.classList.toggle('active', candidate === view);
    });
    localStorage.setItem('financeHub.tab', view);
    if (view === 'budget') window.HubBudget.init();
    else if (view === 'budget-plan') window.HubBudgetPlan.init();
    else if (view === 'portfolio') window.HubPortfolio.init();
    else if (view === 'retirement') window.HubRetirement.init();
  }

  function canLeaveBudgetPlan(nextView) {
    if (activeView !== 'budget-plan' || nextView === 'budget-plan') return true;
    const controller = window.HubBudgetPlan;
    if (controller && typeof controller.isSaving === 'function' && controller.isSaving()) {
      return false;
    }
    if (!controller || !controller.hasUnsavedChanges()) return true;
    if (!confirm('Discard unsaved budget changes?')) return false;
    // Confirmation must discard the in-memory edits before navigation. This
    // also invalidates any late save response from an abandoned edit session.
    if (typeof controller.discardChanges === 'function') controller.discardChanges();
    return true;
  }

  function tryNavigate(view) {
    if (!canLeaveBudgetPlan(view)) return;
    if (location.hash.replace('#', '') === view) {
      activate(view);
    } else {
      location.hash = view;
    }
  }

  NAV.querySelectorAll('button').forEach(button => {
    button.addEventListener('click', () => tryNavigate(button.dataset.view));
  });

  window.addEventListener('hashchange', () => {
    const view = location.hash.replace('#', '');
    if (!VIEWS.includes(view)) return;
    if (activeView && view !== activeView && !canLeaveBudgetPlan(view)) {
      // Replace the hash without another hashchange event, preserving the
      // visible tab when the user cancels browser back/forward navigation.
      history.replaceState(null, '', '#' + activeView);
      return;
    }
    activate(view);
  });

  const requested = location.hash.replace('#', '') || localStorage.getItem('financeHub.tab') || 'budget';
  const initial = VIEWS.includes(requested) ? requested : 'budget';
  if (location.hash.replace('#', '') === initial) activate(initial);
  else location.hash = initial;
})();
