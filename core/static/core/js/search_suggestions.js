(function () {
    const MIN_CHARS = 2;
    const DEBOUNCE_MS = 170;
    const MAX_ITEMS = 8;
    const API_URL = window.FLEXS_SEARCH_SUGGEST_URL || '/api/search-suggestions/';

    let openInstance = null;

    function escapeHtml(value) {
        return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function detectScopeFromPath(pathname) {
        const path = String(pathname || '').toLowerCase();

        if (path.startsWith('/catalogo')) {
            return 'catalog';
        }

        if (!path.startsWith('/admin-panel')) {
            return 'catalog';
        }

        if (/^\/admin-panel\/proveedores\/\d+\/?$/.test(path) || path.startsWith('/admin-panel/proveedores/sin-proveedor')) {
            return 'admin_supplier_products';
        }
        if (path.startsWith('/admin-panel/proveedores')) {
            return 'admin_suppliers';
        }
        if (/^\/admin-panel\/categorias\/\d+\/productos\/?/.test(path)) {
            return 'admin_products';
        }
        if (path.startsWith('/admin-panel/categorias')) {
            return 'admin_categories';
        }
        if (path.startsWith('/admin-panel/clientes')) {
            return 'admin_clients';
        }
        if (path.startsWith('/admin-panel/pedidos')) {
            return 'admin_orders';
        }
        if (path.startsWith('/admin-panel/pagos')) {
            return 'admin_payments';
        }
        if (path.startsWith('/admin-panel/abrazaderas-a-medida')) {
            return 'admin_clamp_requests';
        }
        if (path.startsWith('/admin-panel/admins')) {
            return 'admin_admins';
        }
        if (path.startsWith('/admin-panel/productos')) {
            return 'admin_products';
        }

        return 'admin_products';
    }

    class FlexSearchSuggest {
        constructor(input, scope) {
            this.input = input;
            this.scope = scope;
            this.form = input.form;
            this.items = [];
            this.highlightIndex = -1;
            this.abortController = null;
            this.debounceTimer = null;
            this.lastQuery = '';

            this.dropdown = document.createElement('div');
            this.dropdown.className = 'flex-search-suggest';
            this.dropdown.style.display = 'none';
            this.dropdown.innerHTML = '<div class="flex-search-suggest-list"></div>';
            this.listEl = this.dropdown.querySelector('.flex-search-suggest-list');
            document.body.appendChild(this.dropdown);

            this.bindEvents();
        }

        bindEvents() {
            this.input.addEventListener('input', () => {
                this.onInputChange();
            });

            this.input.addEventListener('focus', () => {
                if (this.items.length > 0) {
                    this.open();
                }
            });

            this.input.addEventListener('keydown', (event) => {
                if (!this.isOpen()) {
                    return;
                }

                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    this.moveHighlight(1);
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    this.moveHighlight(-1);
                } else if (event.key === 'Enter') {
                    if (this.highlightIndex >= 0 && this.highlightIndex < this.items.length) {
                        event.preventDefault();
                        this.pick(this.items[this.highlightIndex]);
                    }
                } else if (event.key === 'Escape') {
                    this.close();
                }
            });

            document.addEventListener('click', (event) => {
                if (event.target === this.input || this.dropdown.contains(event.target)) {
                    return;
                }
                this.close();
            });

            window.addEventListener('resize', () => this.reposition());
            window.addEventListener('scroll', () => this.reposition(), true);
        }

        onInputChange() {
            const query = String(this.input.value || '').trim();
            this.lastQuery = query;

            if (query.length < MIN_CHARS) {
                this.items = [];
                this.render();
                this.close();
                return;
            }

            if (this.debounceTimer) {
                clearTimeout(this.debounceTimer);
            }

            this.debounceTimer = setTimeout(() => {
                this.fetchSuggestions(query);
            }, DEBOUNCE_MS);
        }

        async fetchSuggestions(query) {
            if (this.abortController) {
                this.abortController.abort();
            }
            this.abortController = new AbortController();
            const queryItem = {
                value: query,
                label: `Buscar "${query}"`,
                meta: 'Busqueda exacta',
                kind: 'query',
            };

            try {
                const params = new URLSearchParams({
                    q: query,
                    scope: this.scope,
                });

                const response = await fetch(`${API_URL}?${params.toString()}`, {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                    },
                    signal: this.abortController.signal,
                });

                if (!response.ok) {
                    this.items = [queryItem];
                    this.highlightIndex = -1;
                    this.render();
                    this.open();
                    return;
                }

                const data = await response.json();
                const serverItems = Array.isArray(data.suggestions) ? data.suggestions : [];

                const merged = [queryItem, ...serverItems].slice(0, MAX_ITEMS + 1);
                this.items = merged;
                this.highlightIndex = -1;
                this.render();

                if (this.items.length > 0 && this.lastQuery === query) {
                    this.open();
                } else {
                    this.close();
                }
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    return;
                }
                this.items = [queryItem];
                this.highlightIndex = -1;
                this.render();
                this.open();
            }
        }

        render() {
            if (!this.listEl) {
                return;
            }

            if (!this.items.length) {
                this.listEl.innerHTML = '';
                return;
            }

            this.listEl.innerHTML = this.items
                .map((item, index) => {
                    const activeClass = index === this.highlightIndex ? ' is-active' : '';
                    const label = escapeHtml(item.label || item.value || '');
                    const value = escapeHtml(item.value || '');
                    const meta = escapeHtml(item.meta || '');
                    return `
                        <button type="button" class="flex-search-suggest-item${activeClass}" data-index="${index}" data-value="${value}">
                            <span class="flex-search-suggest-label">${label}</span>
                            ${meta ? `<span class="flex-search-suggest-meta">${meta}</span>` : ''}
                        </button>
                    `;
                })
                .join('');

            this.listEl.querySelectorAll('.flex-search-suggest-item').forEach((btn) => {
                btn.addEventListener('mouseenter', () => {
                    this.highlightIndex = Number(btn.dataset.index || -1);
                    this.applyHighlight();
                });
                btn.addEventListener('mousedown', (event) => {
                    event.preventDefault();
                });
                btn.addEventListener('click', () => {
                    const index = Number(btn.dataset.index || -1);
                    if (index >= 0 && index < this.items.length) {
                        this.pick(this.items[index]);
                    }
                });
            });
        }

        applyHighlight() {
            this.listEl.querySelectorAll('.flex-search-suggest-item').forEach((btn) => {
                const index = Number(btn.dataset.index || -1);
                if (index === this.highlightIndex) {
                    btn.classList.add('is-active');
                } else {
                    btn.classList.remove('is-active');
                }
            });
        }

        moveHighlight(direction) {
            if (!this.items.length) {
                return;
            }
            const nextIndex = this.highlightIndex + direction;
            if (nextIndex < 0) {
                this.highlightIndex = this.items.length - 1;
            } else if (nextIndex >= this.items.length) {
                this.highlightIndex = 0;
            } else {
                this.highlightIndex = nextIndex;
            }
            this.applyHighlight();
        }

        pick(item) {
            this.input.value = item.value || '';
            this.close();
            if (this.form) {
                if (typeof this.form.requestSubmit === 'function') {
                    this.form.requestSubmit();
                } else {
                    this.form.submit();
                }
                return;
            }

            const url = new URL(window.location.href);
            const name = this.input.getAttribute('name') || 'q';
            url.searchParams.set(name, this.input.value);
            window.location.assign(url.toString());
        }

        destroy() {
            this.close();
            if (this.dropdown && this.dropdown.parentNode) {
                this.dropdown.parentNode.removeChild(this.dropdown);
            }
        }

        reposition() {
            if (!this.isOpen()) {
                return;
            }
            const rect = this.input.getBoundingClientRect();
            this.dropdown.style.left = `${Math.max(rect.left, 8)}px`;
            this.dropdown.style.top = `${rect.bottom + 6}px`;
            this.dropdown.style.width = `${Math.max(rect.width, 280)}px`;
        }

        open() {
            if (openInstance && openInstance !== this) {
                openInstance.close();
            }
            openInstance = this;
            this.dropdown.style.display = 'block';
            this.reposition();
        }

        close() {
            if (openInstance === this) {
                openInstance = null;
            }
            this.dropdown.style.display = 'none';
        }

        isOpen() {
            return this.dropdown.style.display !== 'none';
        }
    }

    function getPrimarySearchInput(form) {
        return (
            form.querySelector('input[data-suggest]') ||
            form.querySelector('input[name="q"]') ||
            form.querySelector('input[name="client"]') ||
            form.querySelector('input[type="search"]') ||
            form.querySelector('#filterQ') ||
            form.querySelector('input[type="text"]')
        );
    }

    function collectCandidateInputs() {
        const inputSet = new Set();

        document
            .querySelectorAll(
                'form.toolbar-search input, form.search-form input, form.category-filter-form input, input[data-suggest], input[name="q"], input[name="client"], input[type="search"], #filterQ'
            )
            .forEach((input) => {
                if (!(input instanceof HTMLInputElement)) {
                    return;
                }
                if (input.type === 'hidden' || input.type === 'password') {
                    return;
                }
                inputSet.add(input);
            });

        return Array.from(inputSet);
    }

    function initSearchSuggestions() {
        const formSet = new Set();
        collectCandidateInputs().forEach((input) => {
            if (input.form) {
                formSet.add(input.form);
            }
        });

        formSet.forEach((form) => {
            const input = getPrimarySearchInput(form);
            if (!input) {
                return;
            }
            if (input.dataset.suggestBound === '1') {
                return;
            }

            const scope = input.dataset.suggestScope || form.dataset.suggestScope || detectScopeFromPath(window.location.pathname);
            input.dataset.suggestBound = '1';
            new FlexSearchSuggest(input, scope);
        });

        collectCandidateInputs().forEach((input) => {
            if (input.dataset.suggestBound === '1') {
                return;
            }
            const scope = input.dataset.suggestScope || detectScopeFromPath(window.location.pathname);
            input.dataset.suggestBound = '1';
            new FlexSearchSuggest(input, scope);
        });
    }

    document.addEventListener('DOMContentLoaded', initSearchSuggestions);
    window.addEventListener('load', initSearchSuggestions);
})();
