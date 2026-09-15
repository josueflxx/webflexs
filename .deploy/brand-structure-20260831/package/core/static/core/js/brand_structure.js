(() => {
    'use strict';
    const form = document.getElementById('brand-structure-form');
    if (!form) return;
    const operation = form.elements.operation;
    const search = form.querySelector('#structure-brand-search');
    const options = [...form.querySelectorAll('[data-brand-option]')];
    const normalize = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    const refresh = () => {
        const editing = operation.value.startsWith('update_');
        const all = form.elements.scope.value === 'all';
        const inactive = form.elements.include_inactive.checked;
        form.querySelectorAll('[data-modes]').forEach((node) => {
            node.hidden = !node.dataset.modes.split(' ').includes(operation.value);
        });
        form.querySelector('[data-selected-brands]').hidden = all;
        form.querySelectorAll('input, select, textarea, button').forEach((control) => {
            const patch = control.closest('[data-patch-field]');
            control.disabled = Boolean(control.closest('[hidden]')) || Boolean(editing && patch && !form.elements[`change_${patch.dataset.patchField}`].checked);
        });
        let selected = 0;
        options.forEach((option) => {
            const checkbox = option.querySelector('input');
            const eligible = inactive || option.dataset.active === 'true';
            option.hidden = !eligible || (!all && !normalize(option.dataset.name).includes(normalize(search.value)));
            checkbox.disabled = all || !eligible;
            checkbox.hidden = all;
            option.querySelector('[data-all-marker]').hidden = !all;
            if (eligible && (all || checkbox.checked)) selected += 1;
            option.style.opacity = eligible ? '' : '.5';
        });
        form.querySelector('[data-brand-count]').textContent = `${selected} marca${selected === 1 ? '' : 's'} en el lote${all ? ' · Todas las elegibles' : ''}`;
    };
    form.addEventListener('change', refresh);
    search.addEventListener('input', refresh);
    search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') event.preventDefault();
    });
    form.querySelector('[data-select-visible]').addEventListener('click', () => {
        options.filter((option) => !option.hidden).forEach((option) => { option.querySelector('input').checked = true; });
        refresh();
    });
    form.querySelector('[data-clear-brands]').addEventListener('click', () => {
        options.forEach((option) => { option.querySelector('input').checked = false; });
        refresh();
    });
    form.addEventListener('submit', () => {
        // Keep the posted fields intact. Only prevent accidental repeated clicks.
        const submit = form.querySelector('button[type="submit"]');
        submit.disabled = true;
        submit.textContent = 'Preparando vista previa…';
    });
    refresh();
})();
