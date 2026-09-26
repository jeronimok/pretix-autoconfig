(function () {
    var form = document.querySelector('form[data-autoconfig-voucher-form]');
    if (!form) return;

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var btn = form.querySelector('[type=submit]');
        if (btn) btn.disabled = true;
        var fd = new FormData(form);
        var csrf = fd.get('csrfmiddlewaretoken') || '';
        fetch(form.action + '?ajax=1', {
            method: 'POST',
            body: fd,
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf }
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ready) { window.location.reload(); return; }
                if (data.check_url) { poll(data.check_url); }
                else if (btn) { btn.disabled = false; }
            })
            .catch(function () { if (btn) btn.disabled = false; });
    });

    function poll(url) {
        fetch(url, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ready) { window.location.reload(); }
                else { setTimeout(function () { poll(url); }, 300); }
            })
            .catch(function () { setTimeout(function () { poll(url); }, 500); });
    }
})();
