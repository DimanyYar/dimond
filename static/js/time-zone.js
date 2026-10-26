// Fills an empty time zone list with the browser's own zone, so most people only confirm it.
// A zone the person already saved is never replaced.
const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

for (const select of document.querySelectorAll("[data-browser-time-zone]")) {
  const option = [...select.options].find((o) => o.value === browserZone);
  if (select.value === "" && option) {
    select.value = browserZone;
    const note = document.getElementById(select.dataset.browserTimeZone);
    if (note) note.hidden = false;
  }
}
