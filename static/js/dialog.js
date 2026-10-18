// A window the server draws open (New client) becomes a real modal: the page behind is out
// of reach for Tab and screen readers, and Esc leaves like the × does. Without this script
// the window still works, just not modal.
for (const dialog of document.querySelectorAll("dialog[data-modal][open]")) {
  dialog.close();
  dialog.showModal();
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    window.location.assign(dialog.dataset.closeUrl);
  });
}
