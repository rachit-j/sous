"""V2.3 ownership, persistence and observation contracts for cooking tools.

Only synthetic recipe data and provider outputs are used. No AWS, network or media devices.
"""

import copy
import json
import os
import sqlite3
import sys
from contextlib import closing
from unittest import mock

import providers
from jinja2 import DictLoader

from app import create_app
from tests.test_accounts import AccountTestCase, ALICE_PW, BOB_PW


TOOLS_RECIPE = {
    "id": "tools-fixture",
    "name": "Simple pantry bowl",
    "description": "A small synthetic recipe for cooking-tool tests.",
    "ingredients": ["1 cup oats", "1 cup water"],
    "equipment": ["small bowl", "spoon"],
    "steps": [
        {"title": "Combine", "instruction": "Put the ingredients in the bowl.",
         "visual_cue": "The ingredients are in the bowl."},
        {"title": "Stir", "instruction": "Stir until combined.",
         "visual_cue": "The mixture looks even."},
    ],
}


class CookingToolsTests(AccountTestCase):
    def make_app(self):
        app = create_app({"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-secret",
                          "CLOCK": lambda: self.clock})
        app.jinja_env.loader = DictLoader({"index.html": "<meta name='csrf-token' content='{{ csrf_token }}'>"})
        app.extensions["sous_recipes"][TOOLS_RECIPE["id"]] = copy.deepcopy(TOOLS_RECIPE)

        async def deny_voice(connection):
            self.voice_opens.append(connection)
            raise AssertionError("voice upstream must not open in cooking-tool tests")

        app.extensions["sous_voice"]["open_stream"] = deny_voice
        return app

    def start_tools_session(self, cook, **fields):
        return self.start(cook, recipe_id=TOOLS_RECIPE["id"], **fields)

    def add_timer(self, cook, session_id, name, duration_seconds):
        response = cook.send("POST", f"/api/sessions/{session_id}/timers",
                             {"name": name, "duration_seconds": duration_seconds})
        self.assertEqual(response.status_code, 201, response.get_json())
        timers = response.get_json()["timers"]
        return next(item for item in timers if item["name"] == name)

    def observe_as(self, cook, session, output):
        with mock.patch.object(sys.modules["providers"], "analyze", return_value=output):
            return self.observe(cook, session)

    @staticmethod
    def base_observation(**changes):
        return {"activity": "mixing", "evidence": "a spoon is in the bowl", "confidence": 0.99,
                "step_complete": True, "alert": None, "safety_alert": None, "view_obstructed": False,
                **changes}

    def test_named_timers_have_independent_absolute_deadlines_survive_steps_and_restart(self):
        alice = self.setup_admin()
        session = self.start_tools_session(alice)
        sid = session["id"]
        first = self.add_timer(alice, sid, "Oats", 60)
        second = self.add_timer(alice, sid, "Water", 180)
        self.assertEqual((first["deadline_at"], second["deadline_at"]),
                         (self.clock + 60, self.clock + 180))
        self.act(alice, sid, "next")
        self.clock += 61
        timers = alice.get(f"/api/sessions/{sid}/timers").get_json()["timers"]
        self.assertEqual({item["id"]: item["status"] for item in timers},
                         {first["id"]: "elapsed", second["id"]: "running"})

        self.app = self.make_app()  # simulated server restart; deadlines remain absolute
        restored = self.login("alice", ALICE_PW)
        persisted = restored.get(f"/api/sessions/{sid}/timers").get_json()["timers"]
        self.assertEqual({item["id"]: item["deadline_at"] for item in persisted},
                         {first["id"]: first["deadline_at"], second["id"]: second["deadline_at"]})
        self.assertEqual({item["id"]: item["status"] for item in persisted},
                         {first["id"]: "elapsed", second["id"]: "running"})

    def test_named_timer_ack_and_cancel_are_idempotent_and_independent(self):
        alice = self.setup_admin()
        session = self.start_tools_session(alice)
        sid = session["id"]
        acknowledged = self.add_timer(alice, sid, "Rest", 45)
        cancelled = self.add_timer(alice, sid, "Tea", 90)
        ack_path = f"/api/sessions/{sid}/timers/{acknowledged['id']}/ack"
        cancel_path = f"/api/sessions/{sid}/timers/{cancelled['id']}/cancel"
        self.assertEqual(alice.send("POST", ack_path, {}).status_code, 200)
        self.assertEqual(alice.send("POST", ack_path, {}).status_code, 200)
        self.assertEqual(alice.send("POST", cancel_path, {}).status_code, 200)
        self.assertEqual(alice.send("POST", cancel_path, {}).status_code, 200)
        timers = alice.get(f"/api/sessions/{sid}/timers").get_json()["timers"]
        self.assertEqual({item["id"]: item["status"] for item in timers},
                         {acknowledged["id"]: "acknowledged", cancelled["id"]: "cancelled"})

    def test_named_timer_bounds_and_live_timer_cap(self):
        alice = self.setup_admin()
        session = self.start_tools_session(alice)
        path = f"/api/sessions/{session['id']}/timers"
        for body in ({"name": "", "duration_seconds": 1}, {"name": "x" * 81, "duration_seconds": 1},
                     {"name": 2, "duration_seconds": 1}, {"name": "bad", "duration_seconds": 0},
                     {"name": "bad", "duration_seconds": 86401}, {"name": "bad", "duration_seconds": True},
                     {"name": "bad", "duration_seconds": 1.5}, {"name": "bad", "duration_seconds": "60"}):
            self.assertEqual(alice.send("POST", path, body).status_code, 400, body)
        for index in range(8):
            self.add_timer(alice, session["id"], f"Timer {index}", index + 1)
        overflow = alice.send("POST", path, {"name": "Ninth", "duration_seconds": 30})
        self.assertEqual(overflow.status_code, 409, overflow.get_json())

    def test_timer_routes_are_owned_by_the_parent_session(self):
        alice, bob = self.pair()
        session = self.start_tools_session(alice)
        timer = self.add_timer(alice, session["id"], "Private", 30)
        bob_session = self.start_tools_session(bob)
        self.assertEqual(bob.get(f"/api/sessions/{session['id']}/timers").status_code, 404)
        self.assertEqual(bob.send("POST", f"/api/sessions/{session['id']}/timers",
                                  {"name": "forged", "duration_seconds": 10}).status_code, 404)
        for action in ("ack", "cancel"):
            self.assertEqual(bob.send("POST", f"/api/sessions/{session['id']}/timers/{timer['id']}/{action}",
                                      {}).status_code, 404)
        self.assertEqual(bob.get(f"/api/sessions/{bob_session['id']}/timers").get_json()["timers"], [])

    def test_end_cancels_timers_and_late_old_ack_does_not_touch_new_session(self):
        alice = self.setup_admin()
        old_session = self.start_tools_session(alice)
        old_timer = self.add_timer(alice, old_session["id"], "Old", 300)
        self.act(alice, old_session["id"], "end")
        old_timers = alice.get(f"/api/sessions/{old_session['id']}/timers").get_json()["timers"]
        self.assertEqual(old_timers[0]["status"], "cancelled")
        new_session = self.start_tools_session(alice)
        new_timer = self.add_timer(alice, new_session["id"], "New", 300)
        late = alice.send("POST", f"/api/sessions/{old_session['id']}/timers/{old_timer['id']}/ack", {})
        self.assertEqual(late.status_code, 200)
        current = alice.get(f"/api/sessions/{new_session['id']}/timers").get_json()["timers"]
        self.assertEqual([(item["id"], item["status"]) for item in current], [(new_timer["id"], "running")])

    def test_history_lists_only_finished_sessions_and_honors_limit(self):
        alice = self.setup_admin()
        active = self.start_tools_session(alice)
        self.act(alice, active["id"], "end")
        finished = self.start_tools_session(alice)
        self.act(alice, finished["id"], "next")
        self.act(alice, finished["id"], "finish")
        another = self.start_tools_session(alice)
        self.assertEqual(alice.get("/api/history?limit=1").status_code, 200)
        result = alice.get("/api/history?limit=100").get_json()["sessions"]
        self.assertEqual({item["id"] for item in result}, {active["id"], finished["id"]})
        self.assertNotIn(another["id"], {item["id"] for item in result})
        self.assertLessEqual(len(alice.get("/api/history?limit=1000").get_json()["sessions"]), 100)

    def test_history_details_and_private_notes_are_bounded_and_owned(self):
        alice, bob = self.pair()
        session = self.start_tools_session(alice)
        self.assertEqual(alice.get(f"/api/history/{session['id']}").status_code, 200)  # active detail is allowed
        self.act(alice, session["id"], "end")
        path = f"/api/history/{session['id']}/note"
        note = "private note " * 300
        note = note[:4000]
        saved = alice.send("PUT", path, {"note": note})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["session"]["note"], note)
        too_long = alice.send("PUT", path, {"note": "x" * 4001})
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(alice.get(f"/api/history/{session['id']}").get_json()["session"]["note"], note)
        self.assertEqual(bob.get(f"/api/history/{session['id']}").status_code, 404)
        self.assertEqual(bob.send("PUT", path, {"note": "claim"}).status_code, 404)

    def test_checklist_uses_session_snapshot_ingredients_and_equipment(self):
        alice = self.setup_admin()
        session = self.start_tools_session(alice)
        response = alice.get(f"/api/sessions/{session['id']}/checklist")
        self.assertEqual(response.status_code, 200, response.get_json())
        items = response.get_json()["checklist"]
        self.assertEqual({(item["kind"], item["label"]) for item in items},
                         {("ingredient", "1 cup oats"), ("ingredient", "1 cup water"),
                          ("equipment", "small bowl"), ("equipment", "spoon")})
        self.assertTrue(all(item["checked"] is False for item in items))
        self.assertEqual(len({item["id"] for item in items}), len(items))

    def test_checklist_rejects_unknown_duplicate_and_non_boolean_updates_atomically(self):
        alice = self.setup_admin()
        session = self.start_tools_session(alice)
        path = f"/api/sessions/{session['id']}/checklist"
        items = alice.get(path).get_json()["checklist"]
        valid = [{"id": item["id"], "checked": index == 0} for index, item in enumerate(items)]
        self.assertEqual(alice.send("PUT", path, {"items": valid}).status_code, 200)
        baseline = alice.get(path).get_json()["checklist"]
        invalid_requests = (
            {"items": [{"id": "unknown-item", "checked": True}]},
            {"items": [valid[0], valid[0]]},
            {"items": [{"id": valid[0]["id"], "checked": 1}]},
        )
        for body in invalid_requests:
            self.assertEqual(alice.send("PUT", path, body).status_code, 400, body)
            self.assertEqual(alice.get(path).get_json()["checklist"], baseline)

    def test_checklist_routes_require_the_session_owner(self):
        alice, bob = self.pair()
        session = self.start_tools_session(alice)
        path = f"/api/sessions/{session['id']}/checklist"
        self.assertEqual(bob.get(path).status_code, 404)
        self.assertEqual(bob.send("PUT", path, {"items": []}).status_code, 404)

    def test_provider_safety_fields_are_validated_backward_compatible_and_prompted(self):
        old = {"activity": "mixing", "evidence": "bowl", "confidence": 0.4,
               "step_complete": False, "alert": None}
        parsed_old = providers.parse_observation(json.dumps(old))
        self.assertIn("safety_alert", parsed_old)
        self.assertIsNone(parsed_old["safety_alert"])
        self.assertFalse(parsed_old["view_obstructed"])
        safety = {"category": "hazard", "title": "Hot liquid near the edge", "evidence": "Cup at counter edge"}
        new = {**old, "safety_alert": safety, "view_obstructed": True}
        parsed = providers.parse_observation(json.dumps(new))
        self.assertEqual(parsed["safety_alert"], safety)
        self.assertIs(parsed["view_obstructed"], True)
        for invalid in ({"category": "unknown", "title": "x", "evidence": "y"},
                        {"category": "hazard", "title": "x" * 161, "evidence": "y"},
                        {"category": "possible_emergency", "title": "x", "evidence": "y" * 1001}):
            with self.assertRaises(providers.InvalidOutput):
                providers.parse_observation(json.dumps({**old, "safety_alert": invalid,
                                                        "view_obstructed": False}))
        context = {"recipe": TOOLS_RECIPE, "step": TOOLS_RECIPE["steps"][0], "step_index": 0,
                   "is_final_step": False}
        prompt = providers.build_prompt(context).lower()
        self.assertIn("possible_emergency", prompt)
        self.assertIn("hazard", prompt)
        self.assertIn("uncertain", prompt)

    def test_alerts_deduplicate_escalate_and_ack_without_advancing(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        hazard = {"category": "hazard", "title": "Hot liquid nearby", "evidence": "Cup near edge"}
        possible = {"category": "possible_emergency", "title": "Hot liquid nearby",
                    "evidence": "Cup near edge"}
        first = self.observe_as(alice, session, self.base_observation(safety_alert=hazard)).get_json()
        self.assertEqual((first["step_index"], first["completion_streak"]), (0, 0))
        self.clock += 10
        duplicate = self.observe_as(alice, first, self.base_observation(safety_alert=hazard)).get_json()
        self.assertEqual(len(duplicate["alerts"]), 1)
        self.clock += 61
        escalated = self.observe_as(alice, duplicate, self.base_observation(safety_alert=possible)).get_json()
        self.assertEqual({item["category"] for item in escalated["alerts"]}, {"possible_emergency", "hazard"})
        alert_id = next(item["id"] for item in escalated["alerts"] if item["category"] == "possible_emergency")
        ack = alice.send("POST", f"/api/sessions/{session['id']}/alerts/{alert_id}/ack", {})
        self.assertEqual(ack.status_code, 200, ack.get_json())
        matching = next(item for item in ack.get_json()["alerts"] if item["id"] == alert_id)
        self.assertIsNotNone(matching["acknowledged_at"])
        self.assertEqual(ack.get_json()["step_index"], 0)

    def insert_alerts(self, session_id, category, count, acknowledged=None):
        with closing(sqlite3.connect(self.db_path)) as db:
            for index in range(count):
                db.execute("INSERT INTO session_alerts (session_id, category, title, evidence, created_at,"
                           " acknowledged_at) VALUES (?, ?, ?, 'earlier frame', ?, ?)",
                           (session_id, category, f"{category} {index}", self.clock - 1000 + index, acknowledged))
            db.commit()

    def test_possible_emergency_supersedes_oldest_hazard_at_the_active_cap(self):
        alice, bob = self.pair()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        bob_session = self.start_tools_session(bob)
        self.insert_alerts(session["id"], "hazard", 20)
        self.insert_alerts(bob_session["id"], "hazard", 3)
        bob_before = bob.get(f"/api/sessions/{bob_session['id']}").get_json()["alerts"]
        emergency = {"category": "possible_emergency", "title": "Smoke over the stove",
                     "evidence": "Gray haze above the pan; uncertain"}

        full = self.observe_as(alice, session, self.base_observation(
            safety_alert={"category": "hazard", "title": "One more hazard", "evidence": "x"}))
        self.assertEqual(full.status_code, 200)
        self.assertNotIn("One more hazard", [a["title"] for a in full.get_json()["alerts"]])  # hazards stay capped

        result = self.observe_as(alice, full.get_json(), self.base_observation(safety_alert=emergency)).get_json()
        alerts = result["alerts"]
        self.assertEqual(len(alerts), 20)  # cap preserved
        self.assertTrue(all(a["acknowledged_at"] is None for a in alerts))  # nothing was "acknowledged"
        self.assertEqual([a["title"] for a in alerts if a["category"] == "possible_emergency"],
                         ["Smoke over the stove"])
        titles = {a["title"] for a in alerts}
        self.assertNotIn("hazard 0", titles)  # the oldest active hazard was superseded
        self.assertIn("hazard 1", titles)
        self.assertEqual(result["step_index"], 0)
        persisted = alice.get(f"/api/sessions/{session['id']}").get_json()["alerts"]
        self.assertIn("Smoke over the stove", [a["title"] for a in persisted])
        self.assertEqual(bob.get(f"/api/sessions/{bob_session['id']}").get_json()["alerts"], bob_before)

    def test_fresh_emergency_replaces_oldest_emergency_when_only_emergencies_are_active(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        self.insert_alerts(session["id"], "possible_emergency", 20)
        newest = {"category": "possible_emergency", "title": "Newest concern", "evidence": "Fresh frame"}
        alerts = self.observe_as(alice, session, self.base_observation(safety_alert=newest)).get_json()["alerts"]
        self.assertEqual(len(alerts), 20)
        titles = {a["title"] for a in alerts}
        self.assertIn("Newest concern", titles)
        self.assertNotIn("possible_emergency 0", titles)

    def test_alert_records_and_ack_are_isolated_by_session_owner(self):
        alice, bob = self.pair()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        alert = {"category": "hazard", "title": "Review the counter", "evidence": "A cup is near an edge"}
        observed = self.observe_as(alice, session, self.base_observation(safety_alert=alert)).get_json()
        alert_id = observed["alerts"][0]["id"]
        bob_session = self.start_tools_session(bob)
        self.assertEqual(bob.get(f"/api/sessions/{session['id']}").status_code, 404)
        ack = bob.send("POST", f"/api/sessions/{session['id']}/alerts/{alert_id}/ack", {})
        self.assertEqual(ack.status_code, 404)
        self.assertIsNone(next(item for item in alice.get(f"/api/sessions/{session['id']}").get_json()["alerts"]
                               if item["id"] == alert_id)["acknowledged_at"])
        self.assertEqual(bob.get(f"/api/sessions/{bob_session['id']}").get_json()["alerts"], [])

    def test_obscured_view_and_safety_alert_suppress_auto_progression(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        blocked = self.base_observation(view_obstructed=True)
        first = self.observe_as(alice, session, blocked).get_json()
        second = self.observe_as(alice, first, blocked).get_json()
        self.assertEqual((second["step_index"], second["completion_streak"]), (0, 0))
        self.assertEqual(second["monitoring_health"]["state"], "obscured")
        alert = {"category": "hazard", "title": "Check the scene", "evidence": "Unclear hot surface"}
        first = self.observe_as(alice, second, self.base_observation(safety_alert=alert)).get_json()
        second = self.observe_as(alice, first, self.base_observation(safety_alert=alert)).get_json()
        self.assertEqual((second["step_index"], second["completion_streak"]), (0, 0))
        self.assertEqual(len(second["alerts"]), 1)

    def test_stale_or_revoked_observation_cannot_create_an_alert(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        alert_output = self.base_observation(safety_alert={"category": "hazard", "title": "Review",
                                                           "evidence": "Unstable object"})

        def advance_during_analysis(*_args, **_kwargs):
            self.act(alice, session["id"], "next")
            return alert_output

        with mock.patch.object(sys.modules["providers"], "analyze", side_effect=advance_during_analysis):
            stale = self.observe(alice, session)
        self.assertEqual(stale.status_code, 409)
        current = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual(current["alerts"], [])

        self.act(alice, session["id"], "end")
        next_session = self.act(alice, self.start_tools_session(alice)["id"], "resume")

        def revoke_during_analysis(*_args, **_kwargs):
            self.assertEqual(alice.send("POST", "/api/auth/logout", {}).status_code, 200)
            return alert_output

        with mock.patch.object(sys.modules["providers"], "analyze", side_effect=revoke_during_analysis):
            revoked = self.observe(alice, next_session)
        self.assertEqual(revoked.status_code, 401)
        signed_in = self.login("alice", ALICE_PW)
        current = signed_in.get(f"/api/sessions/{next_session['id']}").get_json()
        self.assertEqual(current["alerts"], [])

    def test_monitoring_freshness_tracks_pause_success_error_and_staleness(self):
        alice = self.setup_admin()
        session = self.act(alice, self.start_tools_session(alice)["id"], "resume")
        recent = self.observe_as(alice, session, self.base_observation()).get_json()
        self.assertEqual(recent["monitoring_health"]["state"], "recent")
        self.assertIsNotNone(recent["monitoring_health"]["last_success_at"])
        self.clock += 31
        stale = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual(stale["monitoring_health"]["state"], "stale")
        paused = self.act(alice, session["id"], "pause")
        self.assertEqual(paused["monitoring_health"]["state"], "paused")
        resumed = self.act(alice, session["id"], "resume")
        with mock.patch.object(sys.modules["providers"], "analyze", side_effect=RuntimeError("synthetic failure")):
            failed = self.observe(alice, resumed)
        self.assertEqual(failed.status_code, 502)
        after_error = alice.get(f"/api/sessions/{session['id']}").get_json()
        self.assertEqual(after_error["monitoring_health"]["state"], "error")
        self.assertIsNotNone(after_error["monitoring_health"]["last_attempt_at"])


if __name__ == "__main__":
    import unittest
    unittest.main()
