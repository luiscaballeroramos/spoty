import unittest

from app_views.error_feedback import clear_incident, record_incident


class ErrorFeedbackTest(unittest.TestCase):
    def test_repeated_error_groups_under_one_incident(self):
        """Verify Repeated error groups under one incident."""
        state = {}
        first_error = RuntimeError("secret database URL")
        first_error.sqlstate = "08006"
        second_error = RuntimeError("different sensitive detail")
        second_error.sqlstate = "08006"

        first = record_incident(state, "summary", first_error)
        second = record_incident(state, "summary", second_error)

        self.assertIs(first, second)
        self.assertEqual(second["repeat_count"], 2)
        self.assertNotIn("secret database URL", repr(state))

    def test_new_error_category_starts_new_incident_and_recovery_clears_it(self):
        """Verify New error category starts new incident and recovery clears it."""
        state = {}
        record_incident(state, "playback", RuntimeError("first"))
        next_incident = record_incident(state, "playback", ValueError("second"))

        self.assertEqual(next_incident["fingerprint"][0], "ValueError")
        self.assertEqual(next_incident["repeat_count"], 1)
        clear_incident(state, "playback")
        self.assertNotIn("playback", state["ui_error_incidents"])
