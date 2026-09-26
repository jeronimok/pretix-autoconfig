// Disable the Pay button on the checkout confirm step until every
// required checkbox (T&C and any organizer-added confirm_texts) is
// ticked. Pretix sets `novalidate` on the form and AJAX-submits via
// `data-asynctask`, which makes intercepting the submit race
// unreliable; gating the button at the click source is robust and
// gives a clearer visual cue. Pretix still validates server-side, so
// this is belt-and-suspenders.
(function () {
    var form = document.querySelector("[data-autoconfig-confirm-form]");
    if (!form) return;
    var btn = form.querySelector('button[type="submit"], input[type="submit"]');
    if (!btn) return;

    function requiredCheckboxes() {
        return Array.prototype.slice.call(
            form.querySelectorAll('input[type="checkbox"][required]')
        );
    }
    function allTicked() {
        return requiredCheckboxes().every(function (cb) {
            return cb.checked;
        });
    }
    function sync() {
        var ok = allTicked();
        btn.disabled = !ok;
        btn.classList.toggle("is-disabled", !ok);
        btn.setAttribute("aria-disabled", String(!ok));
    }
    requiredCheckboxes().forEach(function (cb) {
        cb.addEventListener("change", sync);
    });
    sync();
})();
