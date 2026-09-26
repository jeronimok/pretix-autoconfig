(function () {
    var STORAGE_KEY = 'autoconfig_referral_url';
    var isRedeemPage = window.location.pathname.indexOf('/redeem') !== -1
                       && window.location.search.indexOf('voucher=') !== -1;

    if (isRedeemPage) {
        sessionStorage.setItem(STORAGE_KEY, window.location.href);
        return;
    }

    // On the post-cart-operation page (fallback non-AJAX flow): go back to the referral URL
    // unless the live cart is already active.
    var isPostCartPage = window.location.search.indexOf('require_cookie=true') !== -1;
    var referralUrl = sessionStorage.getItem(STORAGE_KEY);
    var liveCartEl = document.getElementById('autoconfig-live-cart');
    var cartIsActive = liveCartEl && liveCartEl.getAttribute('aria-hidden') !== 'true';

    if (isPostCartPage && !cartIsActive && referralUrl) {
        window.location.replace(referralUrl);
    }
}());
