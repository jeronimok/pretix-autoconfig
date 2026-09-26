(function () {
    // Hide the raw "Voucher code used: XXX" line from the cart box on this page.
    // Buyers arrived via a referral link — they never entered a code manually.
    document.querySelectorAll('.cart-icon-details .fa-tags').forEach(function (icon) {
        icon.closest('.cart-icon-details').remove();
    });
})();
