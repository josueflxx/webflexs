(function () {
    "use strict";

    const SELECTOR = "select";
    const SEARCH_THRESHOLD = 8;
    const instances = new Map();
    let openInstance = null;
    let instanceSequence = 0;
    let scanScheduled = false;

    const icons = {
        chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 9 5 5 5-5"></path></svg>',
        search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4 4"></path></svg>',
        check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.2 4.2L19 7"></path></svg>'
    };

    function normalizeText(value) {
        return String(value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLocaleLowerCase("es")
            .trim();
    }

    function shouldEnhance(select) {
        if (!(select instanceof HTMLSelectElement)) return false;
        if (select.dataset.smartSelectReady === "true") return false;
        if (select.dataset.smartSelect === "off") return false;
        if (select.multiple || Number(select.size || 0) > 1) return false;
        if (select.matches(".clamp-hidden-select, .filter-select, .col-select, .block-select, [aria-hidden='true']")) return false;
        if (select.closest(".spreadsheet-cell, .spreadsheet-table, .spreadsheet-wrap, .grid-editor, [data-smart-select-scope='off']")) return false;
        const style = window.getComputedStyle(select);
        if (style.display === "none" || style.visibility === "hidden") return false;
        return true;
    }

    class SmartSelect {
        constructor(select) {
            this.select = select;
            this.id = ++instanceSequence;
            this.lastValue = select.value;
            this.lastDisabled = select.disabled;
            this.highlightedIndex = -1;
            this.optionButtons = [];
            this.build();
            this.bind();
            this.sync();
        }

        build() {
            const wrapper = document.createElement("div");
            wrapper.className = "smart-select";
            wrapper.dataset.smartSelectId = String(this.id);

            const trigger = document.createElement("button");
            trigger.type = "button";
            trigger.className = "smart-select__trigger";
            trigger.setAttribute("aria-haspopup", "listbox");
            trigger.setAttribute("aria-expanded", "false");
            trigger.setAttribute("aria-controls", `smartSelectListbox${this.id}`);

            const value = document.createElement("span");
            value.className = "smart-select__value";

            const chevron = document.createElement("span");
            chevron.className = "smart-select__chevron";
            chevron.innerHTML = icons.chevron;

            trigger.append(value, chevron);

            const popover = document.createElement("div");
            popover.className = "smart-select__popover";
            popover.hidden = true;

            const options = document.createElement("div");
            options.className = "smart-select__options";
            options.id = `smartSelectListbox${this.id}`;
            options.setAttribute("role", "listbox");
            options.setAttribute("aria-label", this.select.getAttribute("aria-label") || "Opciones");

            const footer = document.createElement("div");
            footer.className = "smart-select__footer";

            popover.append(options, footer);
            wrapper.append(trigger, popover);
            this.select.insertAdjacentElement("afterend", wrapper);

            if (this.select.style.width) wrapper.style.width = this.select.style.width;
            if (this.select.style.maxWidth) wrapper.style.maxWidth = this.select.style.maxWidth;
            if (this.select.style.minWidth) wrapper.style.minWidth = this.select.style.minWidth;

            this.select.classList.add("smart-select-native");
            this.select.dataset.smartSelectReady = "true";

            this.wrapper = wrapper;
            this.trigger = trigger;
            this.valueNode = value;
            this.popover = popover;
            this.optionsNode = options;
            this.footer = footer;
            this.renderOptions();
        }

        visibleOptions() {
            return Array.from(this.select.options).filter((option) => {
                if (option.hidden) return false;
                if (option.style && option.style.display === "none") return false;
                return true;
            });
        }

        renderOptions() {
            const options = this.visibleOptions();
            const searchable = this.select.dataset.smartSelectSearch !== "off"
                && options.filter((option) => !option.disabled).length >= SEARCH_THRESHOLD;

            if (this.searchWrap) this.searchWrap.remove();
            this.optionsNode.replaceChildren();
            this.optionButtons = [];
            this.highlightedIndex = -1;

            if (searchable) {
                const searchWrap = document.createElement("div");
                searchWrap.className = "smart-select__search-wrap";

                const searchIcon = document.createElement("span");
                searchIcon.className = "smart-select__search-icon";
                searchIcon.innerHTML = icons.search;

                const search = document.createElement("input");
                search.type = "search";
                search.className = "smart-select__search";
                search.placeholder = "Buscar una opción...";
                search.autocomplete = "off";
                search.setAttribute("aria-label", "Buscar opciones");

                searchWrap.append(searchIcon, search);
                this.popover.insertBefore(searchWrap, this.optionsNode);
                this.searchWrap = searchWrap;
                this.searchInput = search;
                search.addEventListener("input", () => this.filter(search.value));
                search.addEventListener("keydown", (event) => this.handleSearchKeydown(event));
            } else {
                this.searchWrap = null;
                this.searchInput = null;
            }

            let lastGroup = null;
            options.forEach((option) => {
                const parent = option.parentElement;
                const groupLabel = parent instanceof HTMLOptGroupElement ? parent.label : "";
                if (groupLabel && groupLabel !== lastGroup) {
                    const group = document.createElement("div");
                    group.className = "smart-select__group";
                    group.textContent = groupLabel;
                    this.optionsNode.append(group);
                    lastGroup = groupLabel;
                }

                const button = document.createElement("button");
                button.type = "button";
                button.className = "smart-select__option";
                button.dataset.optionIndex = String(option.index);
                button.dataset.searchText = normalizeText(`${groupLabel} ${option.textContent}`);
                button.setAttribute("role", "option");
                button.setAttribute("aria-selected", option.selected ? "true" : "false");
                button.disabled = option.disabled;
                button.title = option.textContent.trim();

                const label = document.createElement("span");
                label.className = "smart-select__option-label";
                label.textContent = option.textContent.trim();

                const check = document.createElement("span");
                check.className = "smart-select__check";
                check.innerHTML = icons.check;

                button.append(label, check);
                button.addEventListener("click", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    this.choose(Number(button.dataset.optionIndex));
                });
                button.addEventListener("pointermove", () => this.setHighlight(button));
                this.optionsNode.append(button);
                this.optionButtons.push(button);
            });

            const empty = document.createElement("div");
            empty.className = "smart-select__empty";
            empty.textContent = "No hay opciones que coincidan.";
            empty.hidden = true;
            this.optionsNode.append(empty);
            this.emptyNode = empty;
            this.footer.textContent = `${options.length} ${options.length === 1 ? "opción" : "opciones"}`;
            this.refreshSelectedState();
        }

        bind() {
            this.trigger.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                this.toggle();
            });
            this.trigger.addEventListener("keydown", (event) => this.handleTriggerKeydown(event));

            this.select.addEventListener("change", () => this.sync());
            this.select.addEventListener("input", () => this.sync());
            this.select.addEventListener("focus", () => this.trigger.focus());
            this.select.addEventListener("invalid", () => {
                this.wrapper.classList.add("has-error");
                this.trigger.focus();
            });

            const form = this.select.form;
            if (form) {
                form.addEventListener("reset", () => window.setTimeout(() => this.sync(), 0));
            }

            this.mutationObserver = new MutationObserver(() => {
                this.renderOptions();
                this.sync();
            });
            this.mutationObserver.observe(this.select, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ["disabled", "hidden", "label", "selected"]
            });
        }

        sync() {
            const selected = this.select.selectedOptions[0];
            this.valueNode.textContent = selected ? selected.textContent.trim() : "Seleccionar";
            this.valueNode.title = this.valueNode.textContent;
            this.trigger.disabled = this.select.disabled;
            this.wrapper.classList.toggle("is-disabled", this.select.disabled);
            this.wrapper.classList.remove("has-error");
            this.lastValue = this.select.value;
            this.lastDisabled = this.select.disabled;
            this.refreshSelectedState();
            if (this.select.disabled) this.close(false);
        }

        refreshSelectedState() {
            this.optionButtons.forEach((button) => {
                const option = this.select.options[Number(button.dataset.optionIndex)];
                const selected = Boolean(option && option.selected);
                button.classList.toggle("is-selected", selected);
                button.setAttribute("aria-selected", selected ? "true" : "false");
                button.disabled = !option || option.disabled;
            });
        }

        toggle() {
            if (this.select.disabled) return;
            if (this.wrapper.classList.contains("is-open")) this.close();
            else this.open();
        }

        open() {
            if (openInstance && openInstance !== this) openInstance.close(false);
            this.renderOptions();
            this.wrapper.classList.add("is-open");
            this.trigger.setAttribute("aria-expanded", "true");
            this.popover.hidden = false;
            openInstance = this;
            this.position();

            const selected = this.optionButtons.find((button) => button.classList.contains("is-selected") && !button.hidden);
            if (selected) {
                this.setHighlight(selected);
                selected.scrollIntoView({ block: "nearest" });
            }
            if (this.searchInput) {
                this.searchInput.value = "";
                this.filter("");
                window.requestAnimationFrame(() => this.searchInput.focus());
            }
        }

        close(restoreFocus = true) {
            if (!this.wrapper.classList.contains("is-open")) return;
            this.wrapper.classList.remove("is-open");
            this.trigger.setAttribute("aria-expanded", "false");
            this.popover.hidden = true;
            this.popover.classList.remove("is-above");
            this.highlightedIndex = -1;
            if (openInstance === this) openInstance = null;
            if (restoreFocus) this.trigger.focus();
        }

        position() {
            if (this.popover.hidden) return;
            const rect = this.trigger.getBoundingClientRect();
            const margin = 8;
            const preferredWidth = this.searchInput ? 360 : 220;
            const width = Math.min(Math.max(rect.width, preferredWidth), window.innerWidth - margin * 2);
            const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
            const below = window.innerHeight - rect.bottom - margin;
            const above = rect.top - margin;
            const placeAbove = below < 250 && above > below;
            const available = Math.max(170, Math.min(430, placeAbove ? above - 6 : below - 6));

            this.popover.style.width = `${width}px`;
            this.popover.style.left = `${left}px`;
            this.popover.style.maxHeight = `${available}px`;
            this.optionsNode.style.maxHeight = `${Math.max(110, available - (this.searchInput ? 64 : 25))}px`;
            this.popover.classList.toggle("is-above", placeAbove);

            if (placeAbove) {
                const top = Math.max(margin, rect.top - Math.min(this.popover.offsetHeight, available) - 6);
                this.popover.style.top = `${top}px`;
            } else {
                this.popover.style.top = `${rect.bottom + 6}px`;
            }
        }

        filter(query) {
            const normalized = normalizeText(query);
            let visibleCount = 0;
            this.optionButtons.forEach((button) => {
                const visible = !normalized || button.dataset.searchText.includes(normalized);
                button.hidden = !visible;
                if (visible) visibleCount += 1;
            });
            this.emptyNode.hidden = visibleCount !== 0;
            this.footer.textContent = normalized
                ? `${visibleCount} ${visibleCount === 1 ? "resultado" : "resultados"}`
                : `${this.optionButtons.length} ${this.optionButtons.length === 1 ? "opción" : "opciones"}`;
            this.highlightedIndex = -1;
        }

        visibleButtons() {
            return this.optionButtons.filter((button) => !button.hidden && !button.disabled);
        }

        setHighlight(button) {
            this.optionButtons.forEach((item) => item.classList.toggle("is-highlighted", item === button));
            this.highlightedIndex = this.visibleButtons().indexOf(button);
        }

        moveHighlight(delta) {
            const visible = this.visibleButtons();
            if (!visible.length) return;
            let next = this.highlightedIndex + delta;
            if (next < 0) next = visible.length - 1;
            if (next >= visible.length) next = 0;
            this.setHighlight(visible[next]);
            visible[next].scrollIntoView({ block: "nearest" });
        }

        choose(optionIndex) {
            const option = this.select.options[optionIndex];
            if (!option || option.disabled) return;
            this.select.value = option.value;
            this.select.dispatchEvent(new Event("input", { bubbles: true }));
            this.select.dispatchEvent(new Event("change", { bubbles: true }));
            this.sync();
            this.close();
        }

        handleTriggerKeydown(event) {
            if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
                event.preventDefault();
                if (!this.wrapper.classList.contains("is-open")) this.open();
                if (event.key === "ArrowDown") this.moveHighlight(1);
                if (event.key === "ArrowUp") this.moveHighlight(-1);
            } else if (event.key === "Escape") {
                this.close();
            }
        }

        handleSearchKeydown(event) {
            if (event.key === "ArrowDown") {
                event.preventDefault();
                this.moveHighlight(1);
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                this.moveHighlight(-1);
            } else if (event.key === "Enter") {
                event.preventDefault();
                const visible = this.visibleButtons();
                const button = visible[this.highlightedIndex] || (visible.length === 1 ? visible[0] : null);
                if (button) this.choose(Number(button.dataset.optionIndex));
            } else if (event.key === "Escape") {
                event.preventDefault();
                this.close();
            } else if (event.key === "Tab") {
                this.close(false);
            }
        }
    }

    function enhance(root) {
        const candidates = [];
        if (root instanceof HTMLSelectElement && root.matches(SELECTOR)) candidates.push(root);
        if (root.querySelectorAll) candidates.push(...root.querySelectorAll(SELECTOR));
        candidates.forEach((select) => {
            if (!shouldEnhance(select)) return;
            const instance = new SmartSelect(select);
            instances.set(select, instance);
        });
    }

    function scheduleEnhance(root) {
        if (scanScheduled) return;
        scanScheduled = true;
        window.requestAnimationFrame(() => {
            scanScheduled = false;
            enhance(root || document);
        });
    }

    document.addEventListener("click", (event) => {
        if (!openInstance) return;
        if (openInstance.wrapper.contains(event.target) || openInstance.popover.contains(event.target)) return;
        openInstance.close(false);
    });

    window.addEventListener("resize", () => {
        if (openInstance) openInstance.position();
    });

    document.addEventListener("scroll", (event) => {
        if (openInstance && openInstance.popover.contains(event.target)) return;
        if (openInstance) openInstance.close(false);
    }, true);

    const pageObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (!(node instanceof Element)) continue;
                scheduleEnhance(node);
                return;
            }
        }
    });

    window.FlexsSelect = {
        enhance,
        sync(select) {
            const instance = instances.get(select);
            if (instance) {
                instance.renderOptions();
                instance.sync();
            }
        },
        closeAll() {
            if (openInstance) openInstance.close(false);
        }
    };

    function initialize() {
        enhance(document);
        pageObserver.observe(document.body, { childList: true, subtree: true });
        window.setInterval(() => {
            instances.forEach((instance, select) => {
                if (!select.isConnected) {
                    instances.delete(select);
                    return;
                }
                if (instance.lastValue !== select.value || instance.lastDisabled !== select.disabled) {
                    instance.sync();
                }
            });
        }, 400);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();
