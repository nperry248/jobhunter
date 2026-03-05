"""
scripts/test_orchestrator.py — Direct backend test for the Orchestrator agent.

Bypasses FastAPI / HTTP entirely. Calls orchestrator.run() directly,
prints every step and any errors, then stops at the approval gate.

Usage (from backend/):
    python -m scripts.test_orchestrator
    python -m scripts.test_orchestrator --approve   # also runs the apply phase
    python -m scripts.test_orchestrator --dry-run   # mock data only
"""

import argparse
import asyncio
import json
import sys

# Add backend/ to sys.path so imports resolve correctly when run as a script
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(dry_run: bool, also_approve: bool) -> None:
    from agents.orchestrator import resume, run
    from core.config import settings

    print(f"\n{'='*60}")
    print(f"  Orchestrator test")
    print(f"  dry_run={dry_run}  also_approve={also_approve}")
    print(f"  model={settings.orchestrator_model}")
    print(f"{'='*60}\n")

    # Override dry_run for this run
    settings.orchestrator_dry_run = dry_run

    goal = "Check the job database, score any unscored jobs, then request approval to apply to all reviewed jobs above 70%."

    print(f"Goal: {goal}\n")
    print("Running orchestrator...\n")

    result = await run(goal=goal)

    print(f"\n{'─'*60}")
    print(f"  Status:      {result.status}")
    print(f"  Session ID:  {result.session_id}")
    print(f"  Token usage: {result.token_usage}")
    print(f"  Steps:       {len(result.steps)}")
    print(f"{'─'*60}\n")

    print("Steps:")
    for i, step in enumerate(result.steps, 1):
        print(f"\n  [{i}] {step['tool']}")
        if step.get("input"):
            print(f"      input:  {json.dumps(step['input'])}")
        if step.get("result"):
            print(f"      result: {json.dumps(step['result'])}")
        if step.get("error"):
            print(f"      ERROR:  {step['error']}")

    if result.errors:
        print(f"\nErrors:")
        for err in result.errors:
            print(f"  ✗ {err}")

    if result.result_summary:
        print(f"\nSummary: {result.result_summary}")

    if result.status == "waiting_for_approval":
        print(f"\n⏸  Approval gate triggered.")
        print(f"   Pending session in DB: {result.session_id}")

        # Show the pending jobs from the last request_apply_approval step
        for step in reversed(result.steps):
            if step.get("tool") == "request_apply_approval" and step.get("result"):
                r = step["result"]
                print(f"\n   Reasoning: {r.get('reasoning', 'N/A')}")
                print(f"\n   Jobs to approve:")
                for jid in r.get("job_ids", []):
                    print(f"     - {jid}")
                break

        if also_approve:
            print(f"\n▶  --approve flag set. Running apply phase now...\n")

            # Load the pending job IDs from the DB
            from core.database import get_db_context
            from models.orchestrator_session import OrchestratorSession

            async with get_db_context() as session:
                db_record = await session.get(OrchestratorSession, result.session_id)
                if not db_record or not db_record.pending_job_ids:
                    print("ERROR: No pending jobs found in DB record.")
                    return

                pending = db_record.pending_job_ids
                # Extract ID strings from dicts (new format) or plain strings
                id_strings = [
                    item["id"] if isinstance(item, dict) else item
                    for item in pending
                ]
                print(f"   Approving {len(id_strings)} job(s): {id_strings}\n")

            resume_result = await resume(
                session_id=result.session_id,
                approved_job_ids=id_strings,
                dry_run=dry_run,
            )

            print(f"\n{'─'*60}")
            print(f"  Resume status: {resume_result.status}")
            print(f"  Token usage:   {resume_result.token_usage}")
            print(f"{'─'*60}")

            # Show only the new steps added during resume
            new_steps = resume_result.steps[len(result.steps):]
            for i, step in enumerate(new_steps, 1):
                print(f"\n  [apply {i}] {step['tool']}")
                if step.get("result"):
                    print(f"      result: {json.dumps(step['result'])}")
                if step.get("error"):
                    print(f"      ERROR:  {step['error']}")

            if resume_result.result_summary:
                print(f"\nSummary: {resume_result.result_summary}")

            if resume_result.errors:
                print(f"\nErrors:")
                for err in resume_result.errors:
                    print(f"  ✗ {err}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the Orchestrator agent directly.")
    parser.add_argument("--dry-run", action="store_true", help="Use mock data, don't run real agents")
    parser.add_argument("--approve", action="store_true", help="Also run the apply phase after approval gate")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, also_approve=args.approve))
