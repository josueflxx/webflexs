const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('core/static/core/js/catalog_excel_download.js', 'utf8');

async function scenario(responses) {
    const feedback = {textContent: ''};
    const label = {textContent: 'Descargar Excel'};
    const attrs = new Map();
    const classes = new Set();
    const element = {
        dataset: {}, href: 'https://example.test/catalogo/descargar-excel/',
        querySelector: () => label,
        parentNode: {querySelector: () => feedback},
        classList: {add: name => classes.add(name), remove: name => classes.delete(name)},
        setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k),
    };
    const navigations = [];
    let requests = 0;
    const context = {
        URL, AbortController, Date, Promise,
        setTimeout: (callback, delay) => { if (delay === 3000) queueMicrotask(callback); return 1; },
        clearTimeout: () => {},
        document: {addEventListener: () => {}},
        window: {location: {href: 'https://example.test/catalogo/', assign: url => navigations.push(url)}},
        fetch: async (url, options) => {
            requests++;
            assert.equal(url.searchParams.get('status'), '1');
            assert.equal(options.cache, 'no-store');
            assert.equal(options.credentials, 'same-origin');
            const next = responses.shift();
            if (!next) throw new Error('Unexpected extra polling');
            return {ok: next.status !== 'failed', redirected: !!next.redirected,
                headers: {get: () => 'application/json'}, json: async () => next};
        },
    };
    vm.runInNewContext(code, context);
    const first = context.window.handleExcelDownload({preventDefault() {}}, element);
    await context.window.handleExcelDownload({preventDefault() {}}, element);
    await first;
    assert.equal(label.textContent, 'Descargar Excel');
    assert.equal(element.dataset.exportBusy, undefined);
    assert.equal(classes.size, 0);
    assert.equal(attrs.size, 0);
    return {feedback: feedback.textContent, navigations, requests};
}

(async () => {
    const queued = await scenario([{status: 'queued'}, {status: 'running'}, {status: 'ready'}]);
    assert.equal(queued.requests, 3);
    assert.equal(queued.navigations.length, 1);
    const ready = await scenario([{status: 'ready'}]);
    assert.equal(ready.requests, 1);
    const failed = await scenario([{status: 'failed', message: 'Volver a intentar'}]);
    assert.equal(failed.navigations.length, 0);
    assert.equal(failed.feedback, 'Volver a intentar');
    const login = await scenario([{redirected: true}]);
    assert.equal(login.navigations.length, 0);
    assert.match(login.feedback, /sesion/);
    console.log('4 download UI scenarios passed, including duplicate-click suppression.');
})().catch(error => { console.error(error); process.exitCode = 1; });
