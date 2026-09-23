(function () {
    "use strict";
    const root = document.getElementById("brandCatalog");
    if (!root) return;
    const search = root.querySelector("#brandProductSearch");
    const products = root.querySelector("#brandProducts");
    const cards = Array.from(root.querySelectorAll(".bc-product"));
    const count = root.querySelector("#brandProductCount");
    const countLabel = root.querySelector("#brandProductCountLabel");
    const empty = root.querySelector("#brandSearchEmpty");
    const clear = root.querySelector("#brandClearSearch");
    const feedback = root.querySelector("#brandCatalogFeedback");
    const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    const params = new URLSearchParams(location.search);
    if (search) search.value = params.get("q") || "";
    function rememberResults() {
        const url = new URL(location.href);
        if (search?.value.trim()) url.searchParams.set("q", search.value.trim());
        else url.searchParams.delete("q");
        if (products) url.searchParams.set("view", products.dataset.view);
        history.replaceState(null, "", url.pathname + url.search);
        root.querySelectorAll('a[href*="/catalogo/producto/"]').forEach(link => {
            const target = new URL(link.href);
            target.searchParams.set("next", url.pathname + url.search);
            link.href = target.pathname + target.search;
        });
    }
    if (products && ["list", "grid"].includes(params.get("view"))) {
        products.dataset.view = params.get("view");
        root.querySelectorAll("[data-bc-view]").forEach(button => {
            const active = button.dataset.bcView === products.dataset.view;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", String(active));
        });
    }

    function filterProducts() {
        const query = normalize(search?.value).trim();
        const words = query.split(/\s+/).filter(Boolean);
        let visible = 0;
        cards.forEach(card => {
            const haystack = normalize(`${card.dataset.name} ${card.dataset.sku}`);
            card.hidden = !words.every(word => haystack.includes(word));
            if (!card.hidden) visible++;
        });
        if (count) count.textContent = visible;
        if (countLabel) countLabel.textContent = visible === 1 ? "resultado" : "resultados";
        if (empty) empty.hidden = visible > 0 || !query;
        if (clear) clear.hidden = !search.value;
        rememberResults();
    }

    function showFeedback(message, isError = false) {
        if (!feedback) return;
        feedback.textContent = message;
        feedback.classList.toggle("is-error", isError);
        feedback.hidden = false;
    }

    search?.addEventListener("input", filterProducts);
    search?.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            search.value = "";
            filterProducts();
        }
    });
    root.addEventListener("click", async event => {
        const clearButton = event.target.closest("#brandClearSearch, [data-bc-clear]");
        if (clearButton && search) {
            search.value = "";
            filterProducts();
            search.focus();
            return;
        }
        const viewButton = event.target.closest("[data-bc-view]");
        if (viewButton && products) {
            products.dataset.view = viewButton.dataset.bcView;
            root.querySelectorAll("[data-bc-view]").forEach(button => {
                const active = button === viewButton;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            rememberResults();
            return;
        }
        const button = event.target.closest("[data-bc-action]");
        if (!button || button.disabled) return;
        const card = button.closest(".bc-product");
        const action = button.dataset.bcAction;
        if (!card || !["cart", "favorite"].includes(action)) return;
        const previousText = button.textContent;
        button.disabled = true;
        button.textContent = action === "cart" ? "Agregando…" : "Guardando…";
        try {
            const response = await fetch(action === "cart" ? root.dataset.cartUrl : root.dataset.favoriteUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": root.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
                },
                body: JSON.stringify({ product_id: Number(card.dataset.productId), ...(action === "cart" ? { quantity: 1 } : {}) }),
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || "No se pudo completar la acción.");
            if (action === "cart") {
                const badge = document.getElementById("cartBadge");
                if (badge) badge.textContent = data.cart_count;
                button.textContent = previousText;
                showFeedback(`${card.querySelector("h3").textContent.trim()} se agregó al carrito.`);
            } else {
                button.dataset.favorite = data.is_favorite ? "1" : "0";
                button.setAttribute("aria-pressed", String(!!data.is_favorite));
                button.textContent = data.is_favorite ? "Favorito" : "Guardar favorito";
                showFeedback(data.is_favorite ? "Producto guardado en favoritos." : "Producto retirado de favoritos.");
            }
        } catch (error) {
            button.textContent = previousText;
            showFeedback(error instanceof SyntaxError ? "No se pudo completar la acción. Recargá la página e intentá de nuevo." : error.message, true);
        } finally {
            button.disabled = false;
        }
    });
    if (search && products) filterProducts();
})();
