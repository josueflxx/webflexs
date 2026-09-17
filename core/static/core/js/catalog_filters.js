(() => {
    "use strict";
    const form = document.getElementById("technicalFiltersForm");
    const data = document.getElementById("clampFilterCombinations");
    const optionsData = document.getElementById("clampFilterOptions");
    if (!form || !data || !optionsData) return;
    const combinations = JSON.parse(data.textContent);
    const allOptions = JSON.parse(optionsData.textContent);
    const selects = Array.from(form.querySelectorAll("[data-clamp-filter]"));
    const status = document.getElementById("technicalFilterStatus");
    const initial = new URLSearchParams(new FormData(form)).toString();
    function calculate(values) {
        let total = 0;
        const counts = selects.map(() => new Map());
        for (const row of combinations) {
            const compatible = values.map((value, index) => !value || value === row[index]);
            if (compatible.every(Boolean)) total += row[5];
            selects.forEach((select, index) => {
                if (compatible.every((matches, other) => other === index || matches)) {
                    counts[index].set(row[index], (counts[index].get(row[index]) || 0) + row[5]);
                }
            });
        }
        return { total, counts };
    }

    function updateCounts(resetIncompatible = false) {
        const values = selects.map(select => select.value);
        let { total, counts } = calculate(values);

        // A stale downstream selection should not leave the form in a dead-end
        // after the user changes an upstream filter. URL values from the server
        // are kept on first render so the user can see and clear them explicitly.
        if (resetIncompatible) {
            let cleared = false;
            selects.forEach((select, index) => {
                if (select.value && (counts[index].get(select.value) || 0) === 0) {
                    select.value = "";
                    cleared = true;
                }
            });
            if (cleared) {
                ({ total, counts } = calculate(selects.map(select => select.value)));
            }
        }

        selects.forEach((select, index) => {
            const selected = select.value;
            const options = allOptions[select.name] || [];
            const fragment = document.createDocumentFragment();
            const all = document.createElement("option");
            all.value = "";
            all.textContent = "Todos";
            fragment.appendChild(all);
            options.forEach(optionData => {
                const count = counts[index].get(optionData.value) || 0;
                if (count > 0 || (!resetIncompatible && optionData.value === selected)) {
                    const option = document.createElement("option");
                    option.value = optionData.value;
                    option.dataset.label = optionData.label;
                    option.textContent = optionData.label + " (" + count + ")";
                    option.selected = optionData.value === selected;
                    fragment.appendChild(option);
                }
            });
            select.replaceChildren(fragment);
            if (selected && Array.from(select.options).some(option => option.value === selected)) {
                select.value = selected;
            }
        });
        const changed = initial !== new URLSearchParams(new FormData(form)).toString();
        status.dataset.empty = String(total === 0);
        status.textContent = total === 0
            ? "Sin coincidencias para esta combinación. Cambiá una medida o limpiá los filtros."
            : total.toLocaleString("es-AR") + (total === 1 ? " producto compatible." : " productos compatibles.") + (changed ? " Presioná Aplicar para verlos." : "");
    }
    form.addEventListener("change", () => updateCounts(true));
    updateCounts(false);
})();
