// Tells dimond the browser's time zone, so times show in it until the person saves their
// own (ADR 0023). Only the zone's name, e.g. Europe/Moscow, is sent; the server checks it.
// Zone names hold only letters, digits, "/", "_", "-" and "+", which a cookie takes as they are.
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
if (zone) {
  document.cookie = `browser_time_zone=${zone}; path=/; max-age=31536000; samesite=lax`;
}
