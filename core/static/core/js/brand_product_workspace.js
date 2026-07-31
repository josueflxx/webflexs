(function () {
    "use strict";

    const root = document.getElementById("brandProductWorkspace");
    if (!root) return;

    const config = {
        targetKind: root.dataset.targetKind,
        targetId: root.dataset.targetId,
        searchUrl: root.dataset.searchUrl,
        bulkAddUrl: root.dataset.bulkAddUrl,
        bulkRemoveUrl: root.dataset.bulkRemoveUrl,
        reorderUrl: root.dataset.reorderUrl,
        syncUrl: root.dataset.syncUrl,
        undoUrlTemplate: root.dataset.undoUrlTemplate,
    };
    const csrfToken = root.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    const selectionKey = `brand-product-selection:${config.targetKind}:${config.targetId}`;
    const flashKey = `brand-product-flash:${config.targetKind}:${config.targetId}`;
    const state = {
        selected: new Set(),
        selectAll: false,
        selectAllFilters: null,
        visibleProductIds: [],
        currentPage: 1,
        totalCount: 0,
        hasMore: false,
        loading: false,
        syncPreview: null,
        assignedSelection: new Set(),
        orderDirty: false,
    };

    const elements = {
        results: document.getElementById("brandProductsResults"),
        resultCount: document.getElementById("availableResultCount"),
        heroAvailable: document.getElementById("availableHeroCount"),
        pageInfo: document.getElementById("brandResultsPageInfo"),
        loadMore: document.getElementById("loadMoreBrandProducts"),
        search: document.getElementById("brandProductSearch"),
        category: document.getElementById("brandCategoryFilter"),
        supplier: document.getElementById("brandSupplierFilter"),
        status: document.getElementById("brandStatusFilter"),
        stock: document.getElementById("brandStockFilter"),
        assignment: document.getElementById("brandAssignmentFilter"),
        selectionDock: document.getElementById("brandSelectionDock"),
        selectionCount: document.getElementById("brandSelectionCount"),
        selectionHint: document.getElementById("brandSelectionHint"),
        assignedList: document.getElementById("brandAssignedList"),
        assignedSearch: document.getElementById("assignedProductSearch"),
        assignedCount: document.getElementById("assignedSelectionCount"),
        removeButton: document.getElementById("openRemoveModal"),
        saveOrderButton: document.getElementById("saveAssignedOrder"),
        assignDialog: document.getElementById("brandAssignDialog"),
        syncDialog: document.getElementById("brandSyncDialog"),
        removeDialog: document.getElementById("brandRemoveDialog"),
        toast: document.getElementById("brandWorkspaceToast"),
        toastText: document.getElementById("brandWorkspaceToastText"),
        toastUndo: document.getElementById("brandToastUndo"),
    };

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function debounce(callback, delay) {
        let timeoutId;
        return function (...args) {
            window.clearTimeout(timeoutId);
            timeoutId = window.setTimeout(() => callback.apply(this, args), delay);
        };
    }

    async function requestJson(url, options = {}) {
        const headers = new Headers(options.headers || {});
        headers.set("X-Requested-With", "XMLHttpRequest");
        if (options.method && options.method.toUpperCase() !== "GET") {
            headers.set("X-CSRFToken", csrfToken);
            headers.set("Content-Type", "application/json");
        }
        const response = await fetch(url, {
            credentials: "same-origin",
            ...options,
            headers,
        });
        let payload;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = { success: false, error: "El servidor devolvió una respuesta inválida." };
        }
        if (!response.ok || payload.success === false) {
            throw new Error(payload.error || "No se pudo completar la operación.");
        }
        return payload;
    }

    function currentFilters() {
        return {
            q: elements.search?.value.trim() || "",
            category_id: elements.category?.value || "",
            supplier: elements.supplier?.value || "",
            status: elements.status?.value || "all",
            stock: elements.stock?.value || "all",
            assignment: elements.assignment?.value || "available",
        };
    }

    function sameFilters(first, second) {
        return JSON.stringify(first || {}) === JSON.stringify(second || {});
    }

    function restoreSelection() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(selectionKey) || "{}");
            state.selected = new Set(
                Array.isArray(saved.ids) ? saved.ids.map((id) => String(id)) : []
            );
            state.selectAll = Boolean(saved.selectAll);
            state.selectAllFilters = saved.filters || null;
            if (state.selectAll && !sameFilters(state.selectAllFilters, currentFilters())) {
                state.selectAll = false;
                state.selectAllFilters = null;
            }
        } catch (_error) {
            state.selected = new Set();
        }
    }

    function persistSelection() {
        sessionStorage.setItem(
            selectionKey,
            JSON.stringify({
                ids: Array.from(state.selected),
                selectAll: state.selectAll,
                filters: state.selectAllFilters,
            })
        );
    }

    function clearSelection() {
        state.selected.clear();
        state.selectAll = false;
        state.selectAllFilters = null;
        persistSelection();
        root.querySelectorAll(".available-product-check").forEach((checkbox) => {
            checkbox.checked = false;
            checkbox.closest(".brand-product-result")?.classList.remove("is-selected");
        });
        updateSelectionDock();
    }

    function updateSelectionDock() {
        const count = state.selectAll ? state.totalCount : state.selected.size;
        if (!elements.selectionDock || !elements.selectionCount) return;
        elements.selectionDock.hidden = count === 0;
        elements.selectionCount.textContent = `${count} ${count === 1 ? "seleccionado" : "seleccionados"}`;
        if (elements.selectionHint) {
            elements.selectionHint.textContent = state.selectAll
                ? "Se aplicará a todos los resultados filtrados."
                : "La selección se conserva al cambiar filtros.";
        }
    }

    function productImage(product) {
        if (product.image_url) {
            return `<img src="${escapeHtml(product.image_url)}" alt="" loading="lazy">`;
        }
        return `
            <span aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none"><path d="M4 5h16v14H4V5Z" stroke="currentColor" stroke-width="1.5"/><path d="m7 16 3.5-4 2.5 3 2-2 2 3M9 9h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </span>
        `;
    }

    function renderAssignments(product) {
        if (!product.assignments?.length) {
            return `<span>Sin marca asignada</span>`;
        }
        return product.assignments
            .slice(0, 3)
            .map(
                (assignment) => `
                    <span class="${product.has_conflict ? "is-conflict" : ""}" title="${escapeHtml(assignment.label)}">
                        ${escapeHtml(assignment.label)}
                    </span>
                `
            )
            .join("");
    }

    function renderProduct(product) {
        const id = String(product.id);
        const checked = state.selectAll || state.selected.has(id);
        const disabled = product.is_associated;
        const suggestion = product.suggestion
            ? `
                <span class="brand-suggestion-chip ${product.suggestion.matches_target ? "" : "is-other"}"
                    title="${escapeHtml(product.suggestion.reason)}">
                    ${product.suggestion.matches_target ? "Recomendado aquí" : escapeHtml(product.suggestion.destination)}
                    · ${escapeHtml(product.suggestion.confidence)}%
                </span>
            `
            : "";
        return `
            <article class="brand-product-result ${checked ? "is-selected" : ""} ${disabled ? "is-associated" : ""}" data-product-id="${id}">
                <label class="brand-check" title="${disabled ? "Ya está asignado aquí" : `Seleccionar ${escapeHtml(product.sku)}`}">
                    <input type="checkbox" class="available-product-check" value="${id}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
                    <span aria-hidden="true"></span>
                </label>
                <div class="brand-product-media">${productImage(product)}</div>
                <div class="brand-product-copy">
                    <div class="brand-product-copy-top">
                        <code>${escapeHtml(product.sku)}</code>
                        <span class="brand-product-state ${product.is_active ? "" : "is-inactive"}">${product.is_active ? "Activo" : "Inactivo"}</span>
                    </div>
                    <h4 title="${escapeHtml(product.name)}">${escapeHtml(product.name)}</h4>
                    <div class="brand-product-meta">
                        <span title="${escapeHtml(product.category)}">${escapeHtml(product.category)}</span>
                        <span title="${escapeHtml(product.supplier)}">${escapeHtml(product.supplier)}</span>
                        <span>${product.tracks_stock ? "Stock controlado" : "Stock opcional"}: ${escapeHtml(product.stock)}</span>
                    </div>
                    <div class="brand-product-associations">${renderAssignments(product)}</div>
                </div>
                <div class="brand-product-side">
                    ${suggestion}
                    ${product.has_conflict ? '<span class="brand-product-state is-inactive">Revisar conflicto</span>' : ""}
                    ${product.is_associated ? '<span class="brand-product-state">Ya asignado</span>' : ""}
                </div>
            </article>
        `;
    }

    function bindResultCheckboxes() {
        elements.results?.querySelectorAll(".available-product-check").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                const id = String(checkbox.value);
                if (state.selectAll) {
                    state.selectAll = false;
                    state.selectAllFilters = null;
                    state.selected = new Set(
                        state.visibleProductIds.filter((visibleId) => {
                            const visibleCheckbox = elements.results.querySelector(
                                `.available-product-check[value="${CSS.escape(visibleId)}"]`
                            );
                            return Boolean(visibleCheckbox?.checked);
                        })
                    );
                }
                if (checkbox.checked) {
                    state.selected.add(id);
                } else {
                    state.selected.delete(id);
                }
                checkbox.closest(".brand-product-result")?.classList.toggle(
                    "is-selected",
                    checkbox.checked
                );
                persistSelection();
                updateSelectionDock();
            });
        });
    }

    function renderEmptyResults() {
        elements.results.innerHTML = `
            <div class="brand-empty-state">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="m15.5 15.5 4.5 4.5M8 10.5h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
                <strong>No encontramos productos</strong>
                <p>Prueba con otro SKU, proveedor o una categoría menos específica.</p>
            </div>
        `;
    }

    async function loadProducts(page = 1, append = false) {
        if (state.loading) return;
        state.loading = true;
        if (!append) {
            elements.results.innerHTML = `
                <div class="brand-loading-state">
                    <span class="brand-spinner" aria-hidden="true"></span>
                    Buscando productos
                </div>
            `;
            state.visibleProductIds = [];
        }
        const params = new URLSearchParams({
            ...currentFilters(),
            page: String(page),
            ajax: "1",
        });
        try {
            const payload = await requestJson(`${config.searchUrl}?${params.toString()}`, {
                method: "GET",
            });
            state.currentPage = payload.page;
            state.totalCount = payload.total_count;
            state.hasMore = payload.has_more;
            const markup = payload.results.map(renderProduct).join("");
            if (!append) {
                elements.results.innerHTML = markup;
            } else {
                elements.results.insertAdjacentHTML("beforeend", markup);
            }
            state.visibleProductIds.push(
                ...payload.results
                    .filter((product) => !product.is_associated)
                    .map((product) => String(product.id))
            );
            if (!elements.results.children.length) renderEmptyResults();
            elements.resultCount.textContent = `${payload.total_count} resultados`;
            elements.heroAvailable.textContent = payload.total_count;
            elements.pageInfo.textContent = payload.total_count
                ? `Mostrando ${Math.min(state.visibleProductIds.length, payload.total_count)} de ${payload.total_count}`
                : "Sin resultados";
            elements.loadMore.hidden = !payload.has_more;
            bindResultCheckboxes();
            updateSelectionDock();
        } catch (error) {
            elements.results.innerHTML = `
                <div class="brand-empty-state">
                    <strong>No pudimos cargar los productos</strong>
                    <p>${escapeHtml(error.message)}</p>
                    <button type="button" class="btn btn-outline" id="retryBrandProducts">Reintentar</button>
                </div>
            `;
            document.getElementById("retryBrandProducts")?.addEventListener("click", () => loadProducts());
        } finally {
            state.loading = false;
        }
    }

    function handleFiltersChanged() {
        if (state.selectAll && !sameFilters(state.selectAllFilters, currentFilters())) {
            state.selectAll = false;
            state.selectAllFilters = null;
            persistSelection();
        }
        loadProducts(1, false);
    }

    function selectionPayload() {
        if (state.selectAll) {
            return {
                select_all: true,
                filters: state.selectAllFilters || currentFilters(),
                product_ids: [],
            };
        }
        return {
            select_all: false,
            filters: currentFilters(),
            product_ids: Array.from(state.selected),
        };
    }

    function openDialog(dialog) {
        if (!dialog) return;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
    }

    function closeDialog(dialog) {
        if (!dialog) return;
        if (typeof dialog.close === "function") dialog.close();
        else dialog.removeAttribute("open");
    }

    function setButtonBusy(button, busy, label) {
        if (!button) return;
        if (busy) {
            button.dataset.previousLabel = button.textContent;
            button.disabled = true;
            button.textContent = label || "Procesando";
        } else {
            button.disabled = false;
            button.textContent = button.dataset.previousLabel || label || "Confirmar";
        }
    }

    function queueFlashAndReload(message, batchId) {
        sessionStorage.setItem(
            flashKey,
            JSON.stringify({ message, batchId: batchId || null })
        );
        window.location.reload();
    }

    function showToast(message, batchId = null) {
        if (!elements.toast) return;
        elements.toastText.textContent = message;
        elements.toast.hidden = false;
        elements.toastUndo.hidden = !batchId;
        elements.toastUndo.dataset.batchId = batchId || "";
    }

    async function undoBatch(batchId) {
        if (!batchId) return;
        const url = config.undoUrlTemplate.replace("__batch__", String(batchId));
        try {
            await requestJson(url, {
                method: "POST",
                body: JSON.stringify({}),
            });
            queueFlashAndReload(`El lote #${batchId} fue deshecho correctamente.`, null);
        } catch (error) {
            showToast(error.message);
        }
    }

    async function submitAssignment(event) {
        event.preventDefault();
        const observation = document.getElementById("brandAssignObservation")?.value.trim();
        if (!observation) {
            document.getElementById("brandAssignObservation")?.focus();
            return;
        }
        const button = document.getElementById("confirmBrandAssignment");
        setButtonBusy(button, true, "Asignando");
        try {
            const payload = await requestJson(config.bulkAddUrl, {
                method: "POST",
                body: JSON.stringify({
                    ...selectionPayload(),
                    observation,
                    mode: document.getElementById("brandAssignMode")?.value || "add",
                }),
            });
            sessionStorage.removeItem(selectionKey);
            queueFlashAndReload(
                `${payload.created_count} producto(s) agregados. ${payload.existing_count} ya estaban en el destino.`,
                payload.batch_id
            );
        } catch (error) {
            showToast(error.message);
            setButtonBusy(button, false);
        }
    }

    function renderSyncPreview(payload) {
        state.syncPreview = payload;
        document.getElementById("brandSyncLoading").hidden = true;
        document.getElementById("brandSyncError").hidden = true;
        document.getElementById("brandSyncPreview").hidden = false;
        document.getElementById("syncCandidateCount").textContent = payload.candidate_count;
        document.getElementById("syncNewCount").textContent = payload.new_count;
        document.getElementById("syncExistingCount").textContent = payload.associated_count;
        document.getElementById("syncConflictCount").textContent = payload.conflict_count;
        const preview = document.getElementById("brandSyncProducts");
        preview.innerHTML = payload.preview.length
            ? payload.preview
                  .map(
                      (product) => `
                        <article class="brand-sync-product">
                            <div>
                                <strong>${escapeHtml(product.sku)} · ${escapeHtml(product.name)}</strong>
                                <small>${escapeHtml(product.category)} · ${escapeHtml(product.supplier)}</small>
                            </div>
                            <span>${product.is_associated ? "Ya asignado" : product.has_conflict ? "Conflicto" : "Nuevo"}</span>
                        </article>
                    `
                  )
                  .join("")
            : '<div class="brand-empty-state"><strong>No hay coincidencias</strong><p>Revisa las categorías ayudantes, alias y reglas de la marca.</p></div>';
        const confirmButton = document.getElementById("confirmBrandSync");
        confirmButton.disabled = payload.new_count === 0;
    }

    async function openSyncPreview() {
        const loading = document.getElementById("brandSyncLoading");
        const preview = document.getElementById("brandSyncPreview");
        const error = document.getElementById("brandSyncError");
        loading.hidden = false;
        preview.hidden = true;
        error.hidden = true;
        document.getElementById("confirmBrandSync").disabled = true;
        openDialog(elements.syncDialog);
        try {
            const payload = await requestJson(config.syncUrl, {
                method: "POST",
                body: JSON.stringify({ action: "preview" }),
            });
            renderSyncPreview(payload);
        } catch (requestError) {
            loading.hidden = true;
            error.hidden = false;
            error.textContent = requestError.message;
        }
    }

    async function submitSync(event) {
        event.preventDefault();
        const observation = document.getElementById("brandSyncObservation")?.value.trim();
        if (!observation) {
            document.getElementById("brandSyncObservation")?.focus();
            return;
        }
        const button = document.getElementById("confirmBrandSync");
        setButtonBusy(button, true, "Sincronizando");
        try {
            const payload = await requestJson(config.syncUrl, {
                method: "POST",
                body: JSON.stringify({
                    action: "confirm",
                    mode: document.getElementById("brandSyncMode")?.value || "add",
                    observation,
                }),
            });
            queueFlashAndReload(
                `${payload.created_count} producto(s) incorporados mediante sincronización revisada.`,
                payload.batch_id
            );
        } catch (error) {
            showToast(error.message);
            setButtonBusy(button, false);
        }
    }

    function updateAssignedSelection() {
        const count = state.assignedSelection.size;
        if (elements.assignedCount) {
            elements.assignedCount.textContent = `${count} ${count === 1 ? "seleccionado" : "seleccionados"}`;
        }
        if (elements.removeButton) elements.removeButton.disabled = count === 0;
    }

    function bindAssignedCheckboxes() {
        root.querySelectorAll(".assigned-product-check").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                const id = String(checkbox.value);
                if (checkbox.checked) state.assignedSelection.add(id);
                else state.assignedSelection.delete(id);
                updateAssignedSelection();
            });
        });
    }

    async function submitRemoval(event) {
        event.preventDefault();
        const observation = document.getElementById("brandRemoveObservation")?.value.trim();
        if (!observation) {
            document.getElementById("brandRemoveObservation")?.focus();
            return;
        }
        const button = document.getElementById("confirmBrandRemoval");
        setButtonBusy(button, true, "Quitando");
        try {
            const payload = await requestJson(config.bulkRemoveUrl, {
                method: "POST",
                body: JSON.stringify({
                    product_ids: Array.from(state.assignedSelection),
                    observation,
                }),
            });
            queueFlashAndReload(
                `${payload.removed_count} producto(s) retirados del destino.`,
                payload.batch_id
            );
        } catch (error) {
            showToast(error.message);
            setButtonBusy(button, false);
        }
    }

    function refreshAssignedPositions() {
        const rows = Array.from(
            elements.assignedList?.querySelectorAll(".brand-assigned-row") || []
        );
        rows.forEach((row, index) => {
            const input = row.querySelector(".brand-position-input");
            if (input) input.value = index + 1;
        });
    }

    function markOrderDirty() {
        state.orderDirty = true;
        if (elements.saveOrderButton) {
            elements.saveOrderButton.disabled = false;
            elements.saveOrderButton.textContent = "Guardar orden pendiente";
        }
        refreshAssignedPositions();
    }

    function bindAssignedOrdering() {
        if (!elements.assignedList) return;
        let draggedRow = null;
        elements.assignedList.querySelectorAll(".brand-assigned-row").forEach((row) => {
            row.addEventListener("dragstart", (event) => {
                draggedRow = row;
                row.classList.add("is-dragging");
                event.dataTransfer.effectAllowed = "move";
            });
            row.addEventListener("dragend", () => {
                row.classList.remove("is-dragging");
                draggedRow = null;
                markOrderDirty();
            });
            row.querySelector(".brand-position-input")?.addEventListener("change", (event) => {
                const rows = Array.from(elements.assignedList.querySelectorAll(".brand-assigned-row"));
                const desired = Math.max(
                    1,
                    Math.min(Number.parseInt(event.target.value, 10) || 1, rows.length)
                );
                const reference = rows[desired - 1];
                if (reference && reference !== row) {
                    if (desired > rows.indexOf(row) + 1) reference.after(row);
                    else reference.before(row);
                }
                markOrderDirty();
            });
        });
        elements.assignedList.addEventListener("dragover", (event) => {
            event.preventDefault();
            if (!draggedRow) return;
            const target = event.target.closest(".brand-assigned-row");
            if (!target || target === draggedRow) return;
            const rect = target.getBoundingClientRect();
            if (event.clientY < rect.top + rect.height / 2) target.before(draggedRow);
            else target.after(draggedRow);
        });
    }

    async function saveAssignedOrder() {
        if (!state.orderDirty) return;
        const orderedIds = Array.from(
            elements.assignedList.querySelectorAll(".brand-assigned-row")
        ).map((row) => row.dataset.productId);
        setButtonBusy(elements.saveOrderButton, true, "Guardando");
        try {
            await requestJson(config.reorderUrl, {
                method: "POST",
                body: JSON.stringify({ ordered_ids: orderedIds }),
            });
            state.orderDirty = false;
            elements.saveOrderButton.textContent = "Orden guardado";
            window.setTimeout(() => {
                elements.saveOrderButton.textContent = "Guardar orden";
                elements.saveOrderButton.disabled = true;
            }, 1200);
        } catch (error) {
            showToast(error.message);
            setButtonBusy(elements.saveOrderButton, false, "Guardar orden");
        }
    }

    function restoreFlash() {
        try {
            const flash = JSON.parse(sessionStorage.getItem(flashKey) || "null");
            sessionStorage.removeItem(flashKey);
            if (flash?.message) showToast(flash.message, flash.batchId);
        } catch (_error) {
            sessionStorage.removeItem(flashKey);
        }
    }

    const debouncedFilter = debounce(handleFiltersChanged, 260);
    elements.search?.addEventListener("input", debouncedFilter);
    [elements.category, elements.supplier, elements.status, elements.stock, elements.assignment]
        .filter(Boolean)
        .forEach((control) => control.addEventListener("change", handleFiltersChanged));

    elements.loadMore?.addEventListener("click", () => {
        loadProducts(state.currentPage + 1, true);
    });
    document.getElementById("selectVisibleProducts")?.addEventListener("click", () => {
        state.selectAll = false;
        state.selectAllFilters = null;
        state.visibleProductIds.forEach((id) => state.selected.add(String(id)));
        persistSelection();
        root.querySelectorAll(".available-product-check:not(:disabled)").forEach((checkbox) => {
            checkbox.checked = true;
            checkbox.closest(".brand-product-result")?.classList.add("is-selected");
        });
        updateSelectionDock();
    });
    document.getElementById("selectAllFilteredProducts")?.addEventListener("click", () => {
        state.selectAll = true;
        state.selectAllFilters = currentFilters();
        state.selected.clear();
        persistSelection();
        root.querySelectorAll(".available-product-check:not(:disabled)").forEach((checkbox) => {
            checkbox.checked = true;
            checkbox.closest(".brand-product-result")?.classList.add("is-selected");
        });
        updateSelectionDock();
    });
    document.getElementById("clearAvailableSelection")?.addEventListener("click", clearSelection);
    document.getElementById("openAssignModal")?.addEventListener("click", () => {
        const count = state.selectAll ? state.totalCount : state.selected.size;
        document.getElementById("brandAssignSummary").textContent =
            `Se asignarán ${count} producto(s) a este destino. Los productos ya vinculados no se duplicarán.`;
        openDialog(elements.assignDialog);
    });
    document.getElementById("brandAssignForm")?.addEventListener("submit", submitAssignment);
    document.getElementById("openSyncPreview")?.addEventListener("click", openSyncPreview);
    document.getElementById("brandSyncForm")?.addEventListener("submit", submitSync);

    elements.assignedSearch?.addEventListener("input", () => {
        const query = elements.assignedSearch.value.trim().toLowerCase();
        elements.assignedList.querySelectorAll(".brand-assigned-row").forEach((row) => {
            row.classList.toggle("is-filtered", Boolean(query) && !row.dataset.search.includes(query));
        });
    });
    document.getElementById("selectAllAssigned")?.addEventListener("click", () => {
        const visibleChecks = Array.from(
            elements.assignedList.querySelectorAll(
                ".brand-assigned-row:not(.is-filtered) .assigned-product-check"
            )
        );
        const shouldSelect = visibleChecks.some((checkbox) => !checkbox.checked);
        visibleChecks.forEach((checkbox) => {
            checkbox.checked = shouldSelect;
            if (shouldSelect) state.assignedSelection.add(String(checkbox.value));
            else state.assignedSelection.delete(String(checkbox.value));
        });
        updateAssignedSelection();
    });
    elements.removeButton?.addEventListener("click", () => {
        const count = state.assignedSelection.size;
        document.getElementById("brandRemoveSummary").textContent =
            `Se retirarán ${count} producto(s) de este destino. El cambio quedará disponible para deshacer.`;
        openDialog(elements.removeDialog);
    });
    document.getElementById("brandRemoveForm")?.addEventListener("submit", submitRemoval);
    elements.saveOrderButton?.addEventListener("click", saveAssignedOrder);

    root.querySelectorAll("[data-close-dialog]").forEach((button) => {
        button.addEventListener("click", () => closeDialog(button.closest("dialog")));
    });
    root.querySelectorAll(".brand-workspace-dialog").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) closeDialog(dialog);
        });
    });
    root.querySelectorAll(".brand-undo-batch").forEach((button) => {
        button.addEventListener("click", () => {
            if (window.confirm(`¿Deshacer el lote #${button.dataset.batchId}?`)) {
                undoBatch(button.dataset.batchId);
            }
        });
    });
    elements.toastUndo?.addEventListener("click", () => {
        undoBatch(elements.toastUndo.dataset.batchId);
    });
    document.getElementById("brandToastClose")?.addEventListener("click", () => {
        elements.toast.hidden = true;
    });

    restoreSelection();
    restoreFlash();
    bindAssignedCheckboxes();
    bindAssignedOrdering();
    updateAssignedSelection();
    updateSelectionDock();
    loadProducts();
})();
