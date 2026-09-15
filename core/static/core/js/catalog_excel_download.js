/* Keep catalog browsing responsive while the dedicated worker prepares Excel. */
(function () {
    'use strict';
    window.handleExcelDownload = async function (event, element) {
        if (event) event.preventDefault();
        if (element.dataset.exportBusy === '1') return;
        element.dataset.exportBusy = '1';
        element.classList.add('disabled');
        element.setAttribute('aria-busy', 'true');
        element.setAttribute('aria-disabled', 'true');
        const label = element.querySelector('span') || element;
        const originalLabel = label.textContent;
        let feedback = element.parentNode.querySelector('[data-excel-feedback]');
        if (!feedback) {
            feedback = document.createElement('p');
            feedback.dataset.excelFeedback = '1';
            feedback.className = 'text-muted';
            feedback.setAttribute('role', 'status');
            element.insertAdjacentElement('afterend', feedback);
        }
        const statusUrl = new URL(element.href, window.location.href);
        statusUrl.searchParams.set('status', '1');
        const deadline = Date.now() + 600000;
        try {
            feedback.textContent = 'Preparando el Excel. Podes seguir navegando por el catalogo.';
            while (Date.now() < deadline) {
                label.textContent = 'Preparando archivo...';
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                let response;
                try {
                    response = await fetch(statusUrl, {
                        credentials: 'same-origin', cache: 'no-store',
                        headers: {Accept: 'application/json'}, signal: controller.signal,
                    });
                } finally {
                    clearTimeout(timer);
                }
                if (response.redirected || !(response.headers.get('content-type') || '').includes('application/json')) {
                    throw new Error('Revisa tu sesion y los permisos de descarga antes de volver a intentar.');
                }
                const state = await response.json();
                if (!response.ok || state.status === 'failed') {
                    throw new Error(state.message || 'No se pudo preparar el Excel. Volve a intentar en unos segundos.');
                }
                if (state.status === 'ready') {
                    feedback.textContent = 'Excel listo. Iniciando descarga...';
                    setTimeout(() => {
                        if (feedback.textContent === 'Excel listo. Iniciando descarga...') feedback.textContent = '';
                    }, 6000);
                    window.location.assign(element.href);
                    return;
                }
                await new Promise(resolve => setTimeout(resolve, 3000));
            }
            throw new Error('La preparacion esta tardando mas de lo esperado. Volve a intentar la descarga.');
        } catch (error) {
            feedback.textContent = error.name === 'AbortError'
                ? 'La conexion demoro demasiado. Volve a intentar la descarga.'
                : (error.message || 'No se pudo conectar. Volve a intentar.');
        } finally {
            delete element.dataset.exportBusy;
            element.classList.remove('disabled');
            element.removeAttribute('aria-busy');
            element.removeAttribute('aria-disabled');
            label.textContent = originalLabel;
        }
    };
    document.addEventListener('DOMContentLoaded', function () {
        const pending = document.querySelector('[data-excel-auto-download]');
        if (pending) window.handleExcelDownload(null, pending);
    });
}());
