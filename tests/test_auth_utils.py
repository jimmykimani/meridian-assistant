from __future__ import annotations

from auth_utils import mask_email


def test_mask_email() -> None:
    assert mask_email("alex@meridian.example") == "a•••x@meridian.example"
    assert mask_email("a@b.co") == "*@b.co"
    assert mask_email(None) is None
