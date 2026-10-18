"""Demo data (spec 05 §4): 1 admin, 2 analysts, 20 customers with trusted contacts,
50 payees (5 reported) and 5 rules.

Run from the repo root after `cd backend && alembic upgrade head`:
    python scripts/seed.py

Safe to re-run: rows that already exist (same email, VPA or rule name) are left alone. New
passwords are printed once and appended to scripts/seed_credentials.local.txt (git-ignored).
"""

import asyncio
import random
import secrets
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.security import BCRYPT_ROUNDS, hash_password  # noqa: E402
from app.models import Payee, Report, Rule, TrustedContact, User  # noqa: E402
from app.models.enums import Language, Role, RuleAction  # noqa: E402

CREDENTIALS_FILE = ROOT / "scripts" / "seed_credentials.local.txt"
DOMAIN = "fraudshield.local"

FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Arjun", "Kavya", "Rahul",
         "Meera", "Siddharth", "Pooja", "Karan", "Neha", "Aditya", "Isha", "Manoj", "Lakshmi",
         "Suresh", "Divya"]  # fmt: skip
LAST = ["Sharma", "Patil", "Iyer", "Deshmukh", "Reddy", "Kulkarni", "Gupta", "Nair", "Joshi",
        "Verma"]  # fmt: skip
CITIES = [("Mumbai", 1), ("Delhi", 1), ("Bengaluru", 1), ("Pune", 2), ("Jaipur", 2),
          ("Nagpur", 2), ("Nashik", 3), ("Kolhapur", 3)]  # fmt: skip
AGE_BANDS = ["18-25", "26-35", "36-45", "46-60", "60+"]
SHOPS = ["Sharma Kirana", "Annapurna Mess", "City Medicals", "Green Grocers", "Raj Electricals",
         "Sai Tailors", "Fresh Dairy", "Metro Fuel", "Book Corner", "Ganesh Sweets"]  # fmt: skip
# The 5 reported payees and their scam types (spec 01: the scam scenarios we simulate).
REPORTED = [
    ("cbi.verification@okaxis", "Digital arrest", "digital_arrest", 3),
    ("refund.helpdesk@ybl", "Refund Support", "fake_support_refund", 2),
    ("earn.daily.tasks@paytm", "Task Rewards", "investment_task", 4),
    ("kyc.update.desk@okicici", "KYC Update", "fake_support_refund", 1),
    ("stock.tips.vip@upi", "VIP Stock Tips", "investment_task", 2),
]
RULES: list[tuple[str, dict[str, Any], RuleAction, int]] = [
    ("Large payment to a new payee",
     {"all": [{"field": "amount", "op": ">", "value": 50000},
              {"field": "payee_is_new", "op": "==", "value": True}]},
     RuleAction.HOLD, 10),
    ("Screen sharing while paying",
     {"all": [{"field": "ctx_screen_share", "op": "==", "value": True},
              {"field": "amount", "op": ">", "value": 2000}]},
     RuleAction.HOLD, 20),
    ("Payee reported by several customers",
     {"all": [{"field": "payee_report_count", "op": ">=", "value": 3}]},
     RuleAction.HOLD, 30),
    ("Unknown caller and a new payee",
     {"all": [{"field": "ctx_unknown_call", "op": "==", "value": True},
              {"field": "payee_is_new", "op": "==", "value": True}]},
     RuleAction.STEP_UP, 40),
    ("Night payment far above usual",
     {"all": [{"field": "is_night", "op": "==", "value": True},
              {"field": "amount_ratio_avg", "op": ">", "value": 5}]},
     RuleAction.STEP_UP, 50),
]  # fmt: skip


@dataclass
class SeedResult:
    created: dict[str, int] = field(default_factory=dict)
    credentials: list[tuple[str, str, str]] = field(default_factory=list)  # role, email, password


def _phone(rng: random.Random) -> str:
    return f"9{rng.randint(100_000_000, 999_999_999)}"


async def _user(
    session: AsyncSession, result: SeedResult, rounds: int, email: str, **values: Any
) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing
    password = secrets.token_urlsafe(9)
    user = User(email=email, password_hash=hash_password(password, rounds), **values)
    session.add(user)
    result.credentials.append((values["role"].value, email, password))
    result.created["users"] = result.created.get("users", 0) + 1
    return user


async def seed(session: AsyncSession, rounds: int = BCRYPT_ROUNDS) -> SeedResult:
    """Insert whatever demo rows are missing. Same seed, same names, cities and VPAs."""
    rng = random.Random(42)
    result = SeedResult()

    await _user(
        session,
        result,
        rounds,
        f"admin@{DOMAIN}",
        name="Asha Admin",
        phone=_phone(rng),
        role=Role.ADMIN,
    )
    for i in (1, 2):
        await _user(
            session,
            result,
            rounds,
            f"analyst{i}@{DOMAIN}",
            name=f"Analyst {i}",
            phone=_phone(rng),
            role=Role.ANALYST,
        )

    customers: list[User] = []
    for i in range(20):
        city, tier = rng.choice(CITIES)
        customers.append(
            await _user(
                session,
                result,
                rounds,
                f"customer{i + 1:02d}@{DOMAIN}",
                external_id=f"C{i + 1:03d}",
                name=f"{FIRST[i]} {rng.choice(LAST)}",
                phone=_phone(rng),
                role=Role.CUSTOMER,
                language=rng.choice([Language.EN, Language.EN, Language.HI, Language.MR]),
                city=city,
                city_tier=tier,
                age_band=rng.choice(AGE_BANDS),
                kyc_level=rng.choice([1, 2, 2, 3]),
                avg_txn_amount=Decimal(rng.randrange(300, 8000)),
            )
        )
    await session.flush()  # customer ids for the foreign keys below

    # Each customer's trusted contact is the next customer (a family member in the demo).
    for i, user in enumerate(customers):
        contact = customers[(i + 1) % len(customers)]
        exists = await session.scalar(
            select(TrustedContact).where(
                TrustedContact.user_id == user.id, TrustedContact.contact_user_id == contact.id
            )
        )
        if exists is None:
            session.add(
                TrustedContact(
                    user_id=user.id, contact_user_id=contact.id, approval_limit=Decimal(10_000)
                )
            )
            result.created["trusted_contacts"] = result.created.get("trusted_contacts", 0) + 1

    payees: list[tuple[str, str, int, int]] = []  # vpa, name, age days, report count
    for i in range(45):
        shop = SHOPS[i % len(SHOPS)]
        vpa = f"{shop.lower().replace(' ', '.')}.{i + 1}@okhdfc"
        payees.append((vpa, f"{shop} {i // len(SHOPS) + 1}", rng.randint(200, 3000), 0))
    payees += [(vpa, name, rng.randint(3, 40), n) for vpa, name, _, n in REPORTED]
    scam_type = {vpa: kind for vpa, _, kind, _ in REPORTED}

    reporters = iter(rng.sample(customers, len(customers)) * 2)
    for vpa, name, age, reports in payees:
        if await session.scalar(select(Payee).where(Payee.vpa_or_account == vpa)) is not None:
            continue
        payee = Payee(
            vpa_or_account=vpa, display_name=name, account_age_days=age, report_count=reports
        )
        session.add(payee)
        await session.flush()
        result.created["payees"] = result.created.get("payees", 0) + 1
        for _ in range(reports):
            session.add(
                Report(
                    reporter_id=next(reporters).id,
                    payee_id=payee.id,
                    scam_type=scam_type[vpa],
                    description="Caller pressured me to pay this UPI ID.",
                )
            )
            result.created["reports"] = result.created.get("reports", 0) + 1

    admin = await session.scalar(select(User).where(User.email == f"admin@{DOMAIN}"))
    for name, condition, action, priority in RULES:
        if await session.scalar(select(Rule).where(Rule.name == name)) is None:
            session.add(
                Rule(
                    name=name,
                    condition_json=condition,
                    action=action,
                    priority=priority,
                    updated_by=admin.id if admin else None,
                )
            )
            result.created["rules"] = result.created.get("rules", 0) + 1

    await session.commit()
    return result


async def row_counts(session: AsyncSession) -> dict[str, int]:
    tables = {"users": User, "trusted_contacts": TrustedContact, "payees": Payee,
              "reports": Report, "rules": Rule}  # fmt: skip
    return {
        name: int(await session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in tables.items()
    }


async def main() -> int:
    engine = make_engine(get_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            result = await seed(session)
            counts = await row_counts(session)
    finally:
        await engine.dispose()

    if result.credentials:
        lines = [f"{role:<9} {email:<32} {pw}" for role, email, pw in result.credentials]
        with CREDENTIALS_FILE.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("New logins (shown once, also saved to scripts/seed_credentials.local.txt):")
        print("\n".join(lines))
    print(f"\nCreated: {result.created or 'nothing, already seeded'}")
    print("Row counts:", ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
