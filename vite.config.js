import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: projectRoot,
  // Relative URLs keep font/asset references valid under any STATIC_URL.
  base: "./",
  publicDir: false,
  build: {
    outDir: fileURLToPath(new URL("./static/build/", import.meta.url)),
    emptyOutDir: true,
    cssCodeSplit: true,
    rollupOptions: {
      input: {
        tokens: fileURLToPath(
          new URL("./frontend/src/styles/tokens.css", import.meta.url),
        ),
        site: fileURLToPath(new URL("./frontend/src/site.js", import.meta.url)),
        "theme-init": fileURLToPath(
          new URL("./frontend/src/theme-init.js", import.meta.url),
        ),
        seo: fileURLToPath(
          new URL("./frontend/src/styles/seo.css", import.meta.url),
        ),
        "seo-sitemap": fileURLToPath(
          new URL("./frontend/src/styles/seo-sitemap.css", import.meta.url),
        ),
        "admin-theme": fileURLToPath(
          new URL("./frontend/src/admin-theme.js", import.meta.url),
        ),
        "admin-theme-switch": fileURLToPath(
          new URL("./frontend/src/styles/admin-theme.css", import.meta.url),
        ),
        editor: fileURLToPath(
          new URL("./frontend/src/editor.js", import.meta.url),
        ),
        "editor-core": fileURLToPath(
          new URL("./frontend/src/editor-core.js", import.meta.url),
        ),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
