import unittest
from datetime import datetime
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from spoty_data_reader import SpotySnapshot
from streamlit_app import render_dashboard, render_listening_calendar


class DashboardChartTest(unittest.TestCase):
    def test_summary_shows_tracks_albums_and_artists_without_duplicate_song_counts(self):
        app = AppTest.from_string("""
from spoty_data_reader import SpotySnapshot
from streamlit_app import render_dashboard

render_dashboard(SpotySnapshot(
    "abcdef123",
    (
        {"track_id": "first", "played_at": 1704067200},
        {"track_id": "first", "played_at": 1704067201},
    ),
    ({"id": "first"}, {"id": "second"}, {"id": "third"}),
    ({"id": "artist"},),
    ({"id": "album1"}, {"id": "album2"}),
))
""").run()
        self.assertFalse(app.exception)
        self.assertEqual(
            [(metric.label, metric.value) for metric in app.metric],
            [
                ("Escuchas", "2"),
                ("Canciones en catálogo", "3"),
                ("Álbumes en catálogo", "2"),
                ("Artistas en catálogo", "1"),
            ],
        )

    def test_chart_counts_tracks_separately_and_labels_ranking(self):
        events = tuple(
            {"track_id": track_id, "played_at": 1793959200 + index}
            for index, track_id in enumerate(["first", "second", "first", "missing"])
        )
        snapshot = SpotySnapshot(
            commit="abcdef123",
            listening_events=events,
            tracks=(
                {"id": "first", "name": "Same name", "artists_ids": ["artist"]},
                {"id": "second", "name": "Same name", "artists_ids": ["artist"]},
            ),
            artists=({"id": "artist", "name": "Artist"},),
            albums=(),
        )
        with patch("streamlit_app.st") as streamlit:
            streamlit.session_state = {}
            render_dashboard(snapshot)
        chart = streamlit.altair_chart.call_args.args[0]
        rows = chart.data.to_dict("records")
        self.assertEqual([row["Escuchas"] for row in rows], [2, 1, 1])
        self.assertEqual(
            [row["Etiqueta"] for row in rows],
            ["1. Same name · Artist", "2. Same name · Artist", "3. missing"],
        )
        spec = chart.to_dict()
        self.assertEqual(spec["layer"][0]["mark"]["type"], "bar")
        self.assertEqual(spec["layer"][1]["mark"]["type"], "text")
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["axis"]["tickMinStep"], 1)
        self.assertTrue(
            any("Todo el historial guardado" in call.args[0] for call in streamlit.caption.call_args_list)
        )

    def test_empty_history_does_not_render_chart(self):
        snapshot = SpotySnapshot("abcdef123", (), (), (), ())
        with patch("streamlit_app.st") as streamlit:
            render_dashboard(snapshot)
        streamlit.altair_chart.assert_not_called()
        streamlit.info.assert_called_once()


class ListeningCalendarTest(unittest.TestCase):
    def render_month(self, timestamps, month):
        events = tuple(
            {"track_id": "track", "played_at": int(datetime.fromisoformat(value).timestamp())}
            for value in timestamps
        )
        snapshot = SpotySnapshot("abcdef123", events, (), (), ())
        with patch("streamlit_app.st") as streamlit:
            selected = datetime.fromisoformat(f"{month}-01")
            streamlit.session_state = {
                "calendar_year": selected.year, "calendar_month": selected.month,
            }
            render_listening_calendar(snapshot)
        return streamlit, streamlit.altair_chart.call_args.args[0]

    def test_calendar_counts_utc_days_and_includes_leap_day_and_zeroes(self):
        streamlit, chart = self.render_month([
            "2024-02-01T00:00:00+00:00",
            "2024-02-01T23:59:59+00:00",
            "2024-03-01T00:30:00+01:00",
            "2024-03-01T00:00:00+00:00",
        ], "2024-02")
        rows = chart.data.to_dict("records")
        self.assertEqual(len(rows), 29)
        self.assertEqual(rows[0], {
            "Fecha": "2024-02-01", "Dia": 1, "DiaSemana": "Jue", "Semana": 0, "Escuchas": 2,
        })
        self.assertEqual(rows[1]["Escuchas"], 0)
        self.assertEqual(rows[-1]["Fecha"], "2024-02-29")
        self.assertEqual(rows[-1]["Escuchas"], 1)
        self.assertEqual(sum(row["Escuchas"] for row in rows), 3)
        self.assertEqual(
            list(streamlit.selectbox.call_args.kwargs["options"]), [2, 3],
        )
        spec = chart.to_dict()
        cells = spec["layer"][0]
        self.assertEqual(cells["mark"]["type"], "rect")
        color = cells["encoding"]["color"]
        self.assertEqual(color["condition"], {
            "test": "(datum.Escuchas === 0)", "value": "#000000",
        })
        self.assertEqual(color["scale"]["domain"], [1, 2])
        self.assertEqual(color["scale"]["range"], ["#22c55e", "#ef4444"])
        self.assertFalse(color["scale"]["zero"])
        self.assertEqual(
            [item["field"] for item in cells["encoding"]["tooltip"]], ["Fecha", "Escuchas"],
        )
        self.assertEqual(spec["layer"][1]["encoding"]["text"]["field"], "Dia")

    def test_calendar_keeps_empty_months_across_year_boundary(self):
        streamlit, chart = self.render_month([
            "2023-12-31T23:59:59+00:00",
            "2024-02-01T00:00:00+00:00",
        ], "2024-01")
        self.assertEqual(
            list(streamlit.selectbox.call_args_list[0].kwargs["options"]), [2024, 2023],
        )
        self.assertEqual(
            list(streamlit.selectbox.call_args_list[1].kwargs["options"]), [1, 2],
        )
        rows = chart.data.to_dict("records")
        self.assertEqual(len(rows), 31)
        self.assertEqual(sum(row["Escuchas"] for row in rows), 0)
        self.assertEqual(rows[0]["DiaSemana"], "Lun")
        self.assertIsNotNone(chart.to_dict()["layer"][0]["encoding"]["color"]["legend"])

    def test_calendar_uses_same_historical_daily_scale_across_months_and_years(self):
        timestamps = [
            f"{day}T00:00:{second:02d}+00:00"
            for day, count in (
                ("2023-12-28", 4), ("2023-12-29", 3),
                ("2024-02-01", 2), ("2024-02-02", 2), ("2024-03-01", 1),
            )
            for second in range(count)
        ]
        _, historical_chart = self.render_month(timestamps, "2023-12")
        historical_color = historical_chart.to_dict()["layer"][0]["encoding"]["color"]
        self.assertEqual(historical_color["scale"]["domain"], [1, 4])
        for month in ("2024-01", "2024-02", "2024-03"):
            with self.subTest(month=month):
                _, chart = self.render_month(timestamps, month)
                color = chart.to_dict()["layer"][0]["encoding"]["color"]
                self.assertEqual(color, historical_color)

    def test_calendar_supports_six_week_month_with_single_play_in_green(self):
        _, chart = self.render_month(["2026-03-01T12:00:00+00:00"], "2026-03")
        rows = chart.data.to_dict("records")
        self.assertEqual(rows[0]["DiaSemana"], "Dom")
        self.assertEqual(rows[0]["Semana"], 0)
        self.assertEqual(rows[-1]["Fecha"], "2026-03-31")
        self.assertEqual(rows[-1]["DiaSemana"], "Mar")
        self.assertEqual(rows[-1]["Semana"], 5)
        color = chart.to_dict()["layer"][0]["encoding"]["color"]
        self.assertEqual(color["scale"]["domain"], [1, 1])
        self.assertEqual(color["scale"]["range"], ["#22c55e", "#22c55e"])

    def create_calendar_app(self):
        return AppTest.from_string("""
from datetime import datetime
from spoty_data_reader import SpotySnapshot
from streamlit_app import render_dashboard

render_dashboard(SpotySnapshot(
    "abcdef123",
    tuple(
        {"track_id": "track", "played_at": int(datetime.fromisoformat(value).timestamp())}
        for value in ("2023-12-31T12:00:00+00:00", "2025-02-01T00:00:00+00:00")
    ),
    (), (), (),
))
""").run()

    def test_calendar_arrows_cross_years_and_update_selectors(self):
        app = self.create_calendar_app()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="calendar_year").value, 2025)
        self.assertEqual(app.selectbox(key="calendar_month").value, 2)
        self.assertTrue(app.button(key="calendar_next").disabled)
        app.button(key="calendar_previous").click().run()
        self.assertEqual(app.selectbox(key="calendar_month").value, 1)
        app.button(key="calendar_previous").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="calendar_year").value, 2024)
        self.assertEqual(app.selectbox(key="calendar_month").value, 12)
        self.assertIn("### Diciembre 2024", [element.value for element in app.markdown])
        app.button(key="calendar_next").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="calendar_year").value, 2025)
        self.assertEqual(app.selectbox(key="calendar_month").value, 1)
        self.assertEqual(len(app.get("vega_lite_chart")), 2)

    def test_separate_year_and_month_selectors_respect_history_bounds(self):
        app = self.create_calendar_app()
        app.selectbox(key="calendar_year").select(2023).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="calendar_month").value, 12)
        self.assertEqual(app.selectbox(key="calendar_month").options, ["Diciembre"])
        self.assertTrue(app.button(key="calendar_previous").disabled)
        app.selectbox(key="calendar_year").select(2024).run()
        app.selectbox(key="calendar_month").select(2).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="calendar_year").value, 2024)
        self.assertEqual(app.selectbox(key="calendar_month").value, 2)
        self.assertIn("### Febrero 2024", [element.value for element in app.markdown])
        self.assertFalse(app.button(key="calendar_previous").disabled)
        self.assertFalse(app.button(key="calendar_next").disabled)
        self.assertEqual(len(app.get("vega_lite_chart")), 2)


if __name__ == "__main__":
    unittest.main()
