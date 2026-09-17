(() => {
  // Theme injects window.MEWBO_VERSIONS_ROOT from `theme.versions_root` in
  // mkdocs.yml when the docs are served from a subpath (e.g. `/your-docs`
  // on GitHub Pages). Empty/unset = served at the domain root.
  const VERSIONS_ROOT = (window.MEWBO_VERSIONS_ROOT || '').replace(/^\/+|\/+$/g, '');

  function getPathInfo() {
    const parts = window.location.pathname.split('/').filter(Boolean);
    let root = '';
    let versionIndex = 0;
    if (VERSIONS_ROOT && parts[0] === VERSIONS_ROOT) {
      root = '/' + VERSIONS_ROOT;
      versionIndex = 1;
    }
    const version = parts[versionIndex] || 'latest';
    const pageParts = parts.slice(versionIndex + 1);
    return { root, version, pageParts };
  }

  function uniqByValue(items) {
    const seen = new Set();
    return items.filter((item) => {
      if (seen.has(item.value)) {
        return false;
      }
      seen.add(item.value);
      return true;
    });
  }

  function buildOptions(versions, currentVersion) {
    const items = [];
    for (const entry of versions) {
      if (entry.aliases && entry.aliases.length) {
        for (const alias of entry.aliases) {
          items.push({ value: alias, label: alias === 'latest' ? 'latest' : alias });
        }
      }
      items.push({ value: entry.version, label: entry.title || entry.version });
    }

    const unique = uniqByValue(items);
    unique.sort((a, b) => {
      if (a.value === 'latest') return -1;
      if (b.value === 'latest') return 1;
      if (a.value === 'main') return -1;
      if (b.value === 'main') return 1;
      if (a.value === currentVersion) return -1;
      if (b.value === currentVersion) return 1;
      return a.label.localeCompare(b.label);
    });
    return unique;
  }

  function createSwitcher(versions, current) {
    const container = document.createElement('div');
    container.id = 'version-switcher';
    // `.ms-header-select` carries the shared geometry — see mewbo.css. The
    // sizing used to live in Tailwind classes here, which is why this control
    // and the mike one (a bare <select>) never matched: two spellings of the
    // same thing, only one of them maintained.
    container.className = 'ms-header-select hidden md:flex';

    // A text label beside a dropdown that already says "latest" is a second
    // word for the same fact, and only THIS switcher had one — so the header
    // read as two unrelated controls. The icon says the same thing in the
    // space the row actually has, and matches how the branch picker beside it
    // is marked.
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('fill', 'none');
    icon.setAttribute('stroke', 'currentColor');
    icon.setAttribute('stroke-width', '2');
    icon.setAttribute('stroke-linecap', 'round');
    icon.setAttribute('stroke-linejoin', 'round');
    icon.setAttribute('aria-hidden', 'true');
    icon.classList.add('ms-header-select__icon');
    // lucide:history — a clock with a rewind arrow. Versions are points in
    // time, and a tag icon would collide with the branch picker's meaning.
    for (const d of [
      'M3 3v5h5',
      'M3.05 13A9 9 0 1 0 6 5.3L3 8',
      'M12 7v5l4 2',
    ]) {
      const path = document.createElementNS(
        'http://www.w3.org/2000/svg',
        'path',
      );
      path.setAttribute('d', d);
      icon.appendChild(path);
    }

    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Documentation version');

    for (const item of versions) {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      if (item.value === current) {
        option.selected = true;
      }
      select.appendChild(option);
    }

    select.addEventListener('change', () => {
      const { root, pageParts } = getPathInfo();
      const selected = select.value;
      const pageSuffix = pageParts.length ? `${pageParts.join('/')}/` : '';
      const rootPrefix = root || '';
      const target = `${rootPrefix}/${selected}/${pageSuffix}`.replace(/\/+/g, '/');
      window.location.assign(target);
    });

    container.appendChild(icon);
    container.appendChild(select);
    return container;
  }

  async function injectSwitcher() {
    const headerActions = document.querySelector('header .ml-auto');
    if (!headerActions || document.getElementById('version-switcher')) {
      return;
    }

    const { root, version } = getPathInfo();
    const versionsUrl = `${root || ''}/versions.json`;
    try {
      const response = await fetch(versionsUrl, { cache: 'no-store' });
      if (!response.ok) {
        return;
      }
      const versions = await response.json();
      const options = buildOptions(versions, version);
      if (!options.length) {
        return;
      }
      const switcher = createSwitcher(options, version);
      headerActions.prepend(switcher);
    } catch (error) {
      console.warn('Version switcher disabled:', error);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectSwitcher);
  } else {
    injectSwitcher();
  }
})();
