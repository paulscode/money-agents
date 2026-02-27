#!/usr/bin/env python3
"""
Database cleanup script for LLM cost tracking redesign.

Since this application has not yet been rolled out for public use,
this script wipes all historical data that used the legacy dual-source
cost tracking (messages.cost_usd + llm_usage) and resets to a clean
single-source-of-truth design where llm_usage is the ONLY cost table.

What this script does:
  1. Clears all llm_usage records (stale data from partial tracking)
  2. Nullifies cost_usd on all messages (no longer used for cost reporting)
  3. Resets agent_runs cost aggregates
  4. Resets agent_definitions budget counters

What this script does NOT do:
  - Delete conversations or messages (content is preserved)
  - Drop any columns (messages.cost_usd column remains for compatibility)
  - Modify the schema

Usage:
  # From backend container
  python scripts/cleanup_cost_tracking.py

  # Or via docker compose
  docker compose exec backend python scripts/cleanup_cost_tracking.py
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from sqlalchemy import text
    from app.core.database import async_engine

    print("=" * 60)
    print("LLM Cost Tracking Cleanup")
    print("=" * 60)
    print()
    print("This will reset all cost tracking data to start fresh")
    print("with the new single-source-of-truth design (llm_usage).")
    print()

    # Confirm
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    async with async_engine.begin() as conn:
        # 1. Clear llm_usage records
        result = await conn.execute(text("DELETE FROM llm_usage"))
        print(f"  [1/4] Cleared {result.rowcount} llm_usage records")

        # 2. Nullify cost_usd on all messages
        result = await conn.execute(text(
            "UPDATE messages SET cost_usd = NULL WHERE cost_usd IS NOT NULL"
        ))
        print(f"  [2/4] Nullified cost_usd on {result.rowcount} messages")

        # 3. Reset agent_runs cost aggregates
        result = await conn.execute(text(
            "UPDATE agent_runs SET cost_usd = 0, tokens_used = 0 "
            "WHERE cost_usd > 0 OR tokens_used > 0"
        ))
        print(f"  [3/4] Reset cost on {result.rowcount} agent runs")

        # 4. Reset agent_definitions budget counters
        result = await conn.execute(text(
            "UPDATE agent_definitions SET "
            "budget_used = 0, total_cost_usd = 0, total_tokens_used = 0 "
            "WHERE budget_used > 0 OR total_cost_usd > 0 OR total_tokens_used > 0"
        ))
        print(f"  [4/4] Reset budget on {result.rowcount} agent definitions")

    print()
    print("Done! Cost tracking is now clean.")
    print("All future LLM costs will be tracked in the llm_usage table.")


if __name__ == "__main__":
    asyncio.run(main())
