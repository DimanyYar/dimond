// The phone menu: the ☰ in the top bar slides the sidebar in (mockups PM · phone, Client ·
// phone). Esc, the ×, or a tap outside closes it, and focus goes back to the ☰.
const app = document.querySelector("[data-menu]");
const button = app?.querySelector("[data-menu-open]");
const scrim = app?.querySelector(".menu-scrim");

function open() {
  app.setAttribute("data-menu-open", "");
  button.setAttribute("aria-expanded", "true");
  scrim.hidden = false;
  document.body.style.overflow = "hidden";
  app.querySelector(".sidebar .nav a")?.focus();
}

function close() {
  if (!app.hasAttribute("data-menu-open")) return;
  app.removeAttribute("data-menu-open");
  button.setAttribute("aria-expanded", "false");
  scrim.hidden = true;
  document.body.style.overflow = "";
  button.focus();
}

if (app && button && scrim) {
  button.addEventListener("click", open);
  app.querySelectorAll("[data-menu-close]").forEach((el) => el.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
}
