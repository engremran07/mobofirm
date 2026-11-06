import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: projectRoot,
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
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
