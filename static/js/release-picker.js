// On a phone the release list hides behind the chosen release's button (30e; canvas 7.3 ·
// MPMReleaseView): a tap opens it, Esc closes it. Picking a release swaps the page (filters.js)
// and the new button comes closed. Delegated, so swapped-in buttons work too.
document.addEventListener("click", (event) => {
  const picker = event.target.closest("button[data-release-picker]");
  if (!picker) return;
  picker.setAttribute("aria-expanded", String(picker.getAttribute("aria-expanded") !== "true"));
});

document.addEventListener("keydown", (event) => {
  const picker = document.querySelector('button[data-release-picker][aria-expanded="true"]');
  if (event.key !== "Escape" || !picker) return;
  picker.setAttribute("aria-expanded", "false");
  picker.focus();
});
