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

        const sidebar = document.getElementById('catalogSidebar');
        const opener = document.getElementById('catalogMobileFilterBtn');
        if (sidebar && opener) {
            const originalOpen = window.openCatalogFilters;
            const originalClose = window.closeCatalogFilters;
            window.openCatalogFilters = function () {
                originalOpen();
                opener.setAttribute('aria-expanded', 'true');
                sidebar.setAttribute('role', 'dialog');
                sidebar.setAttribute('aria-modal', 'true');
                sidebar.setAttribute('aria-label', 'Categorías y filtros');
                sidebar.querySelector('.catalog-sidebar-close')?.focus();
            };
            window.closeCatalogFilters = function () {
                originalClose();
                opener.setAttribute('aria-expanded', 'false');
                sidebar.removeAttribute('role');
                sidebar.removeAttribute('aria-modal');
                opener.focus();
            };
            document.addEventListener('keydown', function (event) {
                if (!sidebar.classList.contains('is-open')) return;
                if (event.key === 'Escape') window.closeCatalogFilters();
                if (event.key !== 'Tab') return;
                const focusable = Array.from(sidebar.querySelectorAll('a[href], button, input, select')).filter(element => !element.disabled && element.getClientRects().length);
                const first = focusable[0], last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            });
        }
    });
}());
