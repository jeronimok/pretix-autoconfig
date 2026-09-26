"""Combine a discount code with a sold-out-bypass private link.

Covers signals_combo_voucher: the tag helpers, the CartManager.apply_voucher wrap
(delegate vs synthesise), and the order-lifecycle counter mirror.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils.timezone import now
from pretix.base.models import Event, Item, Organizer, Quota, Voucher

from pretix_autoconfig import signals_combo_voucher as m

# ---------------------------------------------------------------------------
# tag helpers (no DB)
# ---------------------------------------------------------------------------


class _V:
    def __init__(self, code="", tag=""):
        self.code = code
        self.tag = tag


class TestTagHelpers:
    def test_is_combo_voucher_true(self):
        assert m.is_combo_voucher(_V("CMB-ABC123", "private:4:combo:SUMMER26"))

    @pytest.mark.parametrize(
        "v",
        [
            None,
            _V("SUMMER26", "discount:x"),
            _V("PRIV-ABC", "private:4"),
            _V("CMB-ABC", "private:4"),  # right code, wrong tag
            _V("SUMMER26", "private:4:combo:SUMMER26"),  # right tag, wrong code
        ],
    )
    def test_is_combo_voucher_false(self, v):
        assert not m.is_combo_voucher(v)

    def test_parse_combo_tag(self):
        assert m._parse_combo_tag("private:4:combo:SUMMER26") == ("private:4", "SUMMER26")

    @pytest.mark.parametrize("tag", ["private:4", "private:4:combo:A:B", "discount:4:combo:A", "", None])
    def test_parse_combo_tag_none(self, tag):
        assert m._parse_combo_tag(tag) is None


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _scopes_disabled(db):
    from django_scopes import scopes_disabled

    with scopes_disabled():
        yield


@pytest.fixture
def organizer(db):
    return Organizer.objects.create(name="Org", slug="org")


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer,
        name="E",
        slug="e",
        date_from=now() + timedelta(days=30),
        plugins="pretix_autoconfig",
    )


@pytest.fixture
def sold_out(event):
    item = Item.objects.create(event=event, name="GA", default_price=Decimal("25.00"), admission=True)
    Quota.objects.create(event=event, name="GA", size=0).items.add(item)
    return item


@pytest.fixture
def priv_voucher(event):
    return Voucher.objects.create(
        event=event,
        code="PRIV-LINK",
        tag="private:7",
        price_mode="none",
        value=None,
        allow_ignore_quota=True,
        max_usages=4,
        valid_until=now() + timedelta(days=20),
    )


@pytest.fixture
def discount_voucher(event):
    return Voucher.objects.create(
        event=event,
        code="SUMMER26",
        tag="discount:abc",
        price_mode="percent",
        value=Decimal("20.00"),
        max_usages=10,
        allow_ignore_quota=False,
    )


# ---------------------------------------------------------------------------
# CartManager.apply_voucher wrap
# ---------------------------------------------------------------------------


def _cart_manager(event, cart_id="testcart@api"):
    from pretix.base.services.cart import CartManager

    return CartManager(
        event=event,
        cart_id=cart_id,
        sales_channel=event.organizer.sales_channels.get(identifier="web"),
    )


def _add_position(event, item, voucher, cart_id="testcart@api", expires=None):
    from pretix.base.models import CartPosition

    return CartPosition.objects.create(
        event=event,
        cart_id=cart_id,
        item=item,
        voucher=voucher,
        price=Decimal("25.00"),
        listed_price=Decimal("25.00"),
        price_after_voucher=Decimal("25.00"),
        expires=expires or (now() + timedelta(minutes=10)),
        max_extend=now() + timedelta(minutes=30),
    )


@pytest.mark.django_db
class TestApplyVoucherWrap:
    def test_combines_on_bypass_cart(self, event, sold_out, priv_voucher, discount_voucher):
        _add_position(event, sold_out, priv_voucher)
        _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        cm.apply_voucher("SUMMER26")
        cm.commit()

        from pretix.base.models import CartPosition

        positions = CartPosition.objects.filter(event=event)
        assert positions.count() == 2
        for p in positions:
            assert p.voucher.code.startswith("CMB-")
            assert p.voucher.tag == "private:7:combo:SUMMER26"
            assert p.voucher.allow_ignore_quota is True
            assert p.voucher.price_mode == "percent"
            assert p.price == Decimal("20.00")

    def test_cap_is_min_of_link_code_and_cart(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.max_usages = 3
        priv_voucher.save()
        discount_voucher.max_usages = 5
        discount_voucher.save()
        for _ in range(2):
            _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        cm.apply_voucher("SUMMER26")
        synth = event.vouchers.get(code__startswith="CMB-")
        assert synth.max_usages == 2  # min(3, 5, 2)

    def test_refuses_when_link_allocation_short(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.max_usages = 1
        priv_voucher.save()
        for _ in range(2):
            _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        from pretix.base.services.cart import CartError

        with pytest.raises(CartError):
            cm.apply_voucher("SUMMER26")
        assert not event.vouchers.filter(code__startswith="CMB-").exists()

    def test_another_live_combined_cart_holds_a_slot(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.max_usages = 1
        priv_voucher.save()
        # cart A combines the only slot (committed -> its line now holds the CMB- voucher)
        _add_position(event, sold_out, priv_voucher, cart_id="A")
        cm_a = _cart_manager(event, cart_id="A")
        cm_a.apply_voucher("SUMMER26")
        cm_a.commit()
        # cart B now cannot
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        from pretix.base.services.cart import CartError

        with pytest.raises(CartError):
            _cart_manager(event, cart_id="B").apply_voucher("SUMMER26")
        assert event.vouchers.filter(code__startswith="CMB-").count() == 1

    def test_abandoned_combine_does_not_hold_a_slot(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.max_usages = 1
        priv_voucher.save()
        # cart A combined then went stale: its position is expired
        _add_position(event, sold_out, priv_voucher, cart_id="A")
        cm_a = _cart_manager(event, cart_id="A")
        cm_a.apply_voucher("SUMMER26")
        cm_a.commit()
        from pretix.base.models import CartPosition

        CartPosition.objects.filter(cart_id="A").update(expires=now() - timedelta(minutes=1))
        # cart B is not blocked by the orphan CMB- voucher
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        _cart_manager(event, cart_id="B").apply_voucher("SUMMER26")
        assert event.vouchers.filter(code__startswith="CMB-").count() == 2

    def test_delegates_for_unknown_code(self, event, sold_out, priv_voucher):
        _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        from pretix.base.services.cart import CartError

        with pytest.raises(CartError):
            cm.apply_voucher("NOPE")
        assert not event.vouchers.filter(code__startswith="CMB-").exists()

    def test_delegates_for_discounted_link(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.price_mode = "percent"
        priv_voucher.value = Decimal("10.00")
        priv_voucher.save()
        _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        # a percent private-link cart is not a pure bypass cart -> original path
        try:
            cm.apply_voucher("SUMMER26")
        except Exception:
            pass
        assert not event.vouchers.filter(code__startswith="CMB-").exists()

    def test_delegates_for_mixed_cart(self, event, sold_out, priv_voucher, discount_voucher):
        _add_position(event, sold_out, priv_voucher)
        _add_position(event, sold_out, None)  # a line with no voucher
        cm = _cart_manager(event)
        try:
            cm.apply_voucher("SUMMER26")
        except Exception:
            pass
        assert not event.vouchers.filter(code__startswith="CMB-").exists()

    def test_second_apply_on_combined_cart_delegates(self, event, sold_out, priv_voucher, discount_voucher):
        _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        cm.apply_voucher("SUMMER26")
        cm.commit()
        # A second code: the line now carries a :combo: voucher -> original path
        other = Voucher.objects.create(
            event=event, code="WINTER26", tag="discount:z", price_mode="percent", value=Decimal("5.00"), max_usages=10
        )
        cm2 = _cart_manager(event)
        try:
            cm2.apply_voucher(other.code)
        except Exception:
            pass
        assert event.vouchers.filter(code__startswith="CMB-").count() == 1


@pytest.mark.django_db
class TestReenterCodeForAddedLines:
    """Spec 2026-09-23-private-link-back-navigation: a line added after the code was applied
    stays on the plain link voucher until the buyer enters the code again."""

    def _combine_one_then_add_one(self, event, sold_out, priv_voucher):
        _add_position(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        cm.apply_voucher("SUMMER26")
        cm.commit()
        _add_position(event, sold_out, priv_voucher)

    def test_reentering_the_code_discounts_the_added_line(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.models import CartPosition

        self._combine_one_then_add_one(event, sold_out, priv_voucher)
        cm = _cart_manager(event)
        cm.apply_voucher("summer26")
        cm.commit()

        positions = CartPosition.objects.filter(event=event)
        assert positions.count() == 2
        assert all(m.is_combo_voucher(p.voucher) for p in positions)
        assert all(p.price == Decimal("20.00") for p in positions)
        # one synthesised voucher per apply; each covers only its own lines
        assert sorted(event.vouchers.filter(code__startswith="CMB-").values_list("max_usages", flat=True)) == [1, 1]

    def test_already_combined_lines_are_left_alone(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.models import CartPosition

        self._combine_one_then_add_one(event, sold_out, priv_voucher)
        first = CartPosition.objects.filter(event=event, voucher__code__startswith="CMB-").get()
        first_voucher_id = first.voucher_id
        cm = _cart_manager(event)
        cm.apply_voucher("SUMMER26")
        cm.commit()
        first.refresh_from_db()
        assert first.voucher_id == first_voucher_id

    def test_a_different_code_is_refused(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        self._combine_one_then_add_one(event, sold_out, priv_voucher)
        Voucher.objects.create(
            event=event, code="WINTER26", tag="discount:z", price_mode="percent", value=Decimal("5.00"), max_usages=10
        )
        with pytest.raises(CartError, match="Only one promotional code per order"):
            _cart_manager(event).apply_voucher("WINTER26")
        assert event.vouchers.filter(code__startswith="CMB-").count() == 1

    def test_own_combined_lines_count_against_the_code(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        discount_voucher.max_usages = 1
        discount_voucher.save()
        self._combine_one_then_add_one(event, sold_out, priv_voucher)
        with pytest.raises(CartError, match="stay at the full price"):
            _cart_manager(event).apply_voucher("SUMMER26")
        assert event.vouchers.filter(code__startswith="CMB-").count() == 1

    def test_own_combined_lines_count_against_the_link(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        priv_voucher.max_usages = 1
        priv_voucher.save()
        self._combine_one_then_add_one(event, sold_out, priv_voucher)
        with pytest.raises(CartError, match="stay at the full price"):
            _cart_manager(event).apply_voucher("SUMMER26")


# ---------------------------------------------------------------------------
# counter mirror
# ---------------------------------------------------------------------------


@pytest.fixture
def combo_order(event, sold_out, priv_voucher, discount_voucher):
    from pretix.base.models import Order, OrderPosition

    synth = Voucher.objects.create(
        event=event,
        code="CMB-XYZ",
        tag="private:7:combo:SUMMER26",
        price_mode="percent",
        value=Decimal("20.00"),
        allow_ignore_quota=True,
        max_usages=2,
    )
    order = Order.objects.create(
        event=event,
        code="ORD001",
        status=Order.STATUS_PAID,
        email="b@x.com",
        datetime=now(),
        expires=now() + timedelta(days=1),
        total=Decimal("40.00"),
        sales_channel=event.organizer.sales_channels.get(identifier="web"),
    )
    for _ in range(2):
        OrderPosition.objects.create(
            order=order,
            item=sold_out,
            voucher=synth,
            price=Decimal("20.00"),
            positionid=1,
        )
    return order


@pytest.mark.django_db
class TestCounterMirror:
    def _redeemed(self, event):
        return (
            event.vouchers.get(code="PRIV-LINK").redeemed,
            event.vouchers.get(code="SUMMER26").redeemed,
        )

    def test_placed_bumps_both_parents(self, event, combo_order):
        m.on_order_placed(event, combo_order)
        assert self._redeemed(event) == (2, 2)
        assert combo_order.meta_info_data["autoconfig_combo_counted"] == [["private:7", "SUMMER26", 2]]

    def test_placed_is_idempotent(self, event, combo_order):
        m.on_order_placed(event, combo_order)
        m.on_order_placed(event, combo_order)
        assert self._redeemed(event) == (2, 2)

    def test_canceled_reverts(self, event, combo_order):
        m.on_order_placed(event, combo_order)
        m.on_order_canceled(event, combo_order)
        assert self._redeemed(event) == (0, 0)
        assert combo_order.meta_info_data.get("autoconfig_combo_reverted") is True

    def test_canceled_is_idempotent(self, event, combo_order):
        m.on_order_placed(event, combo_order)
        m.on_order_canceled(event, combo_order)
        m.on_order_expired(event, combo_order)
        assert self._redeemed(event) == (0, 0)

    def test_reactivated_rebumps(self, event, combo_order):
        m.on_order_placed(event, combo_order)
        m.on_order_canceled(event, combo_order)
        m.on_order_reactivated(event, combo_order)
        assert self._redeemed(event) == (2, 2)

    def test_no_op_for_non_combo_order(self, event, sold_out, priv_voucher):
        from pretix.base.models import Order, OrderPosition

        order = Order.objects.create(
            event=event,
            code="ORD002",
            status=Order.STATUS_PAID,
            email="b@x.com",
            datetime=now(),
            expires=now() + timedelta(days=1),
            total=Decimal("25.00"),
            sales_channel=event.organizer.sales_channels.get(identifier="web"),
        )
        OrderPosition.objects.create(
            order=order, item=sold_out, voucher=priv_voucher, price=Decimal("25.00"), positionid=1
        )
        m.on_order_placed(event, order)
        assert "autoconfig_combo_counted" not in (order.meta_info_data or {})


# ---------------------------------------------------------------------------
# the link's limit sees combined lines (spec 2026-09-23-private-link-combined-cap)
# ---------------------------------------------------------------------------


def _combine(event, sold_out, priv_voucher, cart_id, n):
    for _ in range(n):
        _add_position(event, sold_out, priv_voucher, cart_id=cart_id)
    cm = _cart_manager(event, cart_id=cart_id)
    cm.apply_voucher("SUMMER26")
    cm.commit()


def _add_link_ticket(event, item, cart_id):
    cm = _cart_manager(event, cart_id=cart_id)
    cm.add_new_items([{"item": item.pk, "variation": None, "count": 1, "voucher": "PRIV-LINK"}])
    cm.commit()


@pytest.mark.django_db
class TestLinkCapSeesCombinedLines:
    def test_another_buyers_combined_lines_use_up_the_link(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        priv_voucher.max_usages = 2
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        with pytest.raises(CartError):
            _add_link_ticket(event, sold_out, "B")

    def test_own_combined_lines_use_up_the_link(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        priv_voucher.max_usages = 2
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        with pytest.raises(CartError):
            _add_link_ticket(event, sold_out, "A")

    def test_room_left_is_still_sold(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.models import CartPosition

        priv_voucher.max_usages = 3
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        _add_link_ticket(event, sold_out, "B")
        assert CartPosition.objects.filter(cart_id="B", voucher=priv_voucher).count() == 1

    def test_expired_combined_lines_free_the_link(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.models import CartPosition

        priv_voucher.max_usages = 2
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        CartPosition.objects.filter(cart_id="A").update(expires=now() - timedelta(minutes=1))
        _add_link_ticket(event, sold_out, "B")
        assert CartPosition.objects.filter(cart_id="B").count() == 1

    def test_other_vouchers_are_unaffected(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services import cart as cart_service

        diff = {discount_voucher: 1}
        ok, depend = cart_service._get_voucher_availability(event, diff, now(), [])
        assert ok[discount_voucher] == discount_voucher.max_usages
        assert discount_voucher not in depend

    def test_api_cart_uses_the_same_check(self):
        from pretix.api.views import cart as api_cart
        from pretix.base.services import cart as cart_service

        assert api_cart._get_voucher_availability is cart_service._get_voucher_availability
        assert getattr(cart_service._get_voucher_availability, "_autoconfig_combined_cap", False)


@pytest.mark.django_db
class TestCheckoutBackstop:
    def _cart(self, cart_id):
        from pretix.base.models import CartPosition

        return list(CartPosition.objects.filter(cart_id=cart_id))

    def test_refuses_a_cart_that_would_exceed_the_link(self, event, sold_out, priv_voucher, discount_voucher):
        from pretix.base.services.cart import CartError

        priv_voucher.max_usages = 3
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        # got into cart B without the add-to-cart check (e.g. before this fix)
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        with pytest.raises(CartError, match="fewer tickets left"):
            m.on_validate_cart(event, positions=self._cart("B"))

    def test_counts_placed_orders(self, event, sold_out, priv_voucher):
        from pretix.base.services.cart import CartError

        priv_voucher.max_usages = 2
        priv_voucher.redeemed = 2
        priv_voucher.save()
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        with pytest.raises(CartError):
            m.on_validate_cart(event, positions=self._cart("B"))

    def test_a_cart_within_the_link_passes(self, event, sold_out, priv_voucher, discount_voucher):
        priv_voucher.max_usages = 3
        priv_voucher.save()
        _combine(event, sold_out, priv_voucher, "A", 2)
        _add_position(event, sold_out, priv_voucher, cart_id="A")
        m.on_validate_cart(event, positions=self._cart("A"))  # 3 of 3: fine

    def test_cart_without_a_link_is_ignored(self, event, sold_out, discount_voucher):
        _add_position(event, sold_out, discount_voucher, cart_id="C")
        m.on_validate_cart(event, positions=self._cart("C"))

    def test_registered_on_validate_cart(self, event, sold_out, priv_voucher):
        from pretix.base.services.cart import CartError
        from pretix.base.signals import validate_cart

        # Organizer-level plugin: Pretix only delivers the signal where the organizer has it on
        # (production: plugins_organizer_default).
        event.organizer.plugins = "pretix_autoconfig"
        event.organizer.save()
        priv_voucher.max_usages = 0
        priv_voucher.save()
        _add_position(event, sold_out, priv_voucher, cart_id="B")
        with pytest.raises(CartError):
            validate_cart.send(sender=event, positions=self._cart("B"))


@pytest.mark.django_db
def test_reviving_an_expired_combined_cart_counts_its_lines(event, sold_out, priv_voucher, discount_voucher):
    from pretix.base.models import CartPosition
    from pretix.base.services import cart as cart_service

    priv_voucher.max_usages = 2
    priv_voucher.save()
    _combine(event, sold_out, priv_voucher, "A", 2)
    CartPosition.objects.filter(cart_id="A").update(expires=now() - timedelta(minutes=1))
    reviving = list(CartPosition.objects.filter(cart_id="A").values_list("pk", flat=True))
    ok, depend = cart_service._get_voucher_availability(event, {priv_voucher: 1}, now(), reviving)
    assert ok[priv_voucher] == 0
    assert priv_voucher in depend
