"""Guards for live_cart.js: quantity changes run one at a time against a fresh cart.

Two quick "+" clicks on a slow server used to add three or four tickets (or only one): a
new click made the page stop waiting for the previous cart task, which still finished on
the server, and the next click computed its difference from a cart that was still
changing. Reproduced locally with every request delayed 1.2 s. There is no JS test runner
here, so these assert on the source; the behaviour is covered by that browser check.
"""

import re
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parent.parent / "pretix_autoconfig" / "static" / "pretix_autoconfig" / "live_cart.js"
).read_text()


def _function_body(name):
    start = SOURCE.index(f"function {name}(")
    depth = 0
    for i in range(SOURCE.index("{", start), len(SOURCE)):
        depth += {"{": 1, "}": -1}.get(SOURCE[i], 0)
        if depth == 0:
            return SOURCE[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def test_poll_never_gives_up_on_a_running_cart_task_for_a_newer_click():
    body = _function_body("poll")
    assert "changeGen" not in body


def test_each_change_waits_for_the_previous_one():
    body = _function_body("handleChange")
    assert re.search(r"pending = pending\s*\.catch", body)


def test_each_change_diffs_against_a_fresh_cart_read():
    body = _function_body("handleChange")
    assert body.index("fetchCartState()") < body.index("applyDesired(gen, cap)")


def test_a_superseded_change_is_skipped_not_applied():
    body = _function_body("handleChange")
    assert "if (gen !== changeGen) return;" in body
