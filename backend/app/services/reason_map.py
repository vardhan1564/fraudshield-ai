"""Plain-language phrase for every spec 03 §2 feature, in en / hi / mr (spec 01 F4).

`phrase(feature, value, language)` turns one SHAP entry into the text the customer and the
analyst see. True/false features have a phrase for each side; numbers are formatted Indian style
(₹48,500, 1,25,000). Hindi and Marathi wording should get a native speaker's review before launch.
"""

import math
from collections.abc import Callable
from typing import Any

from app.models.enums import Language

EN, HI, MR = Language.EN, Language.HI, Language.MR
Text = dict[Language, str]

WEEKDAYS: dict[Language, list[str]] = {
    EN: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    HI: ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    MR: ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
}

CATEGORIES: dict[str, Text] = {
    "P2P": {EN: "person to person", HI: "व्यक्ति से व्यक्ति", MR: "व्यक्ती ते व्यक्ती"},
    "GROCERY": {EN: "groceries", HI: "किराना", MR: "किराणा"},
    "FOOD": {EN: "food", HI: "खाना", MR: "जेवण"},
    "FUEL": {EN: "fuel", HI: "ईंधन", MR: "इंधन"},
    "UTILITIES": {EN: "bills", HI: "बिल", MR: "बिले"},
    "ECOMMERCE": {EN: "online shopping", HI: "ऑनलाइन खरीदारी", MR: "ऑनलाइन खरेदी"},
    "TRAVEL": {EN: "travel", HI: "यात्रा", MR: "प्रवास"},
    "ENTERTAINMENT": {EN: "entertainment", HI: "मनोरंजन", MR: "मनोरंजन"},
    "HEALTH": {EN: "health", HI: "स्वास्थ्य", MR: "आरोग्य"},
    "EDUCATION": {EN: "education", HI: "शिक्षा", MR: "शिक्षण"},
    "RENT": {EN: "rent", HI: "किराया", MR: "भाडे"},
}


def indian(n: float) -> str:
    """1234567 -> '12,34,567' (lakh/crore grouping)."""
    s = str(round(n))
    sign, s = ("-", s[1:]) if s.startswith("-") else ("", s)
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return sign + ",".join([head, *groups, tail])


def rupees(n: float) -> str:
    return "₹" + indian(n)


def num(x: float) -> str:
    """Whole numbers without decimals, the rest with one."""
    return indian(x) if float(x).is_integer() else f"{x:.1f}"


# Numeric features: a template per language, filled by a formatter of the value.
NUMERIC: dict[str, tuple[Callable[[float], dict[str, str]], Text]] = {
    "amount": (
        lambda v: {"x": rupees(v)},
        {EN: "Amount {x}", HI: "राशि {x}", MR: "रक्कम {x}"},
    ),
    "amount_log": (
        lambda v: {"x": rupees(math.expm1(v))},  # amount_log = log1p(amount)
        {EN: "Amount {x}", HI: "राशि {x}", MR: "रक्कम {x}"},
    ),
    "amount_ratio_avg": (
        lambda v: {"x": f"{v:.1f}"},
        {
            EN: "Amount is {x}× your usual",
            HI: "राशि आपकी सामान्य राशि की {x} गुना है",
            MR: "रक्कम तुमच्या नेहमीच्या रकमेच्या {x} पट आहे",
        },
    ),
    "hour": (
        lambda v: {"x": f"{int(v):02d}:00"},
        {EN: "Paid at {x}", HI: "{x} बजे भुगतान", MR: "{x} वाजता पेमेंट"},
    ),
    "txn_count_5m": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} payments in the last 5 minutes",
            HI: "पिछले 5 मिनट में {x} भुगतान",
            MR: "गेल्या 5 मिनिटांत {x} पेमेंट",
        },
    ),
    "txn_count_1h": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} payments in the last hour",
            HI: "पिछले 1 घंटे में {x} भुगतान",
            MR: "गेल्या 1 तासात {x} पेमेंट",
        },
    ),
    "txn_count_24h": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} payments in the last 24 hours",
            HI: "पिछले 24 घंटों में {x} भुगतान",
            MR: "गेल्या 24 तासांत {x} पेमेंट",
        },
    ),
    "amount_sum_1h": (
        lambda v: {"x": rupees(v)},
        {
            EN: "{x} paid in the last hour",
            HI: "पिछले 1 घंटे में {x} का भुगतान",
            MR: "गेल्या 1 तासात {x} दिले",
        },
    ),
    "amount_sum_24h": (
        lambda v: {"x": rupees(v)},
        {
            EN: "{x} paid in the last 24 hours",
            HI: "पिछले 24 घंटों में {x} का भुगतान",
            MR: "गेल्या 24 तासांत {x} दिले",
        },
    ),
    "failed_count_1h": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} failed attempts in the last hour",
            HI: "पिछले 1 घंटे में {x} असफल प्रयास",
            MR: "गेल्या 1 तासात {x} अयशस्वी प्रयत्न",
        },
    ),
    "payee_account_age_days": (
        lambda v: {"x": num(v)},
        {
            EN: "Payee account is {x} days old",
            HI: "प्राप्तकर्ता का खाता {x} दिन पुराना है",
            MR: "प्राप्तकर्त्याचे खाते {x} दिवस जुने आहे",
        },
    ),
    "payee_unique_senders_24h": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} different people paid this payee in the last 24 hours",
            HI: "पिछले 24 घंटों में {x} अलग लोगों ने इस प्राप्तकर्ता को भुगतान किया",
            MR: "गेल्या 24 तासांत {x} वेगवेगळ्या लोकांनी या प्राप्तकर्त्याला पेमेंट केले",
        },
    ),
    "payee_reputation": (  # spec 01 F7: 0 = clean, 1 = worst
        lambda v: {"x": f"{v * 100:.0f}"},
        {
            EN: "Payee risk rating {x}/100",
            HI: "प्राप्तकर्ता की जोखिम रेटिंग {x}/100",
            MR: "प्राप्तकर्त्याचे जोखीम रेटिंग {x}/100",
        },
    ),
    "payee_report_count": (
        lambda v: {"x": num(v)},
        {
            EN: "Payee reported by {x} users",
            HI: "{x} लोगों ने इस प्राप्तकर्ता की शिकायत की है",
            MR: "{x} लोकांनी या प्राप्तकर्त्याची तक्रार केली आहे",
        },
    ),
    "ip_distance_km": (
        lambda v: {"x": num(round(v))},
        {
            EN: "{x} km from your usual location",
            HI: "आपकी सामान्य जगह से {x} किमी दूर",
            MR: "तुमच्या नेहमीच्या ठिकाणापासून {x} किमी दूर",
        },
    ),
    "typing_speed_z": (
        lambda v: {},
        {
            EN: "Typing pattern differs from your usual",
            HI: "टाइपिंग का तरीका आपके सामान्य तरीके से अलग है",
            MR: "टायपिंगची पद्धत तुमच्या नेहमीपेक्षा वेगळी आहे",
        },
    ),
    "session_len_s": (
        lambda v: {"x": num(round(v))},
        {EN: "Session lasted {x} seconds", HI: "सत्र {x} सेकंड चला", MR: "सत्र {x} सेकंद चालले"},
    ),
    "login_to_pay_s": (
        lambda v: {"x": num(round(v))},
        {
            EN: "Paid {x} seconds after logging in",
            HI: "लॉग इन के {x} सेकंड बाद भुगतान",
            MR: "लॉग इन केल्यानंतर {x} सेकंदांत पेमेंट",
        },
    ),
    "coach_red_flags": (
        lambda v: {"x": num(v)},
        {
            EN: "{x} warning signs in your answers",
            HI: "आपके जवाबों में {x} खतरे के संकेत",
            MR: "तुमच्या उत्तरांमध्ये {x} धोक्याची चिन्हे",
        },
    ),
    "graph_score": (
        lambda v: {"x": f"{v * 100:.0f}"},
        {
            EN: "Linked to a suspected fraud network (score {x}/100)",
            HI: "संदिग्ध धोखाधड़ी नेटवर्क से जुड़ाव (स्कोर {x}/100)",
            MR: "संशयित फसवणूक नेटवर्कशी संबंध (स्कोअर {x}/100)",
        },
    ),
    "graph_degree": (
        lambda v: {"x": num(round(v))},
        {
            EN: "Account connected to {x} other accounts",
            HI: "खाता {x} अन्य खातों से जुड़ा है",
            MR: "खाते {x} इतर खात्यांशी जोडलेले आहे",
        },
    ),
    "graph_pagerank": (
        lambda v: {"x": f"{v:.3f}"},
        {
            EN: "Account is a busy hub in the payment network",
            HI: "खाता भुगतान नेटवर्क का व्यस्त केंद्र है",
            MR: "खाते पेमेंट नेटवर्कमधील व्यस्त केंद्र आहे",
        },
    ),
    "hops_to_fraud": (
        lambda v: {"x": num(round(v))},
        {
            EN: "{x} steps away from a known fraud account",
            HI: "ज्ञात धोखाधड़ी खाते से {x} कदम दूर",
            MR: "ज्ञात फसवणूक खात्यापासून {x} पायऱ्या दूर",
        },
    ),
    "community_fraud_ratio": (
        lambda v: {"x": f"{v * 100:.0f}%"},
        {
            EN: "{x} of this account's group is linked to fraud",
            HI: "इस खाते के समूह का {x} धोखाधड़ी से जुड़ा है",
            MR: "या खात्याच्या गटातील {x} फसवणुकीशी जोडलेले आहे",
        },
    ),
}

# True/false features: (phrase when true, phrase when false).
FLAGS: dict[str, tuple[Text, Text]] = {
    "is_night": (
        {EN: "Late-night payment", HI: "देर रात का भुगतान", MR: "रात्री उशिराचे पेमेंट"},
        {EN: "Daytime payment", HI: "दिन का भुगतान", MR: "दिवसाचे पेमेंट"},
    ),
    "channel_upi": (
        {EN: "UPI payment", HI: "UPI भुगतान", MR: "UPI पेमेंट"},
        {EN: "Not a UPI payment", HI: "UPI भुगतान नहीं", MR: "UPI पेमेंट नाही"},
    ),
    "channel_card": (
        {EN: "Card payment", HI: "कार्ड से भुगतान", MR: "कार्डने पेमेंट"},
        {EN: "Not a card payment", HI: "कार्ड से भुगतान नहीं", MR: "कार्डने पेमेंट नाही"},
    ),
    "channel_wallet": (
        {EN: "Wallet payment", HI: "वॉलेट से भुगतान", MR: "वॉलेटने पेमेंट"},
        {EN: "Not a wallet payment", HI: "वॉलेट से भुगतान नहीं", MR: "वॉलेटने पेमेंट नाही"},
    ),
    "payee_is_new": (
        {
            EN: "First payment to this payee",
            HI: "इस प्राप्तकर्ता को पहला भुगतान",
            MR: "या प्राप्तकर्त्याला पहिले पेमेंट",
        },
        {
            EN: "You have paid this payee before",
            HI: "आप पहले भी इस प्राप्तकर्ता को भुगतान कर चुके हैं",
            MR: "तुम्ही याआधी या प्राप्तकर्त्याला पेमेंट केले आहे",
        },
    ),
    "device_is_new": (
        {EN: "New device", HI: "नया डिवाइस", MR: "नवीन डिव्हाइस"},
        {EN: "Your usual device", HI: "आपका सामान्य डिवाइस", MR: "तुमचे नेहमीचे डिव्हाइस"},
    ),
    "device_rooted": (
        {
            EN: "Phone is rooted (weaker security)",
            HI: "फ़ोन रूटेड है (सुरक्षा कमज़ोर)",
            MR: "फोन रूटेड आहे (सुरक्षा कमकुवत)",
        },
        {EN: "Phone is not rooted", HI: "फ़ोन रूटेड नहीं है", MR: "फोन रूटेड नाही"},
    ),
    "is_vpn": (
        {EN: "VPN is on", HI: "VPN चालू है", MR: "VPN चालू आहे"},
        {EN: "VPN is off", HI: "VPN बंद है", MR: "VPN बंद आहे"},
    ),
    "sim_changed_7d": (
        {
            EN: "SIM changed in the last 7 days",
            HI: "पिछले 7 दिनों में SIM बदली गई",
            MR: "गेल्या 7 दिवसांत SIM बदलले",
        },
        {
            EN: "SIM not changed recently",
            HI: "हाल में SIM नहीं बदली",
            MR: "अलीकडे SIM बदलले नाही",
        },
    ),
    "ctx_unknown_call": (
        {
            EN: "You are on a call with an unknown number",
            HI: "आप किसी अनजान नंबर से कॉल पर हैं",
            MR: "तुम्ही अनोळखी नंबरशी कॉलवर आहात",
        },
        {
            EN: "Not on a call with an unknown number",
            HI: "किसी अनजान नंबर से कॉल पर नहीं",
            MR: "अनोळखी नंबरशी कॉलवर नाही",
        },
    ),
    "ctx_screen_share": (
        {
            EN: "Screen sharing or a remote-access app is on",
            HI: "स्क्रीन शेयरिंग या रिमोट-एक्सेस ऐप चालू है",
            MR: "स्क्रीन शेअरिंग किंवा रिमोट-ॲक्सेस ॲप चालू आहे",
        },
        {EN: "Screen sharing is off", HI: "स्क्रीन शेयरिंग बंद है", MR: "स्क्रीन शेअरिंग बंद आहे"},
    ),
    "ctx_call_long": (
        {
            EN: "Long phone call during the payment",
            HI: "भुगतान के दौरान लंबी कॉल",
            MR: "पेमेंट करताना लांब कॉल",
        },
        {EN: "No long call", HI: "कोई लंबी कॉल नहीं", MR: "लांब कॉल नाही"},
    ),
}

WEEKDAY_TEXT: Text = {EN: "Paid on a {x}", HI: "{x} को भुगतान", MR: "{x} रोजी पेमेंट"}
CATEGORY_TEXT: Text = {EN: "Payment type: {x}", HI: "भुगतान का प्रकार: {x}", MR: "पेमेंटचा प्रकार: {x}"}

COVERED = {*NUMERIC, *FLAGS, "weekday", "merchant_category"}


def phrase(feature: str, value: Any, language: Language) -> str:
    """Text for one feature value; unknown features fall back to their name (never raises)."""
    try:
        if feature in FLAGS:
            when_true, when_false = FLAGS[feature]
            return (when_true if bool(value) else when_false)[language]
        if feature == "weekday":
            return WEEKDAY_TEXT[language].format(x=WEEKDAYS[language][int(value) % 7])
        if feature == "merchant_category":
            names = CATEGORIES.get(str(value).upper())
            return CATEGORY_TEXT[language].format(x=names[language] if names else str(value))
        if feature in NUMERIC:
            fmt, texts = NUMERIC[feature]
            return texts[language].format(**fmt(float(value)))
    except (TypeError, ValueError, KeyError, OverflowError):
        pass
    return f"{feature.replace('_', ' ')}: {value}"
