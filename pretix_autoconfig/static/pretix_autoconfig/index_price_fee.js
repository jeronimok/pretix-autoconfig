(function () {
    function parseDecimal(value) {
        var num = parseFloat((value || '0').toString().replace(',', '.'));
        return Number.isFinite(num) ? num : 0;
    }

    function parseCurrencyAmounts(text) {
        if (!text) return [];
        var normalized = text.replace(/ /g, ' ').trim();
        var re = /([€$£])?\s*(-?\d+[.,]\d{2})/g;
        var out = [];
        var m;
        while ((m = re.exec(normalized)) !== null) {
            out.push({
                symbol: m[1] || '€',
                value: parseFloat(m[2].replace(',', '.'))
            });
        }
        return out;
    }

    function formatAmount(symbol, value) {
        return symbol + value.toFixed(2);
    }

    function roundTo2(x) {
        return Math.round((x + Number.EPSILON) * 100) / 100;
    }

    function computeFee(amount, feePercent, feeMin) {
        var fee = roundTo2((amount * feePercent) / 100);
        if (fee < feeMin) fee = feeMin;
        return fee;
    }

    function buildInlineText(baseText, feeText) {
        return baseText + ' + ' + feeText + ' fee';
    }

    function applyIndexInlineFees() {
        var flow = document.querySelector('.autoconfig-flow');
        if (!flow || flow.dataset.indexInlineFeeApplied === '1') return;

        var feePercent = parseDecimal(flow.getAttribute('data-autoconfig-fee-percent'));
        var feeMin = parseDecimal(flow.getAttribute('data-autoconfig-fee-min'));
        if (feePercent <= 0 && feeMin <= 0) return;

        var rows = document.querySelectorAll('.autoconfig-ticket-rows article.product-row');
        rows.forEach(function (row) {
            var title = row.querySelector('.product-description h4, .product-description h5');
            var priceCell = row.querySelector('.price');
            if (!title || !priceCell) return;

            if (priceCell.querySelector('input.input-item-price')) return;

            var inline = row.querySelector('.autoconfig-item-inline-price');
            if (!inline) {
                inline = document.createElement('p');
                inline.className = 'autoconfig-item-inline-price';
                title.insertAdjacentElement('afterend', inline);
            }

            // When a voucher discount is applied, the price cell contains a <del> (original)
            // and an <ins> (discounted) element. Show the discounted price to the buyer but
            // compute the fee on the original price (discount applies to ticket only, not fee).
            var delEl = priceCell.querySelector('del');
            var insEl = priceCell.querySelector('ins');
            if (delEl && insEl) {
                var originalAmounts = parseCurrencyAmounts(delEl.textContent || '');
                var discountedAmounts = parseCurrencyAmounts(insEl.textContent || '');
                if (originalAmounts.length && discountedAmounts.length) {
                    var orig = originalAmounts[0];
                    var disc = discountedAmounts[0];
                    var fee = computeFee(orig.value, feePercent, feeMin);
                    inline.textContent = buildInlineText(formatAmount(disc.symbol, disc.value), formatAmount(disc.symbol, fee));
                    return;
                }
            }

            var amounts = parseCurrencyAmounts(priceCell.textContent || '');
            if (!amounts.length) return;

            if (amounts.length === 1) {
                var one = amounts[0];
                var fee = computeFee(one.value, feePercent, feeMin);
                inline.textContent = buildInlineText(formatAmount(one.symbol, one.value), formatAmount(one.symbol, fee));
                return;
            }

            var min = amounts[0];
            var max = amounts[amounts.length - 1];
            var minFee = computeFee(min.value, feePercent, feeMin);
            var maxFee = computeFee(max.value, feePercent, feeMin);
            var baseRange = formatAmount(min.symbol, min.value) + ' - ' + formatAmount(max.symbol, max.value);
            var feeRange = formatAmount(min.symbol, minFee) + ' - ' + formatAmount(max.symbol, maxFee);
            inline.textContent = buildInlineText(baseRange, feeRange);
        });

        flow.dataset.indexInlineFeeApplied = '1';
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyIndexInlineFees, { once: true });
    } else {
        applyIndexInlineFees();
    }
    window.addEventListener('load', applyIndexInlineFees, { once: true });
})();
