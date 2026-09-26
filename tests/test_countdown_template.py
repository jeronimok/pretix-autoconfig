"""Regression guards for the live-cart countdown and the no-reserve-on-load rule.

Two specs meet in this file. 2026-09-08-private-link-countdown-stuck-expired: a lapsed
cart must not freeze the card on "Cart expired" — the template always emits a
``#cart-deadline-short`` element for pretix's cart.js to drive, and a lapsed server cart
is never adopted. 2026-09-20-private-link-voucher-lock: loading the page must not reserve
anything (quantities start at 0, state comes from autoconfig/cart.json, the ``prefill``
parameter is gone), and "Reserved for" is only shown next to a live MM:SS. Behaviour is
covered by runtime verification; these assert on source so the fixes can't be silently
reverted.
"""

from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "pretix_autoconfig"
INDEX = BASE / "templates" / "pretixpresale" / "event" / "index.html"
LIVE_CART_JS = BASE / "static" / "pretix_autoconfig" / "live_cart.js"


@pytest.fixture
def index_src():
    return INDEX.read_text()


@pytest.fixture
def js_src():
    return LIVE_CART_JS.read_text()


class TestIndexTemplate:
    def test_positions_branch_always_emits_cart_deadline_short(self, index_src):
        # The live-cart card renders it exactly twice: once in the cart.positions branch,
        # once in the empty-cart else branch. Both must be unconditional.
        assert index_src.count('id="cart-deadline-short"') == 2

    def test_no_bare_cart_expired_literal_on_visible_line(self, index_src):
        # The sr-only #cart-deadline span may still branch its text (cart.js overwrites it);
        # the visible line must not print a standalone {% trans "Cart expired." %}.
        # After the fix the only "Cart expired." string in the visible footer is gone.
        footer = index_src.split("autoconfig-cart-summary-footer", 1)[1].split("</div>", 1)[0]
        assert "Cart expired." not in footer or "sr-only" in footer  # sr-only span text is allowed


class TestDeadlinePrefix:
    def test_prefix_is_its_own_element_in_both_branches(self, index_src):
        # Plain text next to the slot cart.js writes into rendered "Reserved for Cart expired".
        assert index_src.count('id="cart-deadline-prefix"') == 2

    def test_empty_cart_branch_starts_hidden(self, index_src):
        assert '<span id="cart-deadline-prefix" hidden>' in index_src

    def test_js_hides_prefix_unless_slot_holds_a_countdown(self, js_src):
        assert "syncDeadlinePrefix" in js_src
        assert "prefix.hidden = !/^\\d{1,2}:\\d{2}$/.test" in js_src


class TestLiveCartJs:
    def test_adopts_server_cart_only_when_live(self, js_src):
        # A lapsed-but-not-yet-swept cart must be ignored, or the countdown never starts.
        assert "serverState.expires * 1000 > Date.now()" in js_src

    def test_state_comes_from_the_cart_json_endpoint(self, js_src):
        assert "data-cart-state-url" in js_src
        assert "data-live-cart-positions" not in js_src

    def test_quantities_start_at_zero(self, js_src):
        assert "inputs.forEach(function (input) { input.value = 0; });" in js_src

    def test_no_prefill_anywhere(self, js_src, index_src):
        # Loading the page must not reserve tickets: no prefill parsing, no first sync.
        assert "prefill" not in js_src
        assert "prefill" not in index_src

    def test_page_load_issues_no_cart_write(self, js_src):
        init = js_src.split("function init()", 1)[1]
        # handleChange() is only ever reached from a user gesture: a debounced input, or the
        # checkout click flushing it. Never from init's own body, which is what turned a mere
        # pageview into a 30-minute reservation.
        top_level_calls = [ln for ln in init.split("\n") if ln.startswith("        handleChange()")]
        assert not top_level_calls


class TestIncrementalCartOps:
    """Slice 3: a quantity change must never clear-then-re-add the basket.

    Pretix's re-add is partial, so a clear followed by a rejected re-add left the buyer
    with fewer tickets than they started with — or a different mix. Changes are now posted
    as deltas, and a removal addresses one position id.
    """

    def test_add_posts_only_the_delta(self, js_src):
        assert "function doCartAdd(gen, deltas)" in js_src

    def test_removal_uses_position_ids(self, js_src):
        assert "function doCartRemove(gen, positionIds)" in js_src
        assert "fd.append('id', id);" in js_src
        assert "data-cart-remove-url" in js_src

    def test_change_diffs_against_server_state(self, js_src):
        assert "var have = serverState.quantities[key] || 0;" in js_src

    def test_clear_only_when_the_buyer_wants_an_empty_cart(self, js_src):
        handle = js_src.split("function handleChange()", 1)[1].split("function onQuantityInput", 1)[0]
        assert handle.count("doCartClear") == 1
        assert "if (totalQty === 0)" in handle


class TestAllocationCap:
    """Slice 5: the UI refuses to ask for more than the link covers."""

    def test_cap_is_rendered_and_read(self, index_src, js_src):
        assert "data-cart-max-total" in index_src
        assert "This link covers" in index_src
        assert "function linkCap()" in js_src

    def test_over_cap_never_reaches_the_server(self, js_src):
        handle = js_src.split("function handleChange()", 1)[1].split("function onQuantityInput", 1)[0]
        over_cap = handle.split("if (cap !== null && totalQty > cap)", 1)[1].split("clearCartError", 1)[0]
        assert "return;" in over_cap
        assert "doCartAdd" not in over_cap

    def test_pretix_voucher_wording_is_reworded(self, js_src):
        assert "function humaneError" in js_src
        assert "Someone else is using this link right now" in js_src


class TestCheckoutIsNeverRaced:
    """Slice 4: pressing checkout mid-change must not lose it or hit an empty cart."""

    def test_pending_change_is_flushed_before_navigating(self, js_src):
        click = js_src.split("checkoutLink.addEventListener('click'", 1)[1]
        assert "clearTimeout(debounceTimer)" in click
        assert "handleChange();" in click
        assert "pending.then(" in click

    def test_navigation_waits_for_a_settled_cart(self, js_src):
        assert "if (!debounceTimer && settled) return;" in js_src


class TestCheckoutDebounceNeverStaysArmed:
    """A quantity change's debounce timer must clear itself once it fires, or the
    checkout-click guard (`!debounceTimer && settled`) can never take its fast path again —
    every checkout click after the first-ever change force-invokes handleChange() a second
    time, racing whichever sync is already in flight. Verified end to end: this caused a
    transient checkout error that only a hard refresh cleared. See spec
    2026-09-20-private-link-voucher-lock."""

    def test_debounce_timer_is_nulled_when_it_fires(self, js_src):
        block = js_src.split("function onQuantityInput()", 1)[1].split("function init()", 1)[0]
        assert "debounceTimer = null;" in block
        assert "handleChange();" in block


class TestCartExtendFormIsNeverNested:
    """ "Renew reservation" (fragment_modals.html's global expiry dialog) submits
    $("#cart-extend-form"). On the "questions" and "confirm" checkout steps that form did
    not exist at all — those steps skip fragment_cart.html — so clicking renew silently did
    nothing. It must live outside {% block inner %}: both steps wrap their own content in
    one big <form>, and HTML forbids nested forms — a browser drops a nested <form> start
    tag silently, and the stray closing </form> then closes the OUTER form early instead."""

    @pytest.fixture
    def base_src(self):
        return (
            Path(__file__).resolve().parent.parent
            / "pretix_autoconfig"
            / "templates"
            / "pretixpresale"
            / "event"
            / "checkout_base.html"
        ).read_text()

    @pytest.fixture
    def confirm_src(self):
        return (
            Path(__file__).resolve().parent.parent
            / "pretix_autoconfig"
            / "templates"
            / "pretixpresale"
            / "event"
            / "checkout_confirm.html"
        ).read_text()

    def test_extend_form_exists_for_questions_and_confirm_steps(self, base_src):
        # The extend form must appear after the topbar and before {% block inner %} —
        # i.e. inside the questions/confirm branch, not the other steps' branch.
        topbar_pos = base_src.index('<div class="autoconfig-checkout-topbar">')
        form_pos = base_src.index('id="cart-extend-form"')
        inner_pos = base_src.index("{% block inner %}")
        assert topbar_pos < form_pos < inner_pos
        assert "presale:event.cart.extend" in base_src

    def test_extend_form_is_outside_block_inner(self, base_src):
        before_inner, _, after_inner = base_src.partition("{% block inner %}")
        assert 'id="cart-extend-form"' in before_inner
        assert 'id="cart-extend-form"' not in after_inner

    def test_confirm_template_does_not_duplicate_the_form(self, confirm_src):
        # A second copy here would nest inside confirm's own submit <form> and get
        # silently dropped by the browser, with its stray </form> closing the outer one.
        assert 'id="cart-extend-form"' not in confirm_src


class TestBfcacheRestoreResyncs:
    """Back/forward navigation can restore this exact page from the bfcache: a frozen DOM
    snapshot repainted with no new navigation, so DOMContentLoaded — and this whole
    init() — never runs again. Whatever quantities were on screen when the buyer left for
    checkout just sit there, un-reconciled with the real server cart by the time they come
    back. See spec 2026-09-20-private-link-voucher-lock."""

    def test_pageshow_listener_resyncs_on_persisted_restore(self, js_src):
        assert "addEventListener('pageshow'" in js_src
        assert "e.persisted" in js_src

    def test_resync_uses_the_current_generation_not_the_page_load_sentinel(self, js_src):
        # Passing the literal 0 here (the value init() uses on first load) would make
        # syncFromServer discard the resync the moment any real change had already
        # advanced changeGen past 0 before the buyer navigated away — i.e. almost always.
        pageshow_block = js_src.split("addEventListener('pageshow'", 1)[1].split("});", 1)[0]
        assert "syncFromServer(changeGen)" in pageshow_block
        assert "syncFromServer(0)" not in pageshow_block
