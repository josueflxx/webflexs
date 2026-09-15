(function () {
    const MIN_CHARS = 2;
    const DEBOUNCE_MS = 170;
    const MAX_ITEMS_DEFAULT = 12;
    const MAX_ITEMS_PRODUCTS = 300;
    const API_URL = window.FLEXS_SEARCH_SUGGEST_URL || '/api/search-suggestions/';

    let openInstance = null;
    let instanceCounter = 0;

    function escapeHtml(value) {
        return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function highlightMatch(value, query) {
        const text = String(value || '');
        const needle = String(query || '').trim();
        if (!needle) {
            return escapeHtml(text);
        }

        const index = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
        if (index < 0) {
            return escapeHtml(text);
        }

        return [
            escapeHtml(text.slice(0, index)),
            `<mark>${escapeHtml(text.slice(index, index + needle.length))}</mark>`,
            escapeHtml(text.slice(index + needle.length)),
        ].join('');
    }

    function cleanDetail(value) {
        const text = String(value || '').trim();
        if (['nan', 'none', 'null', 'undefined', '-'].includes(text.toLocaleLowerCase())) {
            return '';
        }
        return text;
    }

    function getKindLabel(kind) {
        const labels = {
            query: 'Búsqueda',
            client: 'Cliente',
            product: 'Producto',
            category: 'Categoría',
            order: 'Pedido',
            supplier: 'Proveedor',
            payment: 'Cobro',
            clamp_request: 'Solicitud',
            admin_user: 'Usuario',
        };
        return labels[String(kind || '').toLowerCase()] || 'Resultado';
    }

    function getItemInitials(item) {
        const kind = String(item.kind || '').toLowerCase();
        if (kind !== 'client') {
            const shortLabels = {
                query: 'IR',
                product: 'PR',
                category: 'CA',
                order: 'PE',
                supplier: 'PV',
                payment: 'CO',
                clamp_request: 'SO',
                admin_user: 'US',
            };
            return shortLabels[kind] || 'RE';
        }

        const words = String(item.label || item.value || '')
            .trim()
            .split(/\s+/)
            .filter(Boolean);
        if (!words.length) {
            return 'CL';
        }
        if (words.length === 1) {
            return words[0].slice(0, 2).toUpperCase();
        }
        return `${words[0][0]}${words[1][0]}`.toUpperCase();
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
            this.submitMode = String(input.dataset.suggestSubmit || 'auto').toLowerCase();
            this.includeQueryItem = input.dataset.suggestQueryItem !== '0';
            this.targetSelector = String(input.dataset.suggestTarget || '').trim();
            this.items = [];
            this.highlightIndex = -1;
            this.abortController = null;
            this.debounceTimer = null;
            this.lastQuery = '';
            this.maxItems = this.resolveMaxItems();
            this.instanceId = ++instanceCounter;

            this.dropdown = document.createElement('div');
            this.dropdown.className = 'flex-search-suggest';
            this.dropdown.id = `flex-search-suggest-${this.instanceId}`;
            this.dropdown.setAttribute('role', 'listbox');
            this.dropdown.setAttribute('aria-label', 'Sugerencias de búsqueda');
            this.dropdown.style.display = 'none';
            this.dropdown.innerHTML = '<div class="flex-search-suggest-list"></div>';
            this.listEl = this.dropdown.querySelector('.flex-search-suggest-list');
            document.body.appendChild(this.dropdown);

            // Disable browser search history/autocomplete dropdown to keep
            // only server-side suggestions in the UI.
            this.input.setAttribute('autocomplete', 'off');
            this.input.setAttribute('autocapitalize', 'off');
            this.input.setAttribute('autocorrect', 'off');
            this.input.setAttribute('spellcheck', 'false');
            this.input.setAttribute('role', 'combobox');
            this.input.setAttribute('aria-autocomplete', 'list');
            this.input.setAttribute('aria-controls', this.dropdown.id);
            this.input.setAttribute('aria-expanded', 'false');
            if (this.form) {
                this.form.setAttribute('autocomplete', 'off');
            }

            this.bindEvents();
        }

        resolveMaxItems() {
            const explicitMax = Number(this.input.dataset.suggestMax || '');
            if (Number.isFinite(explicitMax) && explicitMax > 0) {
                return explicitMax;
            }
            if (this.scope === 'admin_products' || this.scope === 'admin_supplier_products') {
                return MAX_ITEMS_PRODUCTS;
            }
            return MAX_ITEMS_DEFAULT;
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
            this.clearTargetValue();
            if (this.listEl) {
                this.listEl.scrollTop = 0;
            }

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
            const queryItem = this.includeQueryItem
                ? {
                      value: query,
                      input_value: query,
                      label: `Ver resultados para "${query}"`,
                      meta: 'Aplicar la búsqueda completa',
                      kind: 'query',
                  }
                : null;

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
                    this.items = queryItem ? [queryItem] : [];
                    this.highlightIndex = -1;
                    this.render();
                    if (this.items.length) {
                        this.open();
                    } else {
                        this.close();
                    }
                    return;
                }

                const data = await response.json();
                const serverItems = Array.isArray(data.suggestions) ? data.suggestions : [];

                const merged = (queryItem ? [queryItem, ...serverItems] : serverItems).slice(
                    0,
                    queryItem ? this.maxItems + 1 : this.maxItems
                );
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
                this.items = queryItem ? [queryItem] : [];
                this.highlightIndex = -1;
                this.render();
                if (this.items.length > 0) {
                    this.open();
                } else {
                    this.close();
                }
            }
        }

        render() {
            if (!this.listEl) {
                return;
            }

            if (!this.items.length) {
                this.listEl.innerHTML = '';
                this.listEl.scrollTop = 0;
                return;
            }

            const resultCount = this.items.filter((item) => item.kind !== 'query').length;
            this.listEl.innerHTML = this.items
                .map((item, index) => {
                    const kind = String(item.kind || '').toLowerCase();
                    const activeClass = index === this.highlightIndex ? ' is-active' : '';
                    const queryClass = kind === 'query' ? ' is-query' : '';
                    const label = highlightMatch(item.label || item.value || '', this.lastQuery);
                    const value = escapeHtml(item.value || '');
                    const meta = escapeHtml(item.meta || '');
                    const price = item.price ? escapeHtml(item.price) : '';
                    const username = escapeHtml(cleanDetail(item.username));
                    const documentNumber = escapeHtml(cleanDetail(item.document));
                    const kindLabel = escapeHtml(getKindLabel(kind));
                    const initials = escapeHtml(getItemInitials(item));
                    const itemId = `${this.dropdown.id}-option-${index}`;

                    const clientDetails = kind === 'client'
                        ? (
                            username || documentNumber
                                ? `
                                    <span class="flex-search-suggest-details">
                                        ${username ? `<span><strong>Usuario</strong>${username}</span>` : ''}
                                        ${documentNumber ? `<span><strong>Documento</strong>${documentNumber}</span>` : ''}
                                    </span>
                                `
                                : (meta ? `<span class="flex-search-suggest-meta">${meta}</span>` : '')
                        )
                        : (meta ? `<span class="flex-search-suggest-meta">${meta}</span>` : '');

                    const sectionHeader = (
                        this.scope === 'admin_clients' &&
                        resultCount > 0 &&
                        kind !== 'query' &&
                        (index === 0 || this.items[index - 1].kind === 'query')
                    )
                        ? `
                            <div class="flex-search-suggest-section" aria-hidden="true">
                                <span>Clientes encontrados</span>
                                <strong>${resultCount}</strong>
                            </div>
                        `
                        : '';

                    return `
                        ${sectionHeader}
                        <button
                            id="${itemId}"
                            type="button"
                            role="option"
                            aria-selected="${index === this.highlightIndex ? 'true' : 'false'}"
                            class="flex-search-suggest-item${activeClass}${queryClass}"
                            data-index="${index}"
                            data-value="${value}"
                            data-kind="${escapeHtml(kind)}"
                        >
                            <span class="flex-search-suggest-visual" aria-hidden="true">${initials}</span>
                            <span class="flex-search-suggest-copy">
                                <span class="flex-search-suggest-kicker">${kindLabel}</span>
                                <span class="flex-search-suggest-label">
                                    ${label}
                                    ${price ? `<span class="flex-search-suggest-price">$${price}</span>` : ''}
                                </span>
                                ${clientDetails}
                            </span>
                            <span class="flex-search-suggest-arrow" aria-hidden="true">
                                <svg viewBox="0 0 24 24" fill="none">
                                    <path d="m9 5 7 7-7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                                </svg>
                            </span>
                        </button>
                    `;
                })
                .join('');
            this.listEl.scrollTop = 0;

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
            let activeButton = null;
            this.listEl.querySelectorAll('.flex-search-suggest-item').forEach((btn) => {
                const index = Number(btn.dataset.index || -1);
                if (index === this.highlightIndex) {
                    btn.classList.add('is-active');
                    btn.setAttribute('aria-selected', 'true');
                    activeButton = btn;
                } else {
                    btn.classList.remove('is-active');
                    btn.setAttribute('aria-selected', 'false');
                }
            });

            if (activeButton) {
                this.input.setAttribute('aria-activedescendant', activeButton.id);
                activeButton.scrollIntoView({ block: 'nearest' });
            } else {
                this.input.removeAttribute('aria-activedescendant');
            }
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
            this.input.value = item.input_value || item.label || item.value || '';
            this.applyTargetValue(item);
            this.close();
            if (this.submitMode !== 'manual' && this.form) {
                if (typeof this.form.requestSubmit === 'function') {
                    this.form.requestSubmit();
                } else {
                    this.form.submit();
                }
                return;
            }

            // If a target input exists (e.g. hidden product_id), we only needed
            // to fill it — do NOT navigate away from the page.
            // Dispatch a custom event so page scripts can react (e.g. fill price).
            if (this.targetSelector) {
                const target = this.getTargetInput();
                if (target) {
                    target.dispatchEvent(new CustomEvent('flex-suggest-pick', {
                        bubbles: true,
                        detail: item,
                    }));
                }
                return;
            }

            const url = new URL(window.location.href);
            const name = this.input.getAttribute('name') || 'q';
            url.searchParams.set(name, this.input.value);
            window.location.assign(url.toString());
        }


        getTargetInput() {
            if (!this.targetSelector) {
                return null;
            }
            return document.querySelector(this.targetSelector);
        }

        clearTargetValue() {
            const target = this.getTargetInput();
            if (target) {
                target.value = '';
            }
        }

        applyTargetValue(item) {
            const target = this.getTargetInput();
            if (!target) {
                return;
            }
            target.value = item.target_value || item.id || item.value || '';
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
            const viewportPadding = 8;
            const preferredMinWidth = this.scope === 'admin_clients' ? 440 : 320;
            const availableWidth = Math.max(window.innerWidth - (viewportPadding * 2), 280);
            const width = Math.min(Math.max(rect.width, preferredMinWidth), availableWidth, 720);
            const left = Math.min(
                Math.max(rect.left, viewportPadding),
                Math.max(viewportPadding, window.innerWidth - width - viewportPadding)
            );
            this.dropdown.style.left = `${left}px`;
            this.dropdown.style.top = `${rect.bottom + 6}px`;
            this.dropdown.style.width = `${width}px`;
        }

        open() {
            if (openInstance && openInstance !== this) {
                openInstance.close();
            }
            openInstance = this;
            this.dropdown.style.display = 'block';
            this.input.setAttribute('aria-expanded', 'true');
            this.reposition();
        }

        close() {
            if (openInstance === this) {
                openInstance = null;
            }
            this.dropdown.style.display = 'none';
            this.input.setAttribute('aria-expanded', 'false');
            this.input.removeAttribute('aria-activedescendant');
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
