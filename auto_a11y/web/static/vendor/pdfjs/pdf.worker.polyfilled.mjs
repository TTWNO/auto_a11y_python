/*
 * PDF.js worker entry point, with the Uint8Array hex/base64 polyfill applied.
 *
 * A worker has its own global scope, so polyfilling on the page does not
 * reach it. `toHex()` — which PDF.js uses to build document fingerprints —
 * runs worker-side, so without this the document still fails to load on any
 * engine older than Chrome 140 even with the page polyfilled.
 *
 * Import order matters: the polyfill must be installed before the worker
 * body runs. Static imports are evaluated in order, so this is sufficient.
 *
 * `GlobalWorkerOptions.workerSrc` points here rather than at
 * pdf.worker.min.mjs; that file stays untouched so upgrading PDF.js is a
 * straight file swap.
 */
import "./uint8array-polyfill.mjs";
import "./pdf.worker.min.mjs";
