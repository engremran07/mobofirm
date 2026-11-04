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
        admin: fileURLToPath(new URL("./frontend/src/admin.js", import.meta.url)),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
