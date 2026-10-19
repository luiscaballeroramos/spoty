import os
import queue
import unittest
from unittest.mock import MagicMock, patch

from services.pulse import PARAMETERS, audio_value, build_pulse
from services.summary_queries import SummaryQueryService


class PulseTest(unittest.TestCase):
    def test_time_decay_and_repeat_listens(self):
        """Keep repeats and halve the weight every thirty real minutes."""
        rows = [{"kind": "history", "played_at": 10000 - age, "audio": {"energy": 0.4}}
                for age in (0, 1800, 3600, 7201)]
        band = build_pulse(rows, 10000)["bands"][0]
        self.assertEqual([sample["weight"] for sample in band["samples"]], [1, 0.5, 0.25])
        self.assertEqual(max(band["heat"]), 1)

    def test_infinite_range_includes_all_past_events_only(self):
        rows = [
            {"kind": "history", "played_at": 10000 - age, "audio": {"energy": 0.4}}
            for age in (0, 86400, 86400 * 365)
        ]
        rows.append({"kind": "history", "played_at": 10001, "audio": {"energy": 0.4}})
        result = build_pulse(rows, 10000, hours=None)
        self.assertEqual(result["history_count"], 3)
        self.assertEqual(len(result["bands"][0]["samples"]), 3)

    def test_current_is_separate_and_missing_is_not_zero(self):
        """Preserve real zeroes without adding current playback to the heat."""
        result = build_pulse([
            {"kind": "current", "audio": {"energy": 0}},
            {"kind": "history", "played_at": 100, "audio": {}},
        ], 100)
        self.assertEqual(result["bands"][0]["current_position"], 0)
        self.assertIsNone(result["bands"][1]["current"])
        self.assertFalse(any(result["bands"][0]["heat"]))
        self.assertEqual(result["history_count"], 1)

    def test_invalid_values_and_tempo_range(self):
        """Reject invalid scalars and center the only known tempo when history is absent."""
        for value in (None, True, "0.5", float("nan"), float("inf"), -1, 1.1):
            self.assertIsNone(audio_value({"energy": value}, PARAMETERS[0]))
        result = build_pulse([{"kind": "current", "audio": {"tempo": 280}}], 100)
        self.assertEqual(result["bands"][-1]["minimum"], 280)
        self.assertEqual(result["bands"][-1]["maximum"], 280)
        self.assertEqual(result["bands"][-1]["current_position"], 0.5)
        self.assertEqual(len(build_pulse([], 100, expanded=True)["bands"]), 9)

    def test_tempo_scale_uses_history_and_handles_identical_values(self):
        """Keep exact historical extrema and constrain outlying current markers to the lane."""
        history = [{"kind": "history", "played_at": 100, "audio": {"tempo": value}}
                   for value in (90, 170)]
        for tempo, expected in ((200, 1), (70, 0), (130, 0.5)):
            band = build_pulse(history + [{"kind": "current", "audio": {"tempo": tempo}}], 100)["bands"][-1]
            self.assertEqual((band["minimum"], band["maximum"]), (90, 170))
            self.assertEqual(band["current_position"], expected)
            self.assertEqual([sample["position"] for sample in band["samples"]], [0, 1])
        band = build_pulse(history[:1] * 2, 100)["bands"][-1]
        self.assertEqual((band["minimum"], band["maximum"]), (90, 90))
        self.assertEqual([sample["position"] for sample in band["samples"]], [0.5, 0.5])

    def test_query_bounds_and_current_occurrence(self):
        """Bound history by time and exclude only the currently playing occurrence."""
        service = SummaryQueryService()
        service._query = MagicMock(return_value=[])
        service.recent_audio_history("track", 10000, 2, 9900)
        query, parameters = service._query.call_args.args
        self.assertEqual(parameters, (2800, 10000, "track", 9900, "track", "track"))
        self.assertIn("LEFT JOIN smarterplaylists_track_metadata", query)
        self.assertIn("UNION ALL", query)

    def test_query_infinite_range_has_no_lower_time_bound(self):
        service = SummaryQueryService()
        service._query = MagicMock(return_value=[])
        service.recent_audio_history("track", 10000, None, 9900)
        query, parameters = service._query.call_args.args
        self.assertEqual(parameters, (10000, "track", 9900, "track", "track"))
        self.assertNotIn("e.played_at >=", query.split("AND NOT", maxsplit=1)[0])
        self.assertIn("e.played_at <= %s", query)

    def test_page_renders_markers_and_expanded_controls(self):
        """Render escaped track names, current markers, windows and all nine optional bands."""
        from streamlit.testing.v1 import AppTest

        rows = [
            {"kind": "current", "audio": {"energy": 0.8, "tempo": 110}},
            {"kind": "history", "played_at": 9900, "name": '<Track "one">',
             "audio": {"energy": 0.3, "tempo": 100}},
            {"kind": "history", "played_at": 10000 - 2 * 3600, "name": "two hours ago",
             "audio": {"energy": 0.4, "tempo": 105}},
            {"kind": "history", "played_at": 10000 - 12 * 3600, "name": "twelve hours ago",
             "audio": {"energy": 0.5, "tempo": 115}},
            {"kind": "history", "played_at": 10000 - 36 * 3600, "name": "thirty-six hours ago",
             "audio": {"energy": 0.6, "tempo": 120}},
        ]
        with patch("app_views.pulse.time.time", return_value=10000), patch(
            "app_views.pulse.has_spotify_auth_available", return_value=False,
        ), patch("app_views.pulse._load_history", return_value=rows) as load:
            app = AppTest.from_string("from app_views.pulse import render_pulse_page\nrender_pulse_page()").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.subheader), 0)
            self.assertEqual(app.segmented_control(key="pulse_hours").proto.label_visibility.value, 2)
            self.assertEqual(load.call_args.args[1], 24)
            self.assertEqual(app.toggle(key="pulse_expanded").label, "All metadata")
            overview = next(element.value for element in app.markdown if 'class="pulse-overview"' in element.value)
            self.assertLess(overview.index('class="pulse-total"'), overview.index('class="pulse-now"'))
            self.assertIn('<div class="pulse-total">3 escuchas</div>', overview)
            self.assertNotIn("escuchas incluidas", overview)
            self.assertNotIn("Últimas", overview)
            self.assertNotIn("pulse-legend", overview)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertEqual(chart.count('class="pulse-row"'), 6)
            self.assertEqual(chart.count('class="pulse-marker"'), 2)
            self.assertIn('&lt;Track &quot;one&quot;&gt;', chart)
            self.assertIn("twelve hours ago", chart)
            self.assertNotIn("thirty-six hours ago", chart)
            app.toggle(key="pulse_expanded").set_value(True).run()
            self.assertFalse(app.exception)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertEqual(chart.count('class="pulse-row"'), 9)
            app.segmented_control(key="pulse_hours").set_value("1h").run()
            self.assertFalse(app.exception)
            self.assertEqual(load.call_args.args[1], 1)
            overview = next(element.value for element in app.markdown if 'class="pulse-overview"' in element.value)
            self.assertIn('<div class="pulse-total">1 escuchas</div>', overview)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertIn("(1/1 100%)", chart)
            self.assertNotIn("two hours ago", chart)
            app.segmented_control(key="pulse_hours").set_value("6h").run()
            self.assertFalse(app.exception)
            self.assertEqual(load.call_args.args[1], 6)
            overview = next(element.value for element in app.markdown if 'class="pulse-overview"' in element.value)
            self.assertIn('<div class="pulse-total">2 escuchas</div>', overview)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertIn("(2/2 100%)", chart)
            self.assertNotIn("twelve hours ago", chart)
            app.segmented_control(key="pulse_hours").set_value("1d").run()
            self.assertFalse(app.exception)
            self.assertEqual(load.call_args.args[1], 24)
            overview = next(element.value for element in app.markdown if 'class="pulse-overview"' in element.value)
            self.assertIn('<div class="pulse-total">3 escuchas</div>', overview)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertIn("(3/3 100%)", chart)
            self.assertIn("twelve hours ago", chart)
            app.segmented_control(key="pulse_hours").set_value("∞").run()
            self.assertFalse(app.exception)
            self.assertIsNone(load.call_args.args[1])
            overview = next(element.value for element in app.markdown if 'class="pulse-overview"' in element.value)
            self.assertIn('<div class="pulse-total">4 escuchas</div>', overview)
            chart = next(element.value for element in app.markdown if 'aria-label="Perfil sonoro reciente"' in element.value)
            self.assertIn("(4/4 100%)", chart)
            self.assertIn("thirty-six hours ago", chart)

    def test_page_does_not_show_notice_when_current_track_has_no_audio_data(self):
        """Keep the chart visible without a text notice for missing current-track data."""
        from streamlit.testing.v1 import AppTest

        playback = {
            "item": {"id": "track", "name": "Song", "artists": []},
            "progress_ms": 0,
            "is_playing": True,
        }
        rows = [{"kind": "current", "audio": {}}]
        with patch("app_views.pulse.has_spotify_auth_available", return_value=True), patch(
            "app_views.pulse._get_playback", return_value=playback,
        ), patch("app_views.pulse._get_spotify_client"), patch(
            "app_views.pulse._load_history", return_value=rows,
        ), patch("app_views.pulse._start_current_track_enrichment", return_value=False):
            app = AppTest.from_string("from app_views.pulse import render_pulse_page\nrender_pulse_page()").run()

        self.assertFalse(app.exception)
        self.assertFalse(any(
            "La canción actual aún no tiene parámetros de audio disponibles." in element.value
            for element in app.caption
        ))

    def test_current_track_metadata_lookup_runs_once_per_track_change(self):
        from app_views.pulse import _start_current_track_enrichment
        from services.pulse import PARAMETERS

        state = {}
        with patch.dict(os.environ, {"SMARTERPLAYLISTS_SESSION_COOKIE": "test-cookie"}), patch(
            "app_views.pulse.threading.Thread",
        ) as thread:
            self.assertTrue(_start_current_track_enrichment("A" * 22, {}, state))
            self.assertFalse(_start_current_track_enrichment("A" * 22, {}, state))
            self.assertTrue(_start_current_track_enrichment("B" * 22, {}, state))
            self.assertFalse(_start_current_track_enrichment("B" * 22, {}, state))
            self.assertTrue(_start_current_track_enrichment("C" * 22, {"name": "Song"}, state))
            complete_audio = {parameter.key: 0.5 for parameter in PARAMETERS}
            self.assertFalse(_start_current_track_enrichment("D" * 22, complete_audio, state))

        self.assertEqual(thread.call_count, 3)
        self.assertEqual(thread.return_value.start.call_count, 3)

    def test_missing_session_does_not_block_a_later_lookup(self):
        from app_views.pulse import _start_current_track_enrichment

        state = {}
        with patch("app_views.pulse._smarterplaylists_session", return_value=(None, None)):
            self.assertFalse(_start_current_track_enrichment("A" * 22, {}, state))
        self.assertNotIn("pulse_current_track_enrichment_v2", state)

        with patch("app_views.pulse._smarterplaylists_session", return_value=("cookie", None)), patch(
            "app_views.pulse.threading.Thread",
        ) as thread:
            self.assertTrue(_start_current_track_enrichment("A" * 22, {}, state))

        thread.return_value.start.assert_called_once()

    def test_uses_default_local_session_file_when_environment_is_not_set(self):
        from app_views.pulse import _smarterplaylists_session

        with patch.dict(os.environ, {
            "SMARTERPLAYLISTS_SESSION_COOKIE": "",
            "SMARTERPLAYLISTS_SESSION_FILE": "",
        }), patch("app_views.pulse.Path.is_file", return_value=True):
            cookie, session_file = _smarterplaylists_session()

        self.assertIsNone(cookie)
        self.assertTrue(session_file.endswith(".secrets\\smarterplaylists-session.json"))

    def test_completed_current_track_lookup_caches_audio_for_current_track(self):
        from app_views.pulse import (
            _attach_current_track_audio,
            _collect_current_track_enrichment,
        )

        result_queue = queue.Queue()
        result_queue.put(("result", {"status": "completed", "audio": {"energy": 0.5}}))
        state = {
            "pulse_current_track_enrichment_v2_pending": ("A" * 22, result_queue),
        }
        _collect_current_track_enrichment("A" * 22, state)

        self.assertNotIn("pulse_current_track_enrichment_v2_pending", state)
        self.assertEqual(state["pulse_current_track_audio"], ("A" * 22, {"energy": 0.5}))
        rows = [{"kind": "current", "audio": {"name": "Song"}}]
        self.assertEqual(
            _attach_current_track_audio(rows, "A" * 22, state),
            {"energy": 0.5, "name": "Song"},
        )
        self.assertEqual(build_pulse(rows, 100)["bands"][0]["current"], 0.5)

    def test_history_failure_shows_incident_without_fake_chart(self):
        """Surface database failures rather than displaying an empty successful profile."""
        from streamlit.testing.v1 import AppTest

        with patch("app_views.pulse.has_spotify_auth_available", return_value=False), patch(
            "app_views.pulse._load_history", side_effect=RuntimeError("offline"),
        ), patch("app_views.pulse.render_incident_notice") as notice:
            app = AppTest.from_string("from app_views.pulse import render_pulse_page\nrender_pulse_page()").run()
        self.assertFalse(app.exception)
        notice.assert_called_once()
        self.assertFalse(any('aria-label="Perfil sonoro reciente"' in element.value for element in app.markdown))

    def test_chart_shows_coverage_without_axis_labels(self):
        """Show per-parameter coverage including empty history, with axes only for tempo."""
        from app_views.pulse import _render_chart

        rows = [{"kind": "history", "played_at": 100, "audio": {"energy": 0.4, "tempo": 90}}
                for _ in range(16)]
        rows += [{"kind": "history", "played_at": 100, "audio": {}} for _ in range(5)]
        for history, expected in ((rows, "(16/21 76%)"), ([], "(0/0 0%)")):
            with patch("app_views.pulse.st.markdown") as markdown:
                _render_chart(build_pulse(history, 100))
            chart = markdown.call_args.args[0]
            self.assertIn(expected, chart)
            self.assertEqual(chart.count('class="pulse-scale"'), 1)
            self.assertIn("BPM</span>", chart)
            self.assertNotIn("0%</span>", chart)
            self.assertNotIn("escuchas</small>", chart)

    def test_chart_caps_visible_markers_but_keeps_full_coverage(self):
        from app_views.pulse import _render_chart

        history = [
            {"kind": "history", "played_at": 100 - age, "name": f"track-{age}",
             "audio": {"energy": 0.4}}
            for age in range(400)
        ]
        analysis = build_pulse(history, 100, hours=None)
        with patch("app_views.pulse.st.markdown") as markdown:
            _render_chart(analysis)
        chart = markdown.call_args.args[0]
        self.assertIn("(400/400 100%)", chart)
        self.assertEqual(chart.count('class="pulse-tick"'), 250)
        self.assertEqual(analysis["bands"][0]["sample_count"], 400)
        self.assertEqual(len(analysis["bands"][0]["samples"]), 250)

    def test_pulse_navigation_is_third_and_calls_callback(self):
        """Insert the live profile before metadata and wire its navigation callback."""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string('''
import streamlit as st
from app_views.top_navigation import render_top_navigation
def choose_pulse():
    st.session_state["selected_page"] = "pulse"
render_top_navigation("reproduction", lambda: None, lambda: None, lambda: None, choose_pulse)
''').run()
        self.assertFalse(app.exception)
        self.assertEqual([button.key for button in app.button][2], "top_nav_pulse")
        app.button(key="top_nav_pulse").click().run()
        self.assertEqual(app.session_state["selected_page"], "pulse")
