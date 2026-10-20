// After Sync now the worker runs within seconds. While it does, the page swaps in its own
// newer version every few seconds, so the PM sees the result without reloading.
import { showPage } from "./swap-page.js";

const EVERY_MS = 3000;

async function watch() {
  if (!document.querySelector("[data-sync-waiting]")) return;
  await showPage(location.href, { push: false });
  setTimeout(watch, EVERY_MS);
}

setTimeout(watch, EVERY_MS);
