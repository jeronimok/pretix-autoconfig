(function () {
    var cartActive = false;
    var changeGen = 0;
    var debounceTimer = null;
    // Last known server cart. Every quantity change is a diff against this, and every
    // change is followed by a re-read, so the page can never drift away from the cart the
    // buyer will actually check out with. See spec 2026-09-20-private-link-voucher-lock.
    var serverState = { quantities: {}, positions: [], total: '0.00', expires: null, link: null };
    // Set by the last failed cart call, so the refusal can be reworded for the buyer.
    var lastError = '';
    // Resolves when the in-flight cart sync is done — "Proceed with checkout" waits on it.
    var pending = Promise.resolve();
    var settled = true;

    function getFlow() {
        return document.querySelector('.autoconfig-flow');
    }

    function parseCurrencyAmounts(text) {
        if (!text) return [];
        var normalized = text.replace(/ /g, ' ').trim();
        var re = /([€$£])?\s*(-?\d+[.,]\d{2})/g;
        var out = [];
        var m;
        while ((m = re.exec(normalized)) !== null) {
            out.push({ symbol: m[1] || '€', value: parseFloat(m[2].replace(',', '.')) });
        }
        return out;
    }

    function roundTo2(x) {
        return Math.round((x + Number.EPSILON) * 100) / 100;
    }

    function computeOptimisticTotal() {
        var flow = getFlow();
        var feePercent = parseFloat(flow.getAttribute('data-autoconfig-fee-percent') || '0');
        var feeMin = parseFloat(flow.getAttribute('data-autoconfig-fee-min') || '0');
        var subtotal = 0;
        var fee = 0;
        var symbol = '€';
        var anyFee = feePercent > 0 || feeMin > 0;
        document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
            var qty = parseInt(input.value, 10) || 0;
            if (qty <= 0) return;
            var row = input.closest('article.product-row');
            if (!row) return;
            var priceEl = row.querySelector('.price');
            if (!priceEl) return;
            var insEl = priceEl.querySelector('ins');
            var amounts = parseCurrencyAmounts((insEl || priceEl).textContent || '');
            if (!amounts.length) return;
            symbol = amounts[0].symbol;
            var listedPrice = amounts[0].value;
            subtotal += listedPrice * qty;
            if (anyFee) {
                var ticketFee = roundTo2((listedPrice * feePercent) / 100);
                if (ticketFee < feeMin) ticketFee = feeMin;
                fee = roundTo2(fee + ticketFee * qty);
            }
        });
        return { total: subtotal + fee, symbol: symbol };
    }

    function getTotalQty() {
        var n = 0;
        document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
            n += parseInt(input.value, 10) || 0;
        });
        return n;
    }

    function getCSRF() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    function showCart(totalStr) {
        var el = document.getElementById('autoconfig-live-cart');
        if (!el) return;
        var totalEl = document.getElementById('autoconfig-live-cart-total');
        if (totalEl) totalEl.textContent = totalStr;
        el.style.display = '';
        el.removeAttribute('aria-hidden');
        cartActive = true;
        // The real deadline only lands once the AJAX add resolves. Until then, clear any
        // stale "Cart expired" text a prior hideCart() (or an already-expired reservation
        // at page load) left in the badge — otherwise it flashes for the AJAX round-trip.
        var timerEl = document.getElementById('cart-deadline-short');
        if (timerEl) timerEl.textContent = '';
    }

    function hideCart() {
        var el = document.getElementById('autoconfig-live-cart');
        if (!el) return;
        el.style.display = 'none';
        el.setAttribute('aria-hidden', 'true');
        var wasActive = cartActive;
        cartActive = false;
        // Push the deadline into the past: cart.js renders the expired state once and,
        // seeing a negative diff, does not reschedule itself — so this is also our "stop".
        // Only when there was something to stop: doing it on a fresh visit writes "Cart
        // expired" into a card the buyer has not even filled yet.
        if (wasActive) setCartDeadline(new Date(Date.now() - 1000));
    }

    // "Reserved for" only belongs next to a live MM:SS. Pretix's cart.js writes its own
    // "Cart expired" into the same slot, which read as "Reserved for Cart expired".
    function syncDeadlinePrefix() {
        var prefix = document.getElementById('cart-deadline-prefix');
        var slot = document.getElementById('cart-deadline-short');
        if (!prefix || !slot) return;
        prefix.hidden = !/^\d{1,2}:\d{2}$/.test((slot.textContent || '').trim());
    }

    function watchDeadlineSlot() {
        var slot = document.getElementById('cart-deadline-short');
        if (!slot || typeof MutationObserver === 'undefined') return;
        new MutationObserver(syncDeadlinePrefix).observe(slot, { childList: true, characterData: true, subtree: true });
        syncDeadlinePrefix();
    }

    function fetchCartState() {
        var flow = getFlow();
        var url = flow && flow.getAttribute('data-cart-state-url');
        if (!url) return Promise.resolve(null);
        return fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json(); })
            .catch(function () { return null; });
    }

    // Server truth wins: quantities, total and deadline all come from the cart the buyer
    // actually holds, so a refused change can no longer leave a wrong number on screen.
    function syncFromServer(gen) {
        return fetchCartState().then(function (data) {
            if (!data || (gen !== undefined && gen !== changeGen)) return;
            serverState = {
                quantities: data.quantities || {},
                positions: data.positions || [],
                total: data.total || '0.00',
                expires: data.expires || null,
                link: data.link || null,
            };
            var live = !!serverState.expires && serverState.expires * 1000 > Date.now();
            document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
                input.value = live ? (serverState.quantities[input.name] || 0) : 0;
            });
            if (!live || !serverState.positions.length) {
                serverState.positions = [];
                serverState.quantities = {};
                renderRows();
                // Drop the optimistic total too, or the hidden card keeps the amount of a
                // basket the server refused and flashes it the next time it opens.
                var totalEl = document.getElementById('autoconfig-live-cart-total');
                if (totalEl) totalEl.textContent = '—';
                hideCart();
                return;
            }
            renderRows();
            showCart(computeOptimisticTotal().symbol + parseFloat(serverState.total).toFixed(2));
            setCartDeadline(new Date(serverState.expires * 1000));
        });
    }

    // Each row's "N× in your cart" badge and, when a ticket in the cart costs something else
    // than the list price (a discount code applied to some of them), what they cost. Both
    // were server-rendered once and went stale as soon as the buyer changed a quantity.
    function renderRows() {
        document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
            var row = input.closest('article.product-row');
            if (!row) return;
            var lines = serverState.positions.filter(function (p) { return p.key === input.name; });
            renderBadge(row, lines.length);
            renderCartPrices(row, lines);
        });
    }

    function renderBadge(row, count) {
        var legend = row.querySelector('[id$="-legend"]');
        if (!legend) return;
        var badge = legend.querySelector('.textbubble-success');
        if (!count) {
            if (badge) badge.remove();
            return;
        }
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'textbubble-success';
            var icon = document.createElement('span');
            icon.className = 'fa fa-shopping-cart';
            icon.setAttribute('aria-hidden', 'true');
            badge.appendChild(icon);
            badge.appendChild(document.createTextNode(''));
            legend.appendChild(document.createTextNode(' '));
            legend.appendChild(badge);
        }
        badge.lastChild.textContent = ' ' + count + '× in your cart';
    }

    function renderCartPrices(row, lines) {
        var el = row.querySelector('.autoconfig-item-cart-prices');
        var differs = lines.some(function (p) {
            return p.listed_price !== undefined && parseFloat(p.price) !== parseFloat(p.listed_price);
        });
        if (!differs) {
            if (el) el.remove();
            return;
        }
        var anchor = row.querySelector('.autoconfig-item-inline-price');
        var symbol = (parseCurrencyAmounts(anchor ? anchor.textContent : '')[0] || { symbol: '€' }).symbol;
        var counts = {};
        var order = [];
        lines.forEach(function (p) {
            var price = parseFloat(p.price).toFixed(2);
            if (!(price in counts)) { counts[price] = 0; order.push(price); }
            counts[price] += 1;
        });
        if (!el) {
            el = document.createElement('p');
            el.className = 'autoconfig-item-cart-prices';
            if (anchor) anchor.insertAdjacentElement('afterend', el);
            else row.querySelector('[id$="-legend"]').insertAdjacentElement('afterend', el);
        }
        el.textContent = 'In your cart: ' + order.map(function (price) {
            return counts[price] + '×\u00a0' + symbol + price;  // keep "1× €18.00" on one line
        }).join(' · ');
    }

    // Drives Pretix's own #cart-deadline / #cart-deadline-short countdown (pretixpresale/js/ui/cart.js),
    // already loaded on every presale page. Ticks every 500ms, shows MM:SS, is translated, and
    // corrects for client clock skew — reimplementing that here would just be worse.
    function setCartDeadline(expiresAtDate, maxExtendDate) {
        if (!window.cart || typeof window.cart.set_deadline !== 'function') return;
        window.cart.set_deadline(expiresAtDate, maxExtendDate || new Date(Date.now()));
    }

    // Waits for the cart task to finish even when the buyer has clicked again meanwhile.
    // Walking away early let the next click compute its difference from a cart the server
    // was still changing, so two "+" clicks could add three or four tickets (or only one).
    function poll(url) {
        return new Promise(function (resolve, reject) {
            var attempts = 0;
            function check() {
                if (++attempts > 40) { reject(new Error('timeout')); return; }
                fetch(url, { headers: { Accept: 'application/json' } })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.ready) {
                            if (data.success === false) lastError = data.message || '';
                            resolve(data.success === false ? null : data);
                        } else {
                            setTimeout(check, 500);
                        }
                    })
                    .catch(function () { setTimeout(check, 1000); });
            }
            check();
        });
    }

    function postAndPoll(url, formData, gen) {
        return fetch(url, { method: 'POST', body: formData })
            .then(function (r) { return r.json(); })
            .then(function (data) { return poll(data.check_url || data.redirect); });
    }

    function buildFormData(items) {
        var fd = new FormData();
        fd.append('csrfmiddlewaretoken', getCSRF());
        var subevEl = document.querySelector('input[name="subevent"]');
        if (subevEl) fd.append('subevent', subevEl.value);
        var voucherEl = document.querySelector('input[name="_voucher_code"]');
        if (voucherEl) fd.append('_voucher_code', voucherEl.value);
        items.forEach(function (item) { fd.append(item.name, item.qty); });
        return fd;
    }

    // Adds ONLY the difference. Re-posting the whole basket after a clear is what used to
    // destroy a buyer's reservation whenever the new total did not fit the voucher: the
    // clear had already released everything and Pretix's re-add is partial, so tickets were
    // lost or silently swapped for a different type. A delta that does not fit is simply
    // refused and what the buyer already holds is untouched.
    function doCartAdd(gen, deltas) {
        var flow = getFlow();
        var url = flow.getAttribute('data-cart-add-url') + '?ajax=1';
        var items = Object.keys(deltas).map(function (name) { return { name: name, qty: deltas[name] }; });
        if (!items.length) return Promise.resolve(true);
        return postAndPoll(url, buildFormData(items), gen);
    }

    // Pretix removes one position per call, addressed by its id (CartRemove/remove_cart_position).
    function doCartRemove(gen, positionIds) {
        var flow = getFlow();
        var url = flow.getAttribute('data-cart-remove-url');
        if (!url || !positionIds.length) return Promise.resolve(true);
        return positionIds.reduce(function (chain, id) {
            return chain.then(function (ok) {
                if (ok === null) return ok;
                var fd = buildFormData([]);
                fd.append('id', id);
                return postAndPoll(url + '?ajax=1', fd, gen);
            });
        }, Promise.resolve(true));
    }

    function doCartClear(gen) {
        var flow = getFlow();
        var url = flow.getAttribute('data-cart-clear-url') + '?ajax=1';
        return postAndPoll(url, buildFormData([]), gen);
    }

    function showCartError(text) {
        var el = document.getElementById('autoconfig-cart-error');
        if (!el) return;
        el.textContent = text || '';
        el.hidden = !text;
    }

    function clearCartError() { showCartError(''); }

    function linkCap() {
        var flow = getFlow();
        var raw = flow && flow.getAttribute('data-cart-max-total');
        var cap = parseInt(raw || '0', 10);
        return cap > 0 ? cap : null;
    }

    function capMessage(cap) {
        return cap === 1
            ? 'This link covers 1 ticket.'
            : 'This link covers ' + cap + ' tickets.';
    }

    // Pretix's own wording for a voucher that is fully taken blames the buyer ("this
    // voucher is locked in a cart", "already used the maximum number of times") even when
    // it is their own cart holding it, or another invitee is simply mid-checkout.
    //
    // Worded from the link's real counts (`serverState.link`, re-read after the refusal):
    // blaming "someone else" when it was the buyer's own cart left them with no way forward.
    function humaneError(message, cap) {
        var m = (message || '').toLowerCase();
        if (m.indexOf('locked') !== -1 || m.indexOf('maximum number of times') !== -1 ||
            m.indexOf('more times') !== -1) {
            var link = serverState.link;
            // The plain shop page has no data-cart-max-total; the link's own limit still applies.
            cap = cap || (link && link.cap) || null;
            // Count the server cart, not the inputs: a sold-out row has no input there.
            var held = Math.max(getTotalQty(), serverState.positions.length);
            if (cap && held >= cap) return capMessage(cap);
            if (!link || link.held_by_others > 0) {
                return 'Someone else is using this link right now. Please try again in a few minutes.';
            }
            if (link.redeemed >= link.cap) return 'All tickets on this link have been bought.';
            return 'That change could not be applied. Please try again.';
        }
        if (m.indexOf('expired') !== -1) return 'This link has expired.';
        return message || 'That change could not be applied. Please try again.';
    }

    function desiredQuantities() {
        var out = {};
        document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
            out[input.name] = parseInt(input.value, 10) || 0;
        });
        return out;
    }

    // Position ids to drop so that `key` loses `count` tickets. Newest first: the buyer is
    // undoing their most recent addition.
    function positionIdsFor(key, count) {
        return serverState.positions
            .filter(function (p) { return p.key === key; })
            .slice(-count)
            .map(function (p) { return p.id; });
    }

    function handleChange() {
        var gen = ++changeGen;
        settled = false;
        var cap = linkCap();
        var totalQty = getTotalQty();

        // Refuse to even ask for more than the link covers. Pretix caps each product input
        // at the voucher's remaining usages but nothing caps their sum, so "+ one more of a
        // different ticket type" used to be a one-click way into a server-side rejection.
        if (cap !== null && totalQty > cap) {
            document.querySelectorAll('.autoconfig-ticket-rows .input-item-count').forEach(function (input) {
                input.value = serverState.quantities[input.name] || 0;
            });
            showCartError(capMessage(cap));
            settled = true;
            return;
        }
        clearCartError();

        totalQty = getTotalQty();

        // Optimistic total while the round trip is in flight; reconciled from the server below.
        if (totalQty > 0) {
            var opt = computeOptimisticTotal();
            showCart(opt.symbol + opt.total.toFixed(2));
        }

        // One change at a time: wait for the previous one to finish on the server, skip this
        // one if the buyer has changed something again meanwhile (the newest change covers
        // it), and diff against a fresh read of the cart, never a stale one.
        pending = pending
            .catch(function () {})
            .then(function () {
                if (gen !== changeGen) return;
                return fetchCartState().then(function (data) {
                    if (gen !== changeGen) return;
                    if (data) {
                        serverState.quantities = data.quantities || {};
                        serverState.positions = data.positions || [];
                    }
                    return applyDesired(gen, cap);
                });
            })
            .then(function () { if (gen === changeGen) settled = true; });
        return pending;
    }

    function applyDesired(gen, cap) {
        var desired = desiredQuantities();
        var totalQty = getTotalQty();
        lastError = '';

        if (totalQty === 0) {
            if (!serverState.positions.length) { hideCart(); return; }
            return doCartClear(gen).then(function () {
                if (gen !== changeGen) return;
                return syncFromServer(gen);
            });
        }

        var removals = [];
        var additions = {};
        Object.keys(desired).forEach(function (key) {
            var have = serverState.quantities[key] || 0;
            var want = desired[key];
            if (want < have) {
                removals = removals.concat(positionIdsFor(key, have - want));
            } else if (want > have) {
                additions[key] = want - have;
            }
        });

        return doCartRemove(gen, removals)
            .then(function () { return doCartAdd(gen, additions); })
            .then(function (result) {
                if (gen !== changeGen) return;
                var refused = result === null && lastError;
                return syncFromServer(gen).then(function () {
                    if (refused && gen === changeGen) showCartError(humaneError(lastError, cap));
                });
            });
    }

    function onQuantityInput() {
        if (debounceTimer) clearTimeout(debounceTimer);
        // Nulled the instant it fires. Left as a stale (already-fired) id, it stays
        // truthy forever after the first-ever change, which broke the checkout guard
        // below ("nothing in flight" never became true again) and forced a second,
        // concurrent handleChange() on every checkout click — racing whichever real
        // sync was still in flight. See spec 2026-09-20-private-link-voucher-lock.
        debounceTimer = setTimeout(function () {
            debounceTimer = null;
            handleChange();
        }, 300);
    }

    function init() {
        // By decision, the front-page card is countdown display only — no "renew reservation"
        // flow. Suppress upstream's under-45s expiry popup (#dialog-cart-extend is in the DOM
        // via fragment_modals.html, so it would otherwise show; #cart-extend-button is not
        // rendered here, so renewing isn't wired up anyway).
        if (window.cart) {
            window.cart.show_expiry_notification = function () {};
        }

        var flow = getFlow();
        if (!flow || !flow.hasAttribute('data-cart-add-url')) return;

        var inputs = document.querySelectorAll('.autoconfig-ticket-rows .input-item-count');
        if (!inputs.length) return;

        // Ensure live cart is hidden initially — CSSOM may not reflect the HTML style attribute
        var liveCartEl = document.getElementById('autoconfig-live-cart');
        if (liveCartEl) liveCartEl.style.display = 'none';

        watchDeadlineSlot();

        // Quantities always start at 0 and are then corrected from the server's own cart.
        // Nothing is ever added to the cart by loading this page: a visit — by the buyer,
        // by the organizer checking their private link, or by a link-preview crawler — must
        // not reserve tickets. See spec 2026-09-20-private-link-voucher-lock.
        inputs.forEach(function (input) { input.value = 0; });

        inputs.forEach(function (input) {
            input.addEventListener('change', onQuantityInput);
            input.addEventListener('input', onQuantityInput);
        });

        // Adopt the server cart, but only while the reservation is still LIVE — an
        // expired-but-not-yet-swept cart must be treated like a fresh visit, or the
        // countdown never starts. See spec 2026-09-08-private-link-countdown-stuck-expired.
        // A change the buyer makes while this is in flight wins: syncFromServer bails on a
        // stale generation.
        syncFromServer(0);

        // Browser back/forward can restore this exact page from the bfcache: the DOM is
        // repainted from a frozen snapshot with NO new navigation, so DOMContentLoaded
        // (and this whole init()) never runs again — whatever quantities were on screen
        // when the buyer left for checkout just sit there, un-reconciled with whatever the
        // server cart actually is by the time they come back. A "+" from that stale
        // display then computes its delta against the wrong base and adds on top of what
        // the server already holds — the self-inflicted voucher-locked-in-a-cart failure
        // this spec exists to prevent. `event.persisted` is exactly bfcache's flag for
        // this; re-run the same startup sync every time it fires.
        window.addEventListener('pageshow', function (e) {
            // Pass the CURRENT generation, not the page-load sentinel 0: by the time a
            // bfcache restore fires, real interactions before the buyer navigated away
            // may already have moved changeGen well past 0, and syncFromServer bails
            // whenever its gen argument doesn't match changeGen — passing a stale 0 here
            // would make it discard this exact resync every time.
            if (e.persisted) syncFromServer(changeGen);
        });

        // +/- button clicks: safety-net after Pretix's own click handler runs
        document.querySelectorAll('.input-item-count-inc, .input-item-count-dec').forEach(function (btn) {
            btn.addEventListener('click', function () { setTimeout(onQuantityInput, 20); });
        });

        var form = document.querySelector('form[data-live-cart-form]');
        if (form) {
            form.addEventListener('submit', function (e) { e.preventDefault(); });
        }

        // Checkout must never race the cart sync. Pressing it used to either (a) navigate
        // before the debounce fired, checking out the *previous* quantity, or (b) navigate
        // while the cart was momentarily empty mid-update, which Pretix answers with
        // "Your cart is empty" and a bounce to a shop page without the voucher.
        var checkoutLink = document.querySelector('#autoconfig-live-cart a.autoconfig-pill-btn');
        if (checkoutLink) {
            checkoutLink.addEventListener('click', function (e) {
                if (!debounceTimer && settled) return;      // nothing in flight: go
                e.preventDefault();
                if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; handleChange(); }
                var href = checkoutLink.getAttribute('href');
                checkoutLink.setAttribute('aria-busy', 'true');
                pending.then(function () {
                    checkoutLink.removeAttribute('aria-busy');
                    if (getTotalQty() > 0) window.location.href = href;
                });
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
