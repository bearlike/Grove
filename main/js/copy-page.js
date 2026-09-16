(() => {
  const COPY_STATE = 'data-copy-state';

  async function writeClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    document.body.removeChild(textarea);
    if (!copied) {
      throw new Error('Clipboard copy was rejected');
    }
  }

  function findCopyButton() {
    return document.querySelector('#page-header [data-copy-markdown]');
  }

  function setState(button, state) {
    button.setAttribute(COPY_STATE, state);
    const label = state === 'copied' ? button.dataset.copiedLabel
      : state === 'failed' ? button.dataset.copyFailedLabel : button.dataset.copyLabel;
    if (label) {
      const span = button.querySelector('span');
      if (span) span.textContent = label;
      button.setAttribute('aria-label', label);
    }
    // Both 'copied' and 'failed' are transient acknowledgements, not modes.
    // Without this the button keeps saying "Copied" forever and a second copy
    // gives no feedback at all, because the label never changed back.
    if (state !== 'ready') {
      clearTimeout(button._copyResetTimer);
      button._copyResetTimer = setTimeout(() => setState(button, 'ready'), 2000);
    }
  }

  function bindCopyHandler(button) {
    setState(button, 'ready');
    button.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        const markdown = button.dataset.copyMarkdown;
        if (markdown === undefined) {
          throw new Error('No page Markdown was emitted');
        }
        await writeClipboard(markdown);
        setState(button, 'copied');
      } catch (error) {
        setState(button, 'failed');
        console.warn('Copy page markdown failed:', error);
      }
    });
  }

  /* The disclosure menu beside the copy button. Only present when the site
     sets `repo_url` — every item in it needs a URL for the page source — so
     everything here no-ops on a site without one. */
  function bindMenu(root, copyButton) {
    const toggle = root.querySelector('[data-mewbo-page-actions-toggle]');
    const menu = root.querySelector('[data-mewbo-page-actions-menu]');
    if (!toggle || !menu) return;

    // Anchor to the control in every layout, then clamp to the visible
    // viewport. Switching to fixed with top:100% put mobile menus below it.
    const positionMenu = () => {
      if (menu.hidden) return;
      const viewport = window.visualViewport;
      const left = viewport ? viewport.offsetLeft : 0;
      const top = viewport ? viewport.offsetTop : 0;
      const width = viewport ? viewport.width : document.documentElement.clientWidth;
      const height = viewport ? viewport.height : window.innerHeight;
      const margin = 12;
      const gap = 6;
      const anchor = root.getBoundingClientRect();
      menu.style.width = `${Math.min(304, width - margin * 2)}px`;
      const x = Math.max(left + margin,
        Math.min(anchor.right - menu.offsetWidth, left + width - margin - menu.offsetWidth));
      const below = Math.max(0, top + height - margin - anchor.bottom - gap);
      const above = Math.max(0, anchor.top - gap - top - margin);
      const upwards = menu.scrollHeight > below && above > below;
      menu.style.setProperty('--page-menu-max-height', `${upwards ? above : below}px`);
      const y = upwards ? anchor.top - gap - menu.offsetHeight : anchor.bottom + gap;
      menu.style.left = `${x - anchor.left - root.clientLeft}px`;
      menu.style.top = `${y - anchor.top - root.clientTop}px`;
      menu.style.right = 'auto';
    };

    const setOpen = (open) => {
      menu.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      root.toggleAttribute('data-open', open);
      if (open) positionMenu();
    };
    let positionFrame = 0;
    const schedulePosition = (event) => {
      // Scrolling the menu's own rows does not move its anchor.
      if (menu.hidden || event.target === menu || positionFrame) return;
      positionFrame = requestAnimationFrame(() => {
        positionFrame = 0;
        positionMenu();
      });
    };
    window.addEventListener('resize', schedulePosition);
    window.addEventListener('scroll', schedulePosition, { capture: true, passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', schedulePosition);
      window.visualViewport.addEventListener('scroll', schedulePosition);
    }

    toggle.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(menu.hidden);
    });

    // The menu item that duplicates the main button. It delegates rather than
    // re-implementing the copy so there is one clipboard path and one place
    // the "Copied" acknowledgement is produced.
    const proxy = menu.querySelector('[data-mewbo-copy-proxy]');
    if (proxy && copyButton) {
      proxy.addEventListener('click', (event) => {
        event.preventDefault();
        copyButton.click();
        setOpen(false);
      });
    }

    // A menu that swallows the next click anywhere, or traps focus with no way
    // back, is worse than no menu. Close on outside click, on Escape, and
    // whenever a link inside it is followed.
    document.addEventListener('click', (event) => {
      if (!menu.hidden && !root.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !menu.hidden) {
        setOpen(false);
        toggle.focus();
      }
    });
    menu.querySelectorAll('a[role="menuitem"]').forEach((link) => {
      link.addEventListener('click', () => setOpen(false));
    });
  }

  function initCopyButton() {
    const button = findCopyButton();
    if (button) {
      bindCopyHandler(button);
    }
    const root = document.querySelector('[data-mewbo-page-actions]');
    if (root) {
      bindMenu(root, button);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCopyButton);
  } else {
    initCopyButton();
  }
})();
