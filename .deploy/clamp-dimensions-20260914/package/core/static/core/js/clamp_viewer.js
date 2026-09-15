/* The 3D runtime and model are fetched only after an explicit user action. */
(() => {
    const dimensionsModule = new URL('clamp_dimensions.js?v=20260914-1', document.currentScript.src).href;
    const panel = document.getElementById('clamp3dPanel');
    if (!panel) return;
    const drawing = document.getElementById('clampDrawing');
    const openButton = document.getElementById('clampShow3d');
    const drawingButton = document.getElementById('clampShowDrawing');
    const status = document.getElementById('clamp3dStatus');
    const retry = document.getElementById('clamp3dRetry');
    const reset = document.getElementById('clamp3dReset');
    let viewer;
    let loading = false;
    let attempts = 0;
    let clearDimensions;

    function assetUrl(path) {
        const url = new URL(path, document.baseURI);
        // Failed model loads are cached by the runtime; retries need a fresh URL.
        if (attempts > 1) url.searchParams.set('retry', String(attempts));
        return url.href;
    }

    function selectView(show3d) {
        drawing.hidden = show3d;
        panel.hidden = !show3d;
        openButton.setAttribute('aria-pressed', String(show3d));
        drawingButton.setAttribute('aria-pressed', String(!show3d));
    }

    function fail() {
        clearDimensions?.();
        clearDimensions = null;
        loading = false;
        panel.setAttribute('aria-busy', 'false');
        status.hidden = false;
        status.textContent = 'No se pudo cargar la vista 3D. Podés reintentar o volver al plano.';
        retry.hidden = false;
        reset.hidden = true;
        if (viewer) viewer.remove();
        viewer = null;
    }

    async function loadViewer() {
        if (loading || viewer) return;
        loading = true;
        attempts += 1;
        retry.hidden = true;
        status.hidden = false;
        status.textContent = 'Cargando abrazadera 3D…';
        panel.setAttribute('aria-busy', 'true');
        try {
            if (!customElements.get('model-viewer')) {
                await import(assetUrl(panel.dataset.runtime));
            }
            viewer = document.createElement('model-viewer');
            const currentViewer = viewer;
            viewer.setAttribute('alt', 'Abrazadera curva de acero con dos extremos roscados. Arrastrá para girar y usá la rueda o dos dedos para acercar.');
            viewer.setAttribute('camera-controls', '');
            viewer.setAttribute('disable-pan', '');
            viewer.setAttribute('touch-action', 'pan-y');
            viewer.setAttribute('camera-orbit', '30deg 75deg 105%');
            viewer.setAttribute('min-camera-orbit', 'auto auto 35%');
            viewer.setAttribute('max-camera-orbit', 'auto auto 200%');
            viewer.setAttribute('interaction-prompt', 'none');
            viewer.setAttribute('environment-image', 'neutral');
            viewer.setAttribute('exposure', '1.2');
            viewer.setAttribute('loading', 'eager');
            viewer.addEventListener('load', async () => {
                if (viewer !== currentViewer) return;
                loading = false;
                panel.setAttribute('aria-busy', 'false');
                status.hidden = true;
                reset.hidden = false;
                try {
                    const { attachClampDimensions } = await import(dimensionsModule);
                    if (viewer === currentViewer) clearDimensions = attachClampDimensions(viewer, {
                        variant: 'curva', controls: panel.querySelector('.clamp-3d-footer'),
                        footer: panel.querySelector('.clamp-3d-footer'),
                    });
                } catch (error) {
                    console.warn('No se pudieron cargar las indicaciones de medidas.', error);
                }
            }, { once: true });
            viewer.addEventListener('error', () => {
                if (viewer === currentViewer) fail();
            }, { once: true });
            viewer.setAttribute('src', assetUrl(panel.dataset.model));
            document.getElementById('clamp3dStage').append(viewer);
        } catch (error) {
            fail();
        }
    }

    openButton.addEventListener('click', () => {
        selectView(true);
        loadViewer();
    });
    drawingButton.addEventListener('click', () => selectView(false));
    retry.addEventListener('click', loadViewer);
    reset.addEventListener('click', () => {
        if (viewer) viewer.cameraOrbit = '30deg 75deg 105%';
    });
    document.getElementById('clampViewControls').hidden = false;
})();
