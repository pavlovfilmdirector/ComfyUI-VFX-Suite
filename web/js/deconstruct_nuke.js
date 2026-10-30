import { app } from "../../../scripts/app.js";

/**
 * ComfyUI-VFX-Suite: VFX Deconstruct & Nuke Exporter LiteGraph Node Extension
 * Dynamically maps project deconstruction layers into named output sockets
 * and provides quick one-click Foundry Nuke export.
 */

app.registerExtension({
    name: "VFXSuite.DeconstructNuke",
    async nodeCreated(node) {
        if (node.comfyClass !== "VFXDeconstructNuke") return;

        node.setSize([320, 260]);
        node.color = "#1e293b";
        node.bgcolor = "#0f172a";

        // Sync dynamic outputs with project's deconstruction items
        async function syncNodeOutputs() {
            try {
                const pidWidget = node.widgets?.find(w => w.name === "project_id");
                const pid = (pidWidget ? pidWidget.value : "New_Movie_Shot") || "sandbox_playground";
                const res = await fetch(`/vfx/projects/${encodeURIComponent(pid)}/deconstruction`);
                if (!res.ok) return;
                const data = await res.json();
                const items = (data.items || []).filter(it => it.enabled !== false);

                // Update output socket labels dynamically to match user aliases
                if (node.outputs && Array.isArray(node.outputs)) {
                    items.forEach((it, idx) => {
                        if (idx < node.outputs.length - 3) {
                            const alias = (it.alias || it.name || `out_${idx + 1}`).toLowerCase();
                            node.outputs[idx].name = alias;
                            node.outputs[idx].label = `🧩 ${alias} (${it.data_type || "ANY"})`;
                        }
                    });
                }
                node.setDirtyCanvas(true, true);
            } catch (err) {
                console.warn("[VFXDeconstructNuke] syncNodeOutputs warning:", err);
            }
        }

        // Add "Open Deconstruction Hub" button
        node.addWidget("button", "🧩 Open Deconstruction Hub", null, () => {
            const studioIframe = document.querySelector("iframe[src*='standalone_studio']");
            if (studioIframe && studioIframe.contentWindow && studioIframe.contentWindow.switchTab) {
                studioIframe.contentWindow.switchTab("deconstruct");
            } else if (typeof window.switchTab === "function") {
                window.switchTab("deconstruct");
            } else {
                window.open("/extensions/ComfyUI-VFX-Suite/standalone_studio.html", "_blank");
            }
        });

        // Add "Export to Nuke" button directly on the node
        node.addWidget("button", "🚀 Export to Nuke (.nk)", null, async () => {
            const pidWidget = node.widgets?.find(w => w.name === "project_id");
            const pid = (pidWidget ? pidWidget.value : "New_Movie_Shot") || "sandbox_playground";
            try {
                const res = await fetch(`/vfx/projects/${encodeURIComponent(pid)}/deconstruction/export_nuke`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                const d = await res.json();
                if (res.ok) {
                    alert(`✅ Nuke Export Successful!\n\nScript: ${d.nuke_script}\n\nCamera chan: ${d.chan_file || "None"}\nItems exported: ${d.items_exported}`);
                } else {
                    alert(`❌ Export failed: ${d.error || "Unknown error"}`);
                }
            } catch (e) {
                alert(`❌ Network error: ${e.message}`);
            }
        });

        const origOnConfigure = node.onConfigure;
        node.onConfigure = function() {
            if (origOnConfigure) origOnConfigure.apply(this, arguments);
            setTimeout(syncNodeOutputs, 200);
        };

        setTimeout(syncNodeOutputs, 300);
    }
});
