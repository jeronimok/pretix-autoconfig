"""Organizer-facing view of private-link cart holds, plus a way to release them.

A private link is backed by one voucher whose usages are consumed both by completed
orders (`voucher.redeemed`) and by tickets sitting in somebody's cart. Pretix's REST API
exposes the first but not the second, so the platform could only ever show "0 of 2 used"
while the link was in fact unusable because an invitee — or the organizer testing it —
had the allocation open in a cart. This endpoint closes that blind spot and lets the
organizer hand the tickets back without waiting out the reservation.

Reading cart rows is beyond "configuration only", which is otherwise this plugin's remit,
but there is no alternative that avoids the platform reading pretix's database directly.
"""

from django.db import transaction
from django.utils.timezone import now
from pretix.api.auth.token import TeamTokenAuthentication
from pretix.base.models import CartPosition, Event
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

PRIVATE_TAG_PREFIX = "private:"


def _token_team(request):
    team = getattr(getattr(request, "auth", None), "team", None)
    if team is None:
        raise PermissionDenied("Organizer API token required.")
    return team


def _event_for(request, organizer_slug, event_slug, permission):
    team = _token_team(request)
    if team.organizer.slug != organizer_slug:
        raise PermissionDenied("Token does not belong to this organizer.")
    if not getattr(team, permission, False):
        raise PermissionDenied(f"Token lacks {permission}.")
    try:
        event = Event.objects.get(organizer=team.organizer, slug=event_slug)
    except Event.DoesNotExist:
        raise NotFound("Event not found.")
    if not team.all_events and not team.limit_events.filter(pk=event.pk).exists():
        raise PermissionDenied("Token does not cover this event.")
    return event


def _holds_for(event, codes=None):
    """Live (unexpired) cart positions per private-link voucher code."""
    vouchers = event.vouchers.filter(tag__startswith=PRIVATE_TAG_PREFIX)
    if codes:
        vouchers = vouchers.filter(code__in=codes)

    out = {}
    for voucher in vouchers:
        positions = CartPosition.objects.filter(event=event, voucher=voucher, expires__gte=now())
        held = positions.count()
        expires = max((p.expires for p in positions), default=None)
        out[voucher.code] = {
            "held": held,
            "held_until": expires.isoformat() if expires else None,
            "max_usages": voucher.max_usages,
            "redeemed": voucher.redeemed,
            "available": max(0, (voucher.max_usages or 0) - (voucher.redeemed or 0) - held),
        }
    return out


class PrivateLinkHoldsViewSet(viewsets.ViewSet):
    authentication_classes = [TeamTokenAuthentication]
    permission_classes = []

    def list(self, request, organizer=None):
        event_slug = request.query_params.get("event", "").strip()
        if not event_slug:
            return Response({"event": "Required."}, status=status.HTTP_400_BAD_REQUEST)
        event = _event_for(request, organizer, event_slug, "can_view_orders")
        return Response({"holds": _holds_for(event)})

    @action(detail=False, methods=["post"])
    def release(self, request, organizer=None):
        event_slug = (request.data.get("event") or "").strip()
        code = (request.data.get("code") or "").strip()
        if not event_slug or not code:
            return Response({"detail": "event and code are required."}, status=status.HTTP_400_BAD_REQUEST)

        event = _event_for(request, organizer, event_slug, "can_change_orders")
        voucher = event.vouchers.filter(code=code, tag__startswith=PRIVATE_TAG_PREFIX).first()
        if voucher is None:
            raise NotFound("Private-link voucher not found.")

        # Scoped to this one voucher on purpose: this throws away a reservation that may
        # belong to a real buyer who is mid-checkout, so it must never be able to reach
        # anything else.
        with transaction.atomic():
            released, _ = CartPosition.objects.filter(event=event, voucher=voucher).delete()

        return Response({"released": released, "holds": _holds_for(event, codes=[code])})
