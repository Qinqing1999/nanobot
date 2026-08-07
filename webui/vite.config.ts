import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync, readdirSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";

const GZIP_MIN_BYTES = 4 * 1024;
const GZIP_ASSET_PATTERN = /\.(?:css|html|js|json|mjs|svg)$/i;
const LAZY_FEATURE_CHUNK_PREFIXES = ["markdown-vendor-", "syntax-highlight-", "katex-"];

export function entryLazyFeatureImports(imports: string[]): string[] {
  return imports.filter((fileName) =>
    LAZY_FEATURE_CHUNK_PREFIXES.some((prefix) => path.basename(fileName).startsWith(prefix)),
  );
}

function guardWebuiEntryChunk(): Plugin {
  return {
    name: "nanobot-guard-webui-entry-chunk",
    apply: "build",
    generateBundle(_options, bundle) {
      for (const output of Object.values(bundle)) {
        if (output.type !== "chunk" || !output.isEntry) continue;
        const unexpected = entryLazyFeatureImports(output.imports);
        if (unexpected.length > 0) {
          throw new Error(
            `WebUI entry chunk statically imports lazy features: ${unexpected.join(", ")}`,
          );
        }
      }
    },
  };
}

export function writeCompressedWebuiAssets(outputDir: string, fileNames: string[]): void {
  for (const fileName of fileNames) {
    if (!GZIP_ASSET_PATTERN.test(fileName)) continue;
    const outputPath = path.resolve(outputDir, fileName);
    const bytes = readFileSync(outputPath);
    if (bytes.byteLength < GZIP_MIN_BYTES) continue;
    const compressed = gzipSync(bytes, { level: 9 });
    if (compressed.byteLength >= bytes.byteLength) continue;
    writeFileSync(`${outputPath}.gz`, compressed);
  }
}

export function gzipWebuiAssets(): Plugin {
  return {
    name: "nanobot-gzip-webui-assets",
    apply: "build",
    writeBundle(options, bundle) {
      const outputDir = options.dir ?? (options.file ? path.dirname(options.file) : undefined);
      if (!outputDir) {
        throw new Error("WebUI gzip build requires a Rollup output directory");
      }
      writeCompressedWebuiAssets(outputDir, Object.keys(bundle));
    },
  };
}

/**
 * Remove every entry from the build output directory before Rollup writes new
 * assets.  This replaces Vite's built-in `emptyOutDir`, which calls
 * `fs.rmSync(entry, { recursive: true, force: true })` on each entry and can
 * throw `ENOTDIR` when a hosting environment places a file or symlink (e.g.
 * `.user.ini`) inside the directory — Node's internal `rimraf` may try to
 * `readdirSync` it as though it were a directory.
 *
 * By reading `Dirent` metadata first we can unlink files and symlinks directly
 * and only recurse into real directories, sidestepping the buggy code path.
 */
function cleanWebuiOutDir(): Plugin {
  return {
    name: "nanobot-clean-webui-out-dir",
    apply: "build",
    buildStart() {
      const outDir = path.resolve(__dirname, "../nanobot/web/dist");
      if (!existsSync(outDir)) return;
      for (const entry of readdirSync(outDir, { withFileTypes: true })) {
        const fullPath = path.join(outDir, entry.name);
        // Dirent methods do NOT follow symlinks, so isSymbolicLink() correctly
        // identifies symlinks without dereferencing them.
        if (entry.isDirectory() && !entry.isSymbolicLink()) {
          rmSync(fullPath, { recursive: true, force: true });
        } else {
          try {
            unlinkSync(fullPath);
          } catch {
            rmSync(fullPath, { recursive: true, force: true });
          }
        }
      }
    },
  };
}

export function webuiManualChunk(id: string): string | undefined {
  if (
    id.includes("node_modules/react/")
    || id.includes("node_modules/react-dom/")
    || id.includes("node_modules/scheduler/")
    || id.includes("node_modules/clsx/")
    || id.includes("vite/preload-helper")
  ) {
    return "react-vendor";
  }
  if (id.includes("node_modules/refractor/lang/")) {
    return;
  }
  // Streamdown lazy-loads diagrams and highlighted code. Keep those modules
  // outside the core markdown chunk so ordinary replies do not download them.
  if (
    id.includes("node_modules/streamdown/dist/mermaid-")
    || id.includes("node_modules/streamdown/dist/highlighted-body-")
  ) {
    return;
  }
  // Refractor reaches this HAST helper through hastscript. Keeping it with
  // Refractor prevents syntax-highlight <-> markdown-vendor circular chunks.
  if (
    id.includes("node_modules/react-syntax-highlighter")
    || id.includes("node_modules/refractor/core")
    || id.includes("node_modules/hast-util-parse-selector")
  ) {
    return "syntax-highlight";
  }
  if (
    id.includes("node_modules/streamdown")
    || id.includes("node_modules/remend")
    || id.includes("node_modules/remark-")
    || id.includes("node_modules/rehype-")
    || id.includes("node_modules/unified")
    || id.includes("node_modules/mdast-")
    || id.includes("node_modules/hast-")
    || id.includes("node_modules/micromark")
    || id.includes("node_modules/unist-")
  ) {
    return "markdown-vendor";
  }
  if (id.includes("node_modules/katex")) {
    return "katex";
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.NANOBOT_API_URL ?? "http://127.0.0.1:8765";
  const hmrPath = "/__nanobot_vite_hmr";

  return {
    plugins: [react(), guardWebuiEntryChunk(), cleanWebuiOutDir(), gzipWebuiAssets()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
      // Channel-owned UI lives beside the Python package, outside webui/.
      // Resolve its shared frontend dependencies from this app's root.
      dedupe: ["react", "react-dom", "lucide-react", "react-i18next", "qrcode"],
    },
    optimizeDeps: {
      // Radix Dialog can rewrite its optimized chunk while a dev tab is open.
      // Syntax highlighting must remain pre-bundled because Refractor's core
      // still uses CommonJS internally.
      exclude: ["@radix-ui/react-dialog"],
    },
    build: {
      outDir: path.resolve(__dirname, "../nanobot/web/dist"),
      // Custom `cleanWebuiOutDir` plugin handles output-dir cleaning instead of
      // Vite's built-in `emptyOutDir` to avoid ENOTDIR errors on files/symlinks
      // placed by hosting environments (e.g. `.user.ini`).
      emptyOutDir: false,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: webuiManualChunk,
        },
      },
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      fs: {
        allow: [path.resolve(__dirname, "..")],
      },
      // Keep Vite's HMR socket on a dedicated path. Nanobot's app WebSocket is
      // opened directly from the browser to the gateway, so the dev server
      // should never proxy WebSocket upgrades.
      hmr: {
        host: "127.0.0.1",
        path: hmrPath,
      },
      proxy: {
        "/webui": { target, changeOrigin: true },
        "/api": { target, changeOrigin: true },
        "/auth": { target, changeOrigin: true },
      },
    },
    test: {
      environment: "happy-dom",
      globals: true,
      setupFiles: ["./src/tests/setup.ts"],
    },
  };
});
