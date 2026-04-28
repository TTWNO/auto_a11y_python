/**
 * pdf_viewer.js — iframe page-jump handler for the PDF detail view.
 *
 * Usage: include this script on a page that contains a region marked
 *   `data-pdf-viewer-host` with a child `iframe[data-pdf-iframe]` and
 *   one or more `<button data-pdf-page="N">` elements. Clicking such a
 *   button updates the iframe's `src` fragment to `#page=N&zoom=page-fit`
 *   without reloading the document — Chromium's PDF viewer interprets
 *   the fragment in place. Keyboard focus stays on the activating button
 *   so screen-reader users keep their place.
 *
 * No Jinja2: this file is served as static JS. Translatable strings, if
 * needed later, must come from a `window.i18n` object built in the
 * template, never from inline literals.
 */
(function () {
    "use strict";

    document.addEventListener("click", function (ev) {
        var target = ev.target;
        if (!(target instanceof Element)) {
            return;
        }
        var button = target.closest("[data-pdf-page]");
        if (!button) {
            return;
        }
        var host = button.closest("[data-pdf-viewer-host]");
        if (!host) {
            return;
        }
        var iframe = host.querySelector("iframe[data-pdf-iframe]");
        if (!iframe) {
            return;
        }
        var raw = button.getAttribute("data-pdf-page");
        var page = parseInt(raw || "", 10);
        if (!Number.isFinite(page) || page < 1) {
            return;
        }
        // Preserve the unfragmented base URL so subsequent clicks don't
        // chain fragments onto each other.
        var baseSrc = iframe.getAttribute("data-pdf-base-src");
        if (!baseSrc) {
            baseSrc = (iframe.src || "").split("#")[0];
            iframe.setAttribute("data-pdf-base-src", baseSrc);
        }
        iframe.src = baseSrc + "#page=" + page + "&zoom=page-fit";
        if (typeof button.focus === "function") {
            button.focus();
        }
    });
})();
