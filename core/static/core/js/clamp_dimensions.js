/* Dimension anchors use model coordinates in metres, never form input values.
 * The GLBs are illustrative fixed models. Screen-space lines follow the camera.
 */
const NS = 'http://www.w3.org/2000/svg';
let instance = 0;

export function attachClampDimensions(viewer, { variant = 'plana', controls, footer, initiallyVisible = false }) {
    const interiorLength = variant === 'curva' || variant === 'semicurva';
    const half = 0.045;
    const rod = 0.00635;
    const ceiling = 0.25365;
    const threadTop = 0.079;
    const z = 0.008;
    const left = -half - 0.035;
    const right = half + 0.037;
    const points = {
        a0: [half - rod, ceiling * 0.66, z], a1: [half + rod, ceiling * 0.66, z],
        al: [half + 0.042, ceiling * 0.72, z],
        b0: [-half + rod, ceiling * 0.43, z], b1: [half - rod, ceiling * 0.43, z],
        c0: [left, 0, z], c1: [left, ceiling, z],
        c2: [-half, 0, z], c3: [0, ceiling, z],
        d0: [right, 0, z], d1: [right, threadTop, z],
        d2: [half + rod, 0, z], d3: [half + rod, threadTop, z],
    };
    if (interiorLength) {
        // User's measuring convention: inner end of one leg to inner crown midpoint.
        points.c0 = [-half + rod, 0, z];
        points.c1 = [0, ceiling, z];
        points.cl = [(-half + rod) * 0.28, ceiling * 0.72, z];
    }
    const anchors = Object.entries(points).map(([name, position]) => {
        const anchor = document.createElement('span');
        anchor.slot = `hotspot-dimension-${name}`;
        anchor.className = 'clamp-dimension-anchor';
        anchor.dataset.position = position.map(n => `${n}m`).join(' ');
        anchor.dataset.normal = '0 0 1';
        anchor.setAttribute('aria-hidden', 'true');
        viewer.append(anchor);
        return anchor;
    });
    const id = `clamp-dimensions-${++instance}`;
    const overlay = document.createElement('div');
    overlay.id = id;
    overlay.className = 'clamp-dimensions-overlay';
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    const svg = document.createElementNS(NS, 'svg');
    svg.classList.add('clamp-dimensions-lines');
    svg.innerHTML = `<defs><marker id="${id}-arrow" viewBox="0 0 6 6" refX="3" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M6 0 L0 3 L6 6" fill="none" stroke="currentColor" stroke-width="1.4"/></marker></defs>`;
    overlay.append(svg);
    viewer.parentElement.append(overlay);
    const definitions = [
        ['a', 'A', 'Diámetro', 'Diámetro de la varilla, medido de lado a lado de su sección.'],
        ['b', 'B', 'Ancho interior', 'Distancia entre las caras internas de las patas; no entre sus centros.'],
        ['c', 'C', 'Largo útil', interiorLength
            ? 'Desde el extremo interior de una pata hasta el punto medio de la cara interior del arco.'
            : 'Desde la cara interior del arco hasta el extremo de las patas.'],
        ['d', 'D', 'Rosca', 'Tramo roscado desde el extremo de la pata hacia arriba.'],
    ];
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'clamp-view-button clamp-dimensions-toggle';
    button.textContent = 'Ver medidas';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-controls', `${id} ${id}-legend`);
    controls.append(button);
    const legend = document.createElement('div');
    legend.id = `${id}-legend`;
    legend.className = 'clamp-dimensions-legend';
    legend.hidden = true;
    const note = document.createElement('p');
    note.className = 'clamp-dimensions-note';
    note.textContent = 'Guía de medición del modelo ilustrativo. Las cotas indican dónde medir; no representan las medidas de un pedido.';
    const list = document.createElement('dl');
    legend.append(list, note);
    footer.append(legend);
    const dimensions = definitions.map(([name, letter, title, description]) => {
        const line = document.createElementNS(NS, 'line');
        line.dataset.measure = name;
        line.setAttribute('marker-start', `url(#${id}-arrow)`);
        line.setAttribute('marker-end', `url(#${id}-arrow)`);
        svg.append(line);
        const label = document.createElement('div');
        label.className = `clamp-dimension-label dimension-${name}`;
        label.innerHTML = `<b>${letter}</b><span>${title}</span>`;
        overlay.append(label);
        const term = document.createElement('dt');
        term.textContent = `${letter} · ${title}`;
        const detail = document.createElement('dd');
        detail.textContent = description;
        const entry = document.createElement('div');
        entry.append(term, detail);
        list.append(entry);
        return { name, line, label };
    });
    const witnessPairs = [['d0', 'd2'], ['d1', 'd3'], ['a1', 'al']];
    if (!interiorLength) witnessPairs.push(['c0', 'c2'], ['c1', 'c3']);
    const witnesses = witnessPairs.map(([from, to]) => {
        const line = document.createElementNS(NS, 'line');
        line.classList.add('dimension-witness');
        svg.append(line);
        return { from, to, line };
    });
    let enabled = false;
    let frame = 0;
    function coordinates(name) {
        return viewer.queryHotspot(`hotspot-dimension-${name}`)?.canvasPosition;
    }
    function setLine(line, from, to) {
        if (!from || !to || ![from.x, from.y, to.x, to.y].every(Number.isFinite)) {
            line.style.display = 'none';
            return false;
        }
        line.style.display = '';
        line.setAttribute('x1', from.x);
        line.setAttribute('y1', from.y);
        line.setAttribute('x2', to.x);
        line.setAttribute('y2', to.y);
        return true;
    }
    function draw() {
        frame = 0;
        if (!enabled || viewer.clientWidth === 0) return;
        svg.setAttribute('viewBox', `0 0 ${viewer.clientWidth} ${viewer.clientHeight}`);
        for (const { name, line, label } of dimensions) {
            const start = coordinates(`${name}0`);
            const end = coordinates(`${name}1`);
            const valid = setLine(line, start, end);
            label.hidden = !valid;
            if (!valid) continue;
            const point = name === 'a' ? coordinates('al')
                : name === 'c' && interiorLength ? coordinates('cl')
                : { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
            if (!point) continue;
            let x = point.x;
            let y = point.y;
            if (name === 'b') y -= 19;
            if (name === 'c') x -= 24;
            if (name === 'd') x += 24;
            const padX = label.offsetWidth / 2 + 6;
            const padY = label.offsetHeight / 2 + 6;
            x = Math.max(padX, Math.min(viewer.clientWidth - padX, x));
            y = Math.max(padY, Math.min(viewer.clientHeight - padY, y));
            label.style.left = `${x}px`;
            label.style.top = `${y}px`;
        }
        for (const { from, to, line } of witnesses) setLine(line, coordinates(from), coordinates(to));
    }
    function schedule() {
        if (enabled && !frame) frame = requestAnimationFrame(draw);
    }
    button.addEventListener('click', () => {
        enabled = !enabled;
        overlay.hidden = !enabled;
        legend.hidden = !enabled;
        button.textContent = enabled ? 'Ocultar medidas' : 'Ver medidas';
        button.setAttribute('aria-pressed', String(enabled));
        if (enabled) {
            // Start with a readable measuring view; free orbit remains enabled.
            viewer.cameraOrbit = '0deg 90deg 140%';
            schedule();
        }
    });
    viewer.addEventListener('camera-change', schedule);
    const resize = new ResizeObserver(schedule);
    resize.observe(viewer);
    if (initiallyVisible) button.click();
    return () => {
        cancelAnimationFrame(frame);
        resize.disconnect();
        viewer.removeEventListener('camera-change', schedule);
        anchors.forEach(anchor => anchor.remove());
        overlay.remove();
        button.remove();
        legend.remove();
    };
}
