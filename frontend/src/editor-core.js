import { mountEditors } from "./modules/editor.js";

// The loader (`editor.js`) imports this entry by URL; Vite strips entry exports, so the
// mount function is handed over through a document event instead.
document.dispatchEvent(new CustomEvent("mf:editor-core", { detail: { mountEditors } }));
