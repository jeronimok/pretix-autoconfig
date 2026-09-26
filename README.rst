pretix-autoconfig
=================

A plugin for `pretix`_ that auto-configures new events and organizers, applies service fees at checkout,
and provides webshop branding customisation.

What it does
------------

**On new organizer creation**

- Sets the organizer's primary brand colour (optional, via ``PRETIX_AUTOCONFIG_PRIMARY_COLOR``)
- Sets default webshop canvas and card colours

**On new event creation**

- Configures ticket download settings
- Enables and configures Stripe payment methods (live or test mode, derived from the publishable key)
- Disables specified payment providers
- Creates VAT tax rules (optional, via ``PRETIX_AUTOCONFIG_VAT_RATES``)
- Adds a terms & conditions checkbox to the checkout confirm step (optional, via ``PRETIX_AUTOCONFIG_TERMS_URL``)
- Applies a branded PDF ticket layout (optional, via ``PRETIX_AUTOCONFIG_TICKET_BACKGROUND``)
- Closes the webshop once the presale window ends: hides the item list and shows a "sales have ended" text
  instead of a shop that still looks sellable (the event's ``live`` flag is never touched)
- Sends a single confirmation email with the tickets (suppresses the separate "order placed" email) and sets
  the paid/free email subject and body
- Turns off pretix's built-in attendee name fields
- Creates a hidden, free "Guest" item that is not sold in the webshop but can be used for backend-issued
  guest tickets

**At checkout**

- Applies a service fee: ``max(total × fee_percent, fee_min)``
- No fee is applied if ``fee_percent`` is not configured

**Webshop**

- Injects CSS custom properties (``--autoconfig-*``) via the ``html_head`` signal for consistent branding
- Overrides several checkout templates to improve layout and UX
- Live cart: quantity changes on the event page sync to the cart immediately, with a reservation countdown
- Tracks referral codes in the session
- Renders a configurable "powered by" footer and email footer
- Optionally replaces pretix's favicon (``PRETIX_AUTOCONFIG_FAVICON_URL``)
- Allows the shop and checkout pages to be embedded in an iframe (only the routes pretix itself treats as
  embed-safe; account and order-management pages keep ``X-Frame-Options: DENY``). Checkout breaks out of the
  iframe so redirect-based Stripe payment methods work
- Redirects a bare visit to ``/`` to the platform URL, if one is configured

**Private links**

A "private link" is a shop URL carrying a voucher tagged ``private:<id>`` that lets invitees buy tickets
even when the event is sold out (``allow_ignore_quota``), up to the voucher's ``max_usages``.

- The link's remaining allowance is shown and enforced as a cap on the shop page; loading the page never
  reserves tickets
- The link survives checkout redirects back to the shop and the browser "Back" button
- Buyers can still enter a discount code: the plugin creates a single combined voucher carrying both the
  discount and the sold-out bypass, and mirrors each redemption onto the two original vouchers so their
  usage limits stay enforced

**API**

- Exposes ``POST /api/v1/autoconfig/provision-organizer/`` to create an organizer, team, and API token
  in a single call (protected by a shared secret)
- Patches ``OrganizerSettingsSerializer`` to expose ``autoconfig_branding_*`` settings via the API
- ``GET /api/v1/organizers/<organizer>/autoconfig/private-link-holds/?event=<slug>`` lists tickets held in
  carts against each private-link voucher (pretix's REST API only exposes completed redemptions).
  Requires a team API token with *can view orders*
- ``POST /api/v1/organizers/<organizer>/autoconfig/private-link-holds/release/`` with ``event`` and
  ``code`` deletes the cart positions holding that one voucher. Requires *can change orders*
- ``GET /<organizer>/<event>/autoconfig/cart.json`` returns the current visitor's own cart (resolved from
  the session only; there is no cart-id parameter). Used by the live cart script

Installation
------------

The plugin is not published on PyPI. Install it from source into the same virtual environment as pretix::

    pip install git+https://github.com/jeronimok/pretix-autoconfig.git

For development::

    pip install -e /path/to/pretix-autoconfig

After installing, restart your pretix server and Celery worker.

Enable the plugin for all organizers by default in ``pretix.cfg``::

    [pretix]
    plugins_organizer_default = pretix_autoconfig
    plugins_default = ...,pretix.plugins.stripe

Configuration
-------------

Add a ``[pretix_autoconfig]`` section to ``pretix.cfg``. All values can also be set via environment
variables (env vars take precedence).

Service fees
^^^^^^^^^^^^

::

    [pretix_autoconfig]
    # Percentage fee per order (e.g. 2.5 for 2.5%). No fee if unset.
    fee_percent =
    # Minimum fee floor in the event currency (e.g. 0.50). No floor if unset.
    fee_min =

The fee is calculated per ticket as ``max(ticket_price × fee_percent%, fee_min)`` and summed over
the cart, so the minimum acts as a floor on each ticket rather than on the order. If neither
``fee_percent`` nor ``fee_min`` is set, no fee is added.

Payment methods
^^^^^^^^^^^^^^^

::

    [pretix_autoconfig]
    # Comma-separated Stripe methods to enable on new events
    stripe_methods = card,walletdetection
    # Comma-separated payment providers to disable on new events
    disabled_providers = giftcard

Valid Stripe method names: ``card``, ``ideal``, ``walletdetection`` (Apple Pay + Google Pay),
``bancontact``, ``eps``, ``giropay``, ``klarna``, ``p24``, ``sepa_debit``, and others supported
by pretix's Stripe plugin.

Add a ``[stripe]`` section for Stripe keys::

    [stripe]
    publishable_key = pk_...
    secret_key = sk_...

VAT tax rules
^^^^^^^^^^^^^

Optional. Leave unset to skip VAT rule creation::

    [pretix_autoconfig]
    # Comma-separated name:rate pairs. Example:
    vat_rates = Standard (20%):20.00,Reduced (5%):5.00

Terms & conditions
^^^^^^^^^^^^^^^^^^

Optional. Leave unset to skip adding the T&C checkbox::

    [pretix_autoconfig]
    terms_url = https://example.com/terms/
    terms_name = Our terms and conditions

Branding
^^^^^^^^

Optional. Sets the organizer's primary colour on creation::

    [pretix_autoconfig]
    primary_color = #3a86ff

Sets the platform URL used in private-link landing pages and as the redirect target for a bare
visit to ``/``::

    [pretix_autoconfig]
    platform_url = https://example.com

Replaces pretix's favicon (a URL the browser can fetch; unset leaves pretix's favicon alone)::

    [pretix_autoconfig]
    favicon_url = https://example.com/favicon.ico

PDF tickets
^^^^^^^^^^^

Optional. Unset leaves pretix's ticket layouts untouched::

    [pretix_autoconfig]
    # Filesystem path of a PDF used as the ticket background
    ticket_background = /path/to/ticket-background.pdf
    # Remove pretix's "powered by" element from the ticket PDF (default: false)
    strip_pretix_logo = false

Removing the pretix logo from tickets is off by default and left to the installer's judgement; the
attribution requirement in pretix's license applies to generated web pages, not downloaded PDFs.

Guest item
^^^^^^^^^^

Internal name of the hidden "Guest" item created on each new event (default: ``guest``). Set it
only if your installation already uses a different identifier for this item::

    [pretix_autoconfig]
    guest_item_internal_name = guest

Checkout questions
^^^^^^^^^^^^^^^^^^

Questions whose identifier starts with one of these prefixes are not repeated in the cart summary
(``autoconfig_`` is always included)::

    [pretix_autoconfig]
    hidden_question_prefixes = myplatform_

Organizer provisioning
^^^^^^^^^^^^^^^^^^^^^^

::

    [pretix_autoconfig]
    # Shared secret for the provision-organizer API endpoint
    provision_secret = REPLACE_WITH_SECRET
    # Name for the API token created when provisioning (default: Admin)
    token_name = Admin

"Powered by" footer
^^^^^^^^^^^^^^^^^^^

Configure in Pretix admin → Global settings → License check → "powered by" fields.
When both name and URL are set, the webshop footer and email footer render a
"Powered by <name> based on pretix" link.

Environment variables reference
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

================================================  =================================================
Environment variable                              Description
================================================  =================================================
``PRETIX_AUTOCONFIG_FEE_PERCENT``                 Percentage fee per ticket (decimal)
``PRETIX_AUTOCONFIG_FEE_MIN``                     Minimum service fee floor (decimal)
``PRETIX_AUTOCONFIG_STRIPE_METHODS``              Comma-separated Stripe methods to enable
``PRETIX_AUTOCONFIG_DISABLED_PROVIDERS``          Comma-separated payment providers to disable
``PRETIX_AUTOCONFIG_STRIPE_PUBLISHABLE_KEY``      Stripe publishable key
``PRETIX_AUTOCONFIG_STRIPE_SECRET_KEY``           Stripe secret key
``PRETIX_AUTOCONFIG_VAT_RATES``                   Comma-separated ``name:rate`` pairs
``PRETIX_AUTOCONFIG_TERMS_URL``                   URL for the T&C checkbox on checkout confirm
``PRETIX_AUTOCONFIG_TERMS_NAME``                  Display name for the T&C checkbox
``PRETIX_AUTOCONFIG_PRIMARY_COLOR``               Organizer primary colour (hex)
``PRETIX_AUTOCONFIG_PLATFORM_URL``                Platform public URL
``PRETIX_AUTOCONFIG_FAVICON_URL``                 Favicon URL replacing pretix's
``PRETIX_AUTOCONFIG_TICKET_BACKGROUND``           Path of the ticket background PDF
``PRETIX_AUTOCONFIG_STRIP_PRETIX_LOGO``           Remove pretix's logo from ticket PDFs (bool)
``PRETIX_AUTOCONFIG_HIDDEN_QUESTION_PREFIXES``    Comma-separated question identifier prefixes
``PRETIX_AUTOCONFIG_GUEST_ITEM_INTERNAL_NAME``    Internal name of the hidden Guest item
``PRETIX_AUTOCONFIG_PROVISION_SECRET``            Shared secret for provisioning API
``PRETIX_AUTOCONFIG_TOKEN_NAME``                  Name for the provisioned API token
================================================  =================================================

Template overrides
------------------

This plugin overrides several pretix presale templates (``pretixpresale/event/``) to customise
checkout layout and UX. When upgrading pretix, compare overridden templates against upstream and
port any changes to provider loops, alerts, and form fields.

The most significant override is ``checkout_payment.html``, which provides a two-column layout
(payment methods left, cart summary right).

Development
-----------

1. Set up a `pretix development environment`_.
2. Clone this repository and activate the pretix virtualenv.
3. Install the plugin: ``pip install -e .``
4. Install pre-commit hooks: ``pre-commit install``
5. Restart pretix.

To check code style::

    ruff check .
    ruff format --check .

To run tests::

    pytest

License
-------

Copyright 2026 Jeronimo Calace Montu

Licensed under the GNU Affero General Public License version 3.0 with additional terms (modelled on
pretix's own) — see the `LICENSE`_ file for details.

.. _pretix: https://github.com/pretix/pretix
.. _pretix development environment: https://docs.pretix.eu/en/latest/development/setup.html
.. _LICENSE: LICENSE
