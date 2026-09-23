(() => {
    const nav = document.querySelector('.admin-module-nav');
    if (!nav) return;
    const menus = Array.from(nav.querySelectorAll('details'));
    const fitPanel = menu => {
        const panel = menu.querySelector('.admin-module-links');
        if (!menu.open || !panel) return;
        const available = Math.max(120, window.innerHeight - panel.getBoundingClientRect().top - 16);
        panel.style.setProperty('--nav-available-height', `${available}px`);
    };
    let best = null;
    nav.querySelectorAll('a').forEach(link => {
        const path = new URL(link.href).pathname;
        if (location.pathname === path || (path !== '/admin-panel/' && location.pathname.startsWith(path))) {
            if (!best || path.length > new URL(best.href).pathname.length) best = link;
        }
    });
    if (best) {
        best.setAttribute('aria-current', 'page');
        best.closest('details')?.classList.add('is-current');
    }
    menus.forEach(menu => {
        const trigger = menu.querySelector('summary');
        trigger.setAttribute('aria-expanded', String(menu.open));
        menu.addEventListener('toggle', () => {
            trigger.setAttribute('aria-expanded', String(menu.open));
            if (menu.open) {
                menus.forEach(other => { if (other !== menu) other.open = false; });
                fitPanel(menu);
            }
        });
        // Native details still supports Enter/Space; ArrowDown jumps into its links.
        trigger.addEventListener('keydown', event => {
            if (event.key !== 'ArrowDown') return;
            event.preventDefault();
            menu.open = true;
            menu.querySelector('.admin-module-links a')?.focus();
        });
    });
    document.addEventListener('click', event => {
        if (!nav.contains(event.target)) menus.forEach(menu => menu.open = false);
    });
    document.addEventListener('focusin', event => {
        menus.forEach(menu => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
    });
    let fitFrame = 0;
    const scheduleFit = () => {
        if (fitFrame || !menus.some(menu => menu.open)) return;
        fitFrame = requestAnimationFrame(() => {
            menus.forEach(fitPanel);
            fitFrame = 0;
        });
    };
    window.addEventListener('resize', scheduleFit);
    window.addEventListener('scroll', scheduleFit, { passive: true });
    nav.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const open = menus.find(menu => menu.open);
        if (open) { open.open = false; open.querySelector('summary').focus(); }
    });
})();
