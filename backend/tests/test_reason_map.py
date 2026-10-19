"""reason_map: a real phrase for every spec 03 §2 feature, in en, hi and mr."""

import math

import pytest

from app.models.enums import Language
from app.services.reason_map import COVERED, indian, phrase
from ml.features.schema import DTYPES, FEATURE_NAMES

SAMPLE = {"bool": [True, False], "category": ["P2P", "RENT"], "int64": [3], "float64": [2.5]}


def test_every_feature_has_a_phrase() -> None:
    assert set(FEATURE_NAMES) == COVERED


@pytest.mark.parametrize("feature", FEATURE_NAMES)
def test_phrases_exist_in_every_language(feature: str) -> None:
    for value in SAMPLE[DTYPES[feature]]:
        texts = {lang: phrase(feature, value, lang) for lang in Language}
        for text in texts.values():
            assert text and text != f"{feature.replace('_', ' ')}: {value}"  # not the fallback
        assert texts[Language.HI] != texts[Language.EN] != texts[Language.MR]


def test_examples() -> None:
    assert phrase("payee_report_count", 37, Language.EN) == "Payee reported by 37 users"
    assert phrase("amount_ratio_avg", 6.04, Language.EN) == "Amount is 6.0× your usual"
    assert phrase("amount_log", math.log1p(48_500), Language.EN) == "Amount ₹48,500"
    assert phrase("ctx_unknown_call", True, Language.HI) == "आप किसी अनजान नंबर से कॉल पर हैं"
    assert phrase("payee_is_new", False, Language.MR).startswith("तुम्ही याआधी")
    assert phrase("weekday", 6, Language.MR) == "रविवार रोजी पेमेंट"
    assert phrase("merchant_category", "ECOMMERCE", Language.EN) == "Payment type: online shopping"
    assert phrase("community_fraud_ratio", 0.4, Language.EN).startswith("40% of")


def test_indian_number_grouping() -> None:
    assert [indian(n) for n in (0, 999, 1000, 125000, 12345678, -48500)] == [
        "0", "999", "1,000", "1,25,000", "1,23,45,678", "-48,500"
    ]  # fmt: skip


def test_unknown_feature_or_bad_value_never_raises() -> None:
    assert phrase("brand_new_feature", 1, Language.EN) == "brand new feature: 1"
    assert phrase("amount", "n/a", Language.HI) == "amount: n/a"
