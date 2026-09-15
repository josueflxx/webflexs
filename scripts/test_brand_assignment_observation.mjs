import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

const source = readFileSync(new URL("../core/static/core/js/brand_product_workspace.js", import.meta.url), "utf8");
const start = source.indexOf("    async function submitAssignment(event) {");
const end = source.indexOf("    function renderSyncPreview(", start);
assert.ok(start >= 0 && end > start, "Assignment handler must exist");
const handler = source.slice(start, end);

for (const [input, expected] of [["", ""], ["   ", ""], [" Revisión manual ", "Revisión manual"]]) {
    test(`assignment submits observation ${JSON.stringify(input)}`, async () => {
        const requests = [];
        let prevented = false;
        let reloaded = false;
        const context = vm.createContext({
            document: {
                getElementById(id) {
                    if (id === "brandAssignObservation") return { value: input };
                    if (id === "brandAssignMode") return { value: "add" };
                    return {};
                },
            },
            config: { bulkAddUrl: "/test/assign/" },
            selectionPayload: () => ({ product_ids: [1, 2] }),
            selectionKey: "test-selection",
            sessionStorage: { removeItem() {} },
            setButtonBusy() {},
            requestJson: async (url, options) => {
                requests.push({ url, ...JSON.parse(options.body) });
                return { created_count: 2, existing_count: 0, batch_id: 3 };
            },
            queueFlashAndReload() { reloaded = true; },
            showToast(message) { assert.fail(message); },
        });
        vm.runInContext(handler, context);
        await context.submitAssignment({ preventDefault() { prevented = true; } });
        assert.equal(prevented, true);
        assert.deepEqual(requests, [{ url: "/test/assign/", product_ids: [1, 2], observation: expected, mode: "add" }]);
        assert.equal(reloaded, true);
    });
}
