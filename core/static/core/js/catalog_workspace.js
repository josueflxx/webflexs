/* Presentation-only catalog controls; product/category filtering stays server-side. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const search = document.getElementById('catalogCategorySearch');
        const rows = Array.from(document.querySelectorAll('.category-tree-row'));
        const empty = document.getElementById('catalogCategoryEmpty');
        const byId = new Map(rows.map(row => [row.dataset.categoryId, row]));
        const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
        let beforeSearch = null;
        if (search) search.addEventListener('input', function () {
            const query = normalize(search.value);
            if (!query) {
                if (beforeSearch) rows.forEach(row => row.classList.toggle('expanded', beforeSearch.has(row.dataset.categoryId)));
                beforeSearch = null;
                window.initializeCategoryTree();
                if (empty) empty.hidden = true;
                return;
            }
            if (!beforeSearch) beforeSearch = new Set(rows.filter(row => row.classList.contains('expanded')).map(row => row.dataset.categoryId));
            const visible = new Set();
            rows.forEach(row => {
                if (!normalize(row.dataset.categoryPath || '').includes(query)) return;
                let current = row;
                while (current && !visible.has(current.dataset.categoryId)) {
                    visible.add(current.dataset.categoryId);
                    current = byId.get(current.dataset.parentId);
                }
            });
            rows.forEach(row => {
                row.style.display = visible.has(row.dataset.categoryId) ? 'block' : 'none';
                if (row.classList.contains('has-children')) row.classList.toggle('expanded', visible.has(row.dataset.categoryId));
            });
            if (empty) empty.hidden = visible.size > 0;
        });
        document.querySelectorAll('.category-tree-controls button').forEach(button => button.addEventListener('click', function () {
            if (search) search.value = '';
            beforeSearch = null;
            if (empty) empty.hidden = true;
            window.initializeCategoryTree();
        }));

        const drawers = [
            ['catalogSidebar', 'catalogMobileFilterBtn', 'openCatalogFilters', 'Categorías'],
            ['catalogSidebarRight', 'catalogMobileRightFilterBtn', 'openCatalogRightFilters', 'Filtros técnicos'],
        ];
        let activeSidebar = null;
        let activeOpener = null;
        if (typeof window.closeCatalogFilters === 'function') {
            const originalClose = window.closeCatalogFilters;
            drawers.forEach(([sidebarId, openerId, openName, label]) => {
                const sidebar = document.getElementById(sidebarId);
                const opener = document.getElementById(openerId);
                const originalOpen = window[openName];
                if (!sidebar || !opener || typeof originalOpen !== 'function') return;
                window[openName] = function () {
                    if (activeSidebar) window.closeCatalogFilters();
                    originalOpen();
                    activeSidebar = sidebar;
                    activeOpener = opener;
                    opener.setAttribute('aria-expanded', 'true');
                    sidebar.setAttribute('role', 'dialog');
                    sidebar.setAttribute('aria-modal', 'true');
                    sidebar.setAttribute('aria-label', label);
                    sidebar.querySelector('.catalog-sidebar-close')?.focus();
                };
            });
            window.closeCatalogFilters = function () {
                originalClose();
                activeOpener?.setAttribute('aria-expanded', 'false');
                activeSidebar?.removeAttribute('role');
                activeSidebar?.removeAttribute('aria-modal');
                activeOpener?.focus();
                activeSidebar = activeOpener = null;
            };
            document.addEventListener('keydown', function (event) {
                if (!activeSidebar) return;
                if (event.key === 'Escape') window.closeCatalogFilters();
                if (event.key !== 'Tab') return;
                const focusable = Array.from(activeSidebar.querySelectorAll('a[href], button, input, select')).filter(element => !element.disabled && element.getClientRects().length);
                const first = focusable[0], last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            });
        }
    });
}());
