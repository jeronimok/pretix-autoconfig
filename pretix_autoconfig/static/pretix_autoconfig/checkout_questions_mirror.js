// Per-order checkout UX: mirror position-1 answers into all hidden
// mirror positions on every input/change. Pretix stores answers per
// position server-side, so every cart line ends up with the same answer
// set — buyer sees one form, regardless of cart size.
(function () {
    var src = document.querySelector('[data-autoconfig-position-source]');
    var mirrors = document.querySelectorAll('[data-autoconfig-position-mirror]');
    if (!src || !mirrors.length) return;

    // Strip required/HTML5 validation from mirror fields: they're
    // display:none so browsers refuse to submit forms with required
    // fields they can't focus. Server-side Pretix still validates
    // every position.
    mirrors.forEach(function (m) {
        m.querySelectorAll('[required]').forEach(function (el) {
            el.removeAttribute('required');
        });
    });

    function suffix(name) {
        var dash = name.indexOf('-');
        return dash >= 0 ? name.slice(dash + 1) : name;
    }

    function copy(srcEl, dstEl) {
        if (!srcEl || !dstEl) return;
        if (srcEl.type === 'checkbox' || srcEl.type === 'radio') {
            dstEl.checked = srcEl.checked;
        } else if (srcEl.type !== 'file') {
            dstEl.value = srcEl.value;
        }
    }

    function syncAll() {
        src.querySelectorAll('input, select, textarea').forEach(function (srcEl) {
            if (!srcEl.name) return;
            var suf = suffix(srcEl.name);
            mirrors.forEach(function (m) {
                var sel;
                if (srcEl.type === 'radio') {
                    sel = m.querySelector(
                        'input[type="radio"][name$="-' + CSS.escape(suf) + '"]' +
                        '[value="' + CSS.escape(srcEl.value) + '"]'
                    );
                } else {
                    sel = m.querySelector('[name$="-' + CSS.escape(suf) + '"]');
                }
                copy(srcEl, sel);
            });
        });
    }

    src.addEventListener('input', syncAll);
    src.addEventListener('change', syncAll);
    syncAll();
})();
