/* Business measuring convention shared with the approved 3D guides.
 * Coordinates describe the illustration, never a customer's order dimensions.
 */
(() => {
    const root = document.getElementById('measurementGuide');
    if (!root) return;
    const profiles = {
        curva: { name: 'Curva', path: 'M174 450V168A86 86 0 0 1 346 168V450' },
        semicurva: { name: 'Semicurva', path: 'M174 450V139A86 57 0 0 1 346 139V450' },
        plana: { name: 'Plana', path: 'M174 450V130A48 48 0 0 1 222 82H298A48 48 0 0 1 346 130V450' },
    };
    const interior = 'Desde el extremo interior de una pata hasta el punto medio de la cara interior del arco, siguiendo la línea inclinada.';
    const flat = 'Medí en vertical desde la cara interior del techo plano hasta el extremo de las patas.';
    const measures = {
        a: { title: 'Diámetro de varilla', text: 'Medí la sección roscada con un calibre. Usá las equivalencias de abajo para expresar el diámetro en pulgadas.' },
        b: { title: 'Ancho interior', text: 'Apoyá la regla entre las caras internas de las patas. El espesor de la varilla queda fuera de la medida B.' },
        d: { title: 'Largo de rosca', text: 'Tomá el tramo roscado completo desde el extremo de la pata. La posición de las tuercas y la plaqueta no cambia esta referencia.' },
    };
    let shape = 'curva';
    let focused = 'all';
    const get = id => document.getElementById(id);
    function attributes(element, values) {
        Object.entries(values).forEach(([key, value]) => element.setAttribute(key, value));
    }
    function updateTip() {
        const key = focused === 'all' ? 'c' : focused;
        const info = key === 'c'
            ? { title: `Cómo tomar el largo en la ${shape}`, text: shape === 'plana' ? flat : interior }
            : measures[key];
        get('mgTipLetter').textContent = key.toUpperCase();
        get('mgTipTitle').textContent = info.title;
        get('shapeDesc').textContent = info.text;
    }
    function selectShape(next) {
        if (!profiles[next]) return;
        shape = next;
        const profile = profiles[shape];
        const isFlat = shape === 'plana';
        root.dataset.shape = shape;
        root.querySelectorAll('button[data-shape]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.shape === shape)));
        get('uBoltBody').setAttribute('d', profile.path);
        get('uBoltShine').setAttribute('d', profile.path);
        // Curva/semicurva: inner left tip -> inner crown midpoint.
        // Plana: preserve the external vertical length dimension.
        const points = isFlat ? { x1: 100, y1: 450, x2: 100, y2: 93 } : { x1: 185, y1: 450, x2: 260, y2: 93 };
        attributes(get('lengthLine'), points);
        attributes(get('lengthStart'), { cx: points.x1, cy: points.y1 });
        attributes(get('lengthEnd'), { cx: points.x2, cy: points.y2 });
        get('lengthWitnesses').setAttribute('visibility', isFlat ? 'visible' : 'hidden');
        get('lengthLabel').setAttribute('transform', isFlat ? 'translate(82 263)' : 'translate(220 195)');
        get('shapeTitle').textContent = `Abrazadera ${shape}`;
        get('mgSvgTitle').textContent = `Medidas de una abrazadera ${shape}`;
        get('mgSvgDesc').textContent = `A: diámetro de varilla. B: ancho entre caras internas. C: ${isFlat ? flat : interior} D: longitud de la rosca.`;
        get('mgLengthBadge').textContent = profile.name;
        get('mgLengthInstruction').textContent = isFlat ? flat : interior;
        get('mgLengthHint').textContent = isFlat ? 'Tomá como referencia la superficie plana interior.' : 'Los dos puntos de referencia están sobre la cara interior.';
        updateTip();
    }
    function selectMeasure(next) {
        focused = next;
        root.dataset.measure = next;
        root.querySelectorAll('[data-focus]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.focus === next)));
        root.querySelectorAll('[data-dimension]').forEach(group => group.classList.toggle('is-muted', next !== 'all' && group.dataset.dimension !== next));
        updateTip();
    }
    root.querySelector('.mg-profiles').addEventListener('click', event => {
        const button = event.target.closest('button[data-shape]');
        if (button) selectShape(button.dataset.shape);
    });
    root.querySelectorAll('[data-focus]').forEach(button => button.addEventListener('click', () => {
        selectMeasure(button.dataset.focus);
        if (button.classList.contains('mg-step') && matchMedia('(max-width: 800px)').matches) {
            root.querySelector('.mg-board').scrollIntoView({
                behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
                block: 'start',
            });
        }
    }));
    root.querySelectorAll('.mg-profiles, .mg-measures').forEach(control => { control.hidden = false; });
    selectShape('curva');
})();
