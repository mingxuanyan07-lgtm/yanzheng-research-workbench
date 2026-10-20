"""Bounded direction proposal history: cap, prune counter and active pending preservation."""

import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import workflow
from test_workflow import call, directions, project


class ProposalHistoryBoundTest(unittest.TestCase):
    def test_api_history_is_capped_and_never_drops_active_pending(self):
        item = project()
        payload = directions()
        total = workflow.PROPOSAL_HISTORY_LIMIT + 5
        for index in range(total):
            workflow.store_proposal(item, payload, call(f"call-{index}"))
        flow = item["workflow"]
        history = flow["proposals"]
        self.assertEqual(len(history), workflow.PROPOSAL_HISTORY_LIMIT)
        self.assertEqual(flow["proposals_pruned_count"], total - workflow.PROPOSAL_HISTORY_LIMIT)
        self.assertEqual(len({entry["id"] for entry in history}), workflow.PROPOSAL_HISTORY_LIMIT)
        pending = flow["pending_proposal"]
        self.assertIn(pending["id"], {entry["id"] for entry in history})
        self.assertEqual(history[-1]["id"], pending["id"])
        self.assertEqual(history[-1]["status"], "pending")
        # The active pending preview remains acceptable after pruning.
        workflow.accept_proposal(item, pending["id"])
        self.assertEqual(item["direction_map"]["stage"], "directions_ready")
        self.assertEqual(item["direction_versions"][-1]["proposal_id"], pending["id"])

    def test_prune_fallback_keeps_pending_even_if_it_is_not_in_the_tail(self):
        flow = {"pending_proposal": {"id": "active"}}
        flow["proposals"] = [{"id": f"p{index}", "status": "rejected"} for index in range(52)]
        flow["proposals"][0] = {"id": "active", "status": "pending"}
        pruned = workflow._prune_proposal_history(flow)
        self.assertEqual(pruned, 2)
        self.assertEqual(len(flow["proposals"]), workflow.PROPOSAL_HISTORY_LIMIT)
        self.assertIn("active", {entry["id"] for entry in flow["proposals"]})
        self.assertEqual(flow["proposals_pruned_count"], 2)


if __name__ == "__main__":
    unittest.main()
