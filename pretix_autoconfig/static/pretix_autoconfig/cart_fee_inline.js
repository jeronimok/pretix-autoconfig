(function () {
    function parseAmount(text) {
        if (!text) return null;
        var normalized = text.replace(/\s+/g, "");
        var m = normalized.match(/([€$£])?(-?\d+[.,]\d{2})/);
        if (!m) return null;
        return {
            symbol: m[1] || "€",
            value: parseFloat(m[2].replace(",", "."))
        };
    }

    function parseDecimal(value) {
        var n = parseFloat((value || "0").toString().replace(",", "."));
        return Number.isFinite(n) ? n : 0;
    }

    function formatAmount(symbol, value) {
        return symbol + value.toFixed(2);
    }

    function parseCount(row) {
        var countCell = row.querySelector('[role="cell"].count, .count');
        if (!countCell) return 1;
        var m = (countCell.textContent || "").match(/\d+/);
        return m ? Math.max(1, parseInt(m[0], 10)) : 1;
    }

    function isBundledAddonRow(row) {
        return !!row.querySelector(".addon-signifier");
    }

    function findFeeRow(cart) {
        var rows = cart.querySelectorAll('[role="row"].cart-row');
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            if (r.classList.contains("editable") || r.classList.contains("total") || r.classList.contains("subtotal")) {
                continue;
            }
            var first = r.querySelector('[role="cell"]:first-child');
            var txt = (first && first.textContent ? first.textContent : "").toLowerCase();
            if (txt.indexOf("service fee") !== -1 || txt.indexOf("fee") !== -1) {
                return r;
            }
        }
        return null;
    }

    function roundTo2(x) {
        return Math.round((x + Number.EPSILON) * 100) / 100;
    }

    function computeFee(basePrice, feePercent, feeMin) {
        var fee = roundTo2((basePrice * feePercent) / 100);
        if (fee < feeMin) fee = feeMin;
        return fee;
    }

    function applyInlineFeePerTicket() {
        var cart = document.querySelector(".autoconfig-cart-box .panel.cart");
        if (!cart) return;
        if (cart.dataset.inlineFeeApplied === "1") return;

        var editableRows = cart.querySelectorAll('[role="row"].cart-row.editable');
        if (!editableRows.length) return;

        var feeRow = findFeeRow(cart);
        if (!feeRow) return;

        var primaryRows = [];
        editableRows.forEach(function (row) {
            if (!isBundledAddonRow(row)) primaryRows.push(row);
        });
        if (!primaryRows.length) return;

        // Always read the actual fee from the fee row (server-computed, correct even with vouchers).
        var feeCell = feeRow.querySelector('[role="cell"].price, [role="cell"].totalprice, .price, .totalprice');
        var actualFeeParsed = parseAmount(feeCell ? feeCell.textContent : "");

        // Fall back to computing from config only when the fee row has no parseable amount.
        var feePercent = parseDecimal(cart.getAttribute("data-autoconfig-fee-percent"));
        var feeMin = parseDecimal(cart.getAttribute("data-autoconfig-fee-min"));
        var hasConfigFee = feePercent > 0 || feeMin > 0;

        if (!actualFeeParsed && !hasConfigFee) return;

        var totalTickets = 0;
        primaryRows.forEach(function (row) { totalTickets += parseCount(row); });
        totalTickets = Math.max(1, totalTickets);

        primaryRows.forEach(function (row) {
            var single = row.querySelector('[role="cell"].singleprice, .singleprice.price');
            if (!single) return;

            var base = single.getAttribute("data-base-price");
            if (!base) {
                base = (single.textContent || "").trim();
                single.setAttribute("data-base-price", base);
            }

            var baseParsed = parseAmount(base);
            if (!baseParsed) return;

            var feeValue;
            if (actualFeeParsed) {
                feeValue = roundTo2(actualFeeParsed.value / totalTickets);
            } else {
                feeValue = computeFee(baseParsed.value, feePercent, feeMin);
            }

            var symbol = baseParsed.symbol || (actualFeeParsed ? actualFeeParsed.symbol : "€");
            var feeText = formatAmount(symbol, feeValue) + " fee";
            var newText = base + " + " + feeText;
            if (single.textContent.trim() !== newText) {
                single.textContent = newText;
            }
        });

        feeRow.style.display = "none";
        cart.dataset.inlineFeeApplied = "1";
    }

    function boot() {
        try {
            applyInlineFeePerTicket();
        } catch (e) {
            // Keep checkout functional even if formatting fails.
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
    window.addEventListener("load", boot, { once: true });
})();
