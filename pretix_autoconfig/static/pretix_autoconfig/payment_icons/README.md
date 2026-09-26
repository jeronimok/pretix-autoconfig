# Payment method branding

## Bundled

- **`card.svg`** — generic card illustration for `stripe` (card payments) and `mollie` when used as the card method; not an issuer or scheme logo.

- **`ideal.svg`** — official **iDEAL | Wero** lock-up (light backgrounds). Shown for pretix providers `stripe_ideal` and `mollie_ideal` via `checkout_payment.css`. Follow [iDEAL | Wero branding](https://ideal.nl/en/ideal-wero-branding) for usage rules and updates.

- **`klarna.svg`** — Klarna mark for `stripe_klarna`, `mollie_klarna`, `mollie_klarnapaylater`, and `mollie_klarnasliceit`. Use in line with Klarna’s brand guidelines.

## Optional

Add other vendor logos here and reference them from your own CSS using `[data-provider="…"]` on payment cards.
