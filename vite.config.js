import { existsSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));
const buildRoot = fileURLToPath(new URL("./static/build/", import.meta.url));
const adminOut = fileURLToPath(new URL("./static/build/admin/", import.meta.url));
const entry = (path) => fileURLToPath(new URL(path, import.meta.url));

// Two independent builds share this file:
//   `vite build`               -> public site, static/build/*          (htmx 2, stable names)
//   `vite build --mode admin`  -> staff console, static/build/admin/*  (htmx 4, hashed names + manifest)
// The public build must never wipe the admin output, so it cleans its own files only.
const cleanPublicOutput = () => ({
  name: "clean-public-build",
  buildStart() {
    if (!existsSync(buildRoot)) return;
    for (const name of readdirSync(buildRoot)) {
      if (name !== "admin") rmSync(join(buildRoot, name), { recursive: true, force: true });
    }
  },
});

// Bootstrap upgrade seam (5.3.8 -> 6.0.0)
// ---------------------------------------------------------------------------
// Bootstrap is imported from exactly two places and nowhere else:
//   * frontend/src/admin/styles/_bootstrap.scss      (SCSS partial list)
//   * frontend/src/admin/js/vendor-bootstrap.js       (JS plugin list)
// To upgrade: bump "bootstrap" in package.json, adjust those two files, rebuild.
// AdminLTE 4 (admin-lte/src/scss) is written against Bootstrap 5.3 and imports
// `bootstrap/scss/*` itself, so it resolves through the same node_modules entry below;
// when Bootstrap 6 ships, check AdminLTE's matching release or drop its SCSS partials
// from the re-skin first. No template outside templates/console/components/ may
// depend on Bootstrap class names, which keeps the blast radius to those components.
const adminSass = {
  // Resolves `@import "bootstrap/scss/..."` and `@import "admin-lte/src/scss/..."`.
  loadPaths: [fileURLToPath(new URL("./node_modules/", import.meta.url))],
  // Third-party Bootstrap 5 / AdminLTE sources use the legacy @import / global functions.
  quietDeps: true,
  silenceDeprecations: ["import", "global-builtin", "color-functions", "if-function"],
};

const publicConfig = {
  root: projectRoot,
  // Relative URLs keep font/asset references valid under any STATIC_URL.
  base: "./",
  publicDir: false,
  plugins: [cleanPublicOutput()],
  resolve: {
    // The public bundle is written against htmx 2 events/attributes (htmx:afterSwap, htmx:confirm, ...);
    // htmx 4 renamed them, so `htmx.org` maps to the pinned 2.x alias here. The console uses htmx 4.
    alias: [{ find: /^htmx\.org$/, replacement: "htmx2" }],
  },
  build: {
    outDir: buildRoot,
    emptyOutDir: false,
    cssCodeSplit: true,
    rollupOptions: {
      input: {
        tokens: entry("./frontend/src/styles/tokens.scss"),
        site: entry("./frontend/src/site.js"),
        "theme-init": entry("./frontend/src/theme-init.js"),
        seo: entry("./frontend/src/styles/seo.css"),
        "seo-sitemap": entry("./frontend/src/styles/seo-sitemap.css"),
        editor: entry("./frontend/src/editor.js"),
        "editor-core": entry("./frontend/src/editor-core.js"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
};

const adminConfig = {
  root: projectRoot,
  base: "./",
  publicDir: false,
  css: { preprocessorOptions: { scss: adminSass } },
  build: {
    outDir: adminOut,
    emptyOutDir: true,
    // Written to static/build/admin/.vite/manifest.json; read by {% console_assets %}.
    manifest: true,
    cssCodeSplit: true,
    rollupOptions: {
      input: {
        "console-main": entry("./frontend/src/admin/main.js"),
        "console-styles": entry("./frontend/src/admin/admin.scss"),
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/chunks/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
};

export default defineConfig(({ mode }) => (mode === "admin" ? adminConfig : publicConfig));
