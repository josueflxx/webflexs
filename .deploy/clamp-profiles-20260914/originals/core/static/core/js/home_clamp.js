/* Homepage product showcase: reuse the local viewer, load when visible. */
(() => {
    const dimensionsModule = new URL('clamp_dimensions.js?v=20260914-1', document.currentScript.src).href;
    const showcase = document.getElementById('homeClamp');
    if (!showcase) return;
    const stage = showcase.querySelector('.home-clamp-stage');
    const poster = showcase.querySelector('.home-clamp-poster');
    const status = showcase.querySelector('[role="status"]');
    const loadButton = showcase.querySelector('[data-load]');
    const controls = showcase.querySelector('.home-clamp-controls');
    const orbit = '25deg 78deg 115%';
    let viewer = null;
    let loading = false;
    let attempts = 0;
    let clearDimensions;

    function url(path) {
        const result = new URL(path, document.baseURI);
        if (attempts > 1) result.searchParams.set('retry', attempts);
        return result.href;
    }

    function fail() {
        clearDimensions?.();
        clearDimensions = null;
        loading = false;
        showcase.setAttribute('aria-busy', 'false');
        showcase.classList.remove('is-ready');
        poster.hidden = false;
        controls.hidden = true;
        status.textContent = 'Vista 3D no disponible en este momento.';
        loadButton.textContent = 'Reintentar vista 3D';
        loadButton.hidden = false;
        viewer?.remove();
        viewer = null;
    }

    async function load() {
        if (loading || viewer) return;
        loading = true;
        attempts += 1;
        loadButton.hidden = true;
        status.textContent = 'Preparando vista 3D…';
        showcase.setAttribute('aria-busy', 'true');
        try {
            if (!customElements.get('model-viewer')) await import(url(showcase.dataset.runtime));
            viewer = document.createElement('model-viewer');
            const currentViewer = viewer;
            const attributes = {
                alt: 'Abrazadera FLEXS de acero con plaqueta y dos tuercas. Modelo 3D interactivo.',
                'camera-controls': '', 'disable-pan': '', 'touch-action': 'pan-y',
                'camera-orbit': orbit, 'min-camera-orbit': 'auto auto 45%',
                'max-camera-orbit': 'auto auto 180%', 'interaction-prompt': 'none',
                // Keep the .hdr suffix: the runtime uses it to select its decoder.
                'environment-image': new URL(showcase.dataset.environment, document.baseURI).href,
                'tone-mapping': 'aces', exposure: '1.05',
                'shadow-intensity': '1.3', 'shadow-softness': '0.9', loading: 'eager',
            };
            for (const [name, value] of Object.entries(attributes)) viewer.setAttribute(name, value);
            viewer.addEventListener('load', async () => {
                if (viewer !== currentViewer) return;
                for (const material of viewer.model.materials) {
                    material.pbrMetallicRoughness.setMetallicFactor(1);
                    material.pbrMetallicRoughness.setRoughnessFactor(0.24);
                    material.pbrMetallicRoughness.setBaseColorFactor([0.62, 0.66, 0.72, 1]);
                }
                loading = false;
                showcase.setAttribute('aria-busy', 'false');
                showcase.classList.add('is-ready');
                poster.hidden = true;
                status.textContent = 'Arrastrá para girar · Rueda o dos dedos para acercar';
                controls.hidden = false;
                try {
                    const { attachClampDimensions } = await import(dimensionsModule);
                    if (viewer === currentViewer) clearDimensions = attachClampDimensions(viewer, {
                        variant: showcase.dataset.variant || 'assembly', controls,
                        footer: showcase.querySelector('.home-clamp-footer'),
                    });
                } catch (error) {
                    // Viewing and camera controls remain usable if annotations cannot load.
                    console.warn('No se pudieron cargar las indicaciones de medidas.', error);
                }
            }, { once: true });
            viewer.addEventListener('error', () => {
                if (viewer === currentViewer) fail();
            }, { once: true });
            viewer.src = url(showcase.dataset.model);
            stage.append(viewer);
        } catch (error) {
            fail();
        }
    }

    loadButton.hidden = false;
    loadButton.addEventListener('click', load);
    showcase.querySelector('[data-reset]').addEventListener('click', () => {
        if (viewer) viewer.cameraOrbit = orbit;
    });
    showcase.querySelectorAll('[data-zoom]').forEach(button => {
        button.addEventListener('click', () => {
            if (!viewer) return;
            const camera = viewer.getCameraOrbit();
            const factor = button.dataset.zoom === 'in' ? 0.85 : 1.18;
            viewer.cameraOrbit = `${camera.theta}rad ${camera.phi}rad ${camera.radius * factor}m`;
        });
    });
    // Save-data visitors opt in. Everyone else sees the model as it enters view.
    if (!navigator.connection?.saveData && 'IntersectionObserver' in window) {
        const observer = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) {
                observer.disconnect();
                if ('requestIdleCallback' in window) requestIdleCallback(load, { timeout: 1500 });
                else setTimeout(load, 0);
            }
        }, { threshold: 0.1 });
        observer.observe(showcase);
    }
})();
