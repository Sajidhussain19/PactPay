"""Draft a contract with the real LLM and print it.

cd backend
python -m scripts.draft_contract --budget 150 "Landing page for my bakery: ..."
python -m scripts.draft_contract --budget 150 --mockup demo/mockup.png "..."
"""

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from app.agents.contract_agent import ContractAgent
from app.agents.contract_schema import ContractRequest
from app.core.config import get_settings
from app.core.llm import build_llm
from app.core.logging import configure_logging

DEFAULT_JOB = (
    "Landing page for my bakery: menu, contact form that emails me, works on mobile, "
    "loads fast, and should look nice and modern."
)


async def run(job: str, budget: Decimal, mockup: bytes | None) -> None:
    llm = build_llm(get_settings())
    if llm is None:
        sys.exit("Set OPENAI_API_KEY in backend/.env")
    contract = await ContractAgent(llm).run(
        ContractRequest(job_description=job, budget=budget), mockup
    )

    print(f"\n{contract.title}  ({contract.total} {contract.currency})\n{contract.summary}")
    for m in contract.milestones:
        print(f"\n[{m.id}] {m.title}: {m.amount} {contract.currency}, due {m.due_date}")
        for c in m.criteria:
            tag = "auto" if c.is_objective else "HUMAN"
            params = c.parameters.model_dump(exclude_none=True)
            print(f"  {c.id} [{tag}] {c.check_type}: {c.description}\n        {params}")
    for warning in contract.warnings:
        print(f"\n! {warning}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", nargs="?", default=DEFAULT_JOB)
    parser.add_argument("--budget", type=Decimal, default=Decimal("150"))
    parser.add_argument("--mockup", type=Path)
    args = parser.parse_args()
    configure_logging("WARNING")
    mockup = args.mockup.read_bytes() if args.mockup else None
    asyncio.run(run(args.job, args.budget, mockup))


if __name__ == "__main__":
    main()
