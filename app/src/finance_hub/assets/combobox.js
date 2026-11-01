/* Type-to-filter picker for any <input data-combo="<datalist id>">.
 * The browser draws a native <input list> popup itself, outside the page's
 * CSS, so it can't match the hub's dark theme. Inputs instead name their
 * <datalist> in data-combo (no list attribute, so no native popup) and this
 * draws the suggestions as a themed menu. Picking fires bubbling input and
 * change events, so callers handle it exactly like a typed value.
 */
(() => {
  const menu = document.createElement('div');
  menu.className = 'combo-menu';
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  document.body.appendChild(menu);
  let input = null;
  let matches = [];
  let active = -1;
  let picking = false;
  let picked = null;

  function options(el) {
    const list = document.getElementById(el.dataset.combo);
    return list ? Array.from(list.options, option => option.value) : [];
  }

  function place() {
    const rect = input.getBoundingClientRect();
    menu.style.minWidth = rect.width + 'px';
    menu.style.left = rect.left + 'px';
    // Open upward when there's no room below (e.g. the last table row).
    const below = innerHeight - rect.bottom;
    if (below < 180 && rect.top > below) {
      menu.style.top = '';
      menu.style.bottom = (innerHeight - rect.top + 4) + 'px';
    } else {
      menu.style.bottom = '';
      menu.style.top = (rect.bottom + 4) + 'px';
    }
  }

  function highlight(index) {
    active = index;
    menu.querySelectorAll('.combo-item').forEach((item, i) => {
      item.classList.toggle('active', i === active);
      if (i === active) item.scrollIntoView({block: 'nearest'});
    });
  }

  // showAll: on focus the box holds the current (selected) name, so list
  // every option rather than only the one that matches it.
  function open(el, showAll) {
    input = el;
    const query = showAll ? '' : el.value.trim().toLowerCase();
    matches = options(el).filter(name => name.toLowerCase().includes(query));
    menu.replaceChildren(...(matches.length ? matches.map((name, i) => {
      const item = document.createElement('div');
      item.className = 'combo-item';
      item.setAttribute('role', 'option');
      item.dataset.index = i;
      item.textContent = name;
      return item;
    }) : [Object.assign(document.createElement('div'), {className: 'combo-empty', textContent: 'No matching accounts'})]));
    menu.hidden = false;
    place();
    const current = matches.indexOf(el.value);
    highlight(current >= 0 ? current : (showAll || !matches.length ? -1 : 0));
  }

  function close() {
    menu.hidden = true;
    input = null;
    active = -1;
  }

  function pick(index) {
    const el = input;
    close();
    if (!el || !el.isConnected || index < 0 || index >= matches.length) return;
    el.value = matches[index];
    // Our own input event mustn't reopen the menu.
    picking = true;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    picking = false;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    picked = {el, value: el.value};
  }

  document.addEventListener('focusin', event => {
    const el = event.target;
    if (!(el instanceof HTMLInputElement) || !el.dataset.combo) return;
    picked = null;
    // Select the current name so typing replaces it.
    el.select();
    open(el, true);
  });
  document.addEventListener('focusout', event => {
    if (event.target === input) close();
  });
  document.addEventListener('input', event => {
    if (!(event.target instanceof HTMLInputElement) || !event.target.dataset.combo || picking) return;
    picked = null;
    open(event.target, false);
  });
  // A pick already fired change. If the user had typed first, the browser
  // fires change again on blur; drop that repeat so a Review row isn't
  // categorized twice.
  window.addEventListener('change', event => {
    if (event.isTrusted && picked && event.target === picked.el && event.target.value === picked.value) {
      event.stopImmediatePropagation();
      picked = null;
    }
  }, true);
  // Capture phase on window so Escape closes the menu before view-level
  // handlers (the Budget drawer's Escape cancels the edit) see it.
  window.addEventListener('keydown', event => {
    if (!input || event.target !== input || menu.hidden) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!matches.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      highlight(active < 0 ? (step > 0 ? 0 : matches.length - 1) : (active + step + matches.length) % matches.length);
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      event.stopPropagation();
      pick(active);
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      close();
    } else if (event.key === 'Tab') {
      close();
    }
  }, true);
  // mousedown + preventDefault keeps focus in the input, so focusout doesn't
  // close the menu before the click lands.
  menu.addEventListener('mousedown', event => {
    event.preventDefault();
    const item = event.target.closest('.combo-item');
    if (item) pick(+item.dataset.index);
  });
  menu.addEventListener('mousemove', event => {
    const item = event.target.closest('.combo-item');
    if (item && +item.dataset.index !== active) highlight(+item.dataset.index);
  });
  // Scrolling would leave the fixed menu floating away from its box.
  // A removed input doesn't fire focusout, so also close on any outside press.
  document.addEventListener('mousedown', event => {
    if (input && event.target !== input && !menu.contains(event.target)) close();
  });
  window.addEventListener('scroll', event => {
    if (input && event.target !== menu) close();
  }, true);
  window.addEventListener('resize', close);
})();
