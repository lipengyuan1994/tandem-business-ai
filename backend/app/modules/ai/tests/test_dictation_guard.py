import pytest

from app.modules.ai.dictation_guard import guard


@pytest.mark.parametrize(
    ("raw", "polished", "warning"),
    [
        ("order ten pads", "Order 11 pads.", "voice.polish_new_number"),
        ("order ten pads", "Order 10 pads for Zelda.", "voice.polish_new_name"),
        ("hi", "Hi hi hi hi hi.", "voice.polish_too_long"),
        ("twelve no ten brake pads", "10 brake pads.", None),
        ("don't send it not ten", "Don't send it, not 10.", None),
        ("twenty five dollars", "$25", None),
        ("five euros", "$5", "voice.polish_new_currency"),
    ],
)
def test_polish_guard(raw, polished, warning):
    assert guard(raw, polished, []) == warning


def test_dictionary_can_correct_names_but_cannot_add_numbers():
    assert guard("order ten shimanno pads", "Order 10 Shimano pads.", ["Shimano"]) is None
    assert guard("order ten pads", "Order 20 pads.", ["20"]) == "voice.polish_new_number"


def test_numeric_sku_cannot_hide_new_digits():
    assert guard("order ten ab pads", "Order AB100 pads.", ["AB100"]) == "voice.polish_new_number"
    assert guard("order ten ab pads", "Order AB-10 pads.", ["AB-10"]) is None


def test_cannot_add_negative_sign():
    assert guard("quantity ten", "Quantity -10.", []) == "voice.polish_new_number"


def test_empty_polish_cannot_drop_nonempty_speech():
    assert guard("order ten pads", "", []) == "voice.polish_empty"


def test_screen_dictionary_terms_survive_recency_cap(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.modules.ai import dictation_dictionary as scoped
    from app.platform.testing.auth import ctx_for

    perms = {
        "catalog.item.read",
        "purchasing.po.read",
        "identity.membership.read",
        "crm.customer.read",
        "conversations.thread.read",
    }
    ids = [uuid4()]
    monkeypatch.setattr(
        scoped.catalog, "get_items", lambda *args: {ids[0]: SimpleNamespace(name="ScreenItem", sku="SCREEN")}
    )
    monkeypatch.setattr(
        scoped.catalog,
        "search_items",
        lambda *args, **kwargs: [SimpleNamespace(name=f"Item{n}", sku=f"SKU{n}") for n in range(50)],
    )
    monkeypatch.setattr(
        scoped.purchasing,
        "dictation_names",
        lambda *args, **kwargs: (
            ["ScreenSupplier"] if kwargs.get("only_selected") else [f"Supplier{n}" for n in range(50)]
        ),
    )
    monkeypatch.setattr(
        scoped.identity,
        "dictation_names",
        lambda *args, **kwargs: ["ScreenStaff"] if kwargs.get("only_selected") else [f"Staff{n}" for n in range(50)],
    )
    monkeypatch.setattr(scoped.crm, "customer_names", lambda *args: {ids[0]: "ScreenCustomer"})
    monkeypatch.setattr(scoped.knowledge, "dictation_names", lambda *args: ["ScreenArticle"])
    terms = scoped.dictionary(None, ctx_for(uuid4(), perms), ids)
    assert len(terms) == 200
    assert {"ScreenItem", "SCREEN", "ScreenSupplier", "ScreenStaff", "ScreenCustomer", "ScreenArticle"} <= set(terms)


def test_separate_spoken_numbers_cannot_be_added_together():
    assert guard("order five ten pads", "Order 15 pads.", []) == "voice.polish_new_number"
    assert guard("order twelve thirteen pads", "Order 25 pads.", []) == "voice.polish_new_number"
    assert guard("order one hundred twenty five pads", "Order 125 pads.", []) is None
