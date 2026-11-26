import unittest
from datetime import date, datetime
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from spoty_data_reader import SpotySnapshot
from streamlit_app import (
    growth_period_bounds, listening_growth, render_dashboard,
    render_listening_calendar, render_listening_growth,
)


class DashboardChartTest(unittest.TestCase):
    def test_dashboard_uses_fluid_auto_layout_for_all_regions(self):
        snapshot = SpotySnapshot("abcdef123", (), (), (), ())
        with patch("streamlit_app.render_layout") as layout:
            render_dashboard(snapshot)

        options = layout.call_args.kwargs
        self.assertEqual(options["key"], "spoty-dashboard")
        self.assertEqual(options["orientation"], "auto")
        self.assertEqual(options["height"], "fluid")
        self.assertEqual(
            set(options["regions"]),
            {"metrics", "growth", "calendar", "ranking", "recent"},
        )
        self.assertEqual(
            options["horizontal"].areas,
            (
                ("metrics", "metrics"),
                ("growth", "growth"),
                ("calendar", "ranking"),
                ("recent", "recent"),
            ),
        )
        self.assertEqual(
            options["vertical"].areas,
            (("metrics",), ("growth",), ("calendar",), ("ranking",), ("recent",)),
        )

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
            streamlit.selectbox.return_value = "Todo"
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


class ListeningGrowthTest(unittest.TestCase):
    def test_distinct_counts_carry_into_period_and_include_zero_days(self):
        events = tuple(
            {"track_id": track, "played_at": int(datetime.fromisoformat(time).timestamp())}
            for track, time in [
                ("a", "2024-01-01T23:30:00+00:00"),
                ("a", "2024-01-03T00:00:00+00:00"),
                ("b", "2024-01-03T01:00:00+00:00"),
                ("missing", "2024-01-03T23:30:00+00:00"),
                ("c", "2024-01-04T00:30:00+01:00"),
            ]
        )
        snapshot = SpotySnapshot("commit", events, (
            {"id": "a", "album_id": "album1", "artists_ids": ["one", "two"]},
            {"id": "b", "album_id": "album1", "artists_ids": ["two", "three"]},
            {"id": "c", "album_id": "album2", "artists_ids": ["three"]},
        ), (), ())
        rows = listening_growth(snapshot, date(2024, 1, 2), date(2024, 1, 4)).to_dict("records")
        self.assertEqual(rows, [
            {"Fecha": "2024-01-02", "FechaFin": "2024-01-03", "Escuchas": 0, "Canciones": 1, "Artistas": 2, "Álbumes": 1},
            {"Fecha": "2024-01-03", "FechaFin": "2024-01-04", "Escuchas": 4, "Canciones": 4, "Artistas": 3, "Álbumes": 2},
            {"Fecha": "2024-01-04", "FechaFin": "2024-01-05", "Escuchas": 0, "Canciones": 4, "Artistas": 3, "Álbumes": 2},
        ])

    def test_calendar_periods_use_utc_today(self):
        today = date(2024, 3, 31)
        first, last = date(2022, 1, 1), today
        expected = {
            "Todo": first, "Último año": date(2023, 3, 31),
            "Últimos 6 meses": date(2023, 9, 30),
            "Último mes": date(2024, 2, 29), "Últimos 7 días": date(2024, 3, 25),
        }
        for period, start in expected.items():
            with self.subTest(period=period):
                self.assertEqual(growth_period_bounds(period, today, first, last), (start, today))
        self.assertEqual(
            growth_period_bounds("Últimos 7 días", date(2025, 1, 2), first, last),
            (date(2024, 12, 27), date(2025, 1, 2)),
        )

    def test_combined_chart_has_independent_y_scales(self):
        snapshot = SpotySnapshot(
            "commit", ({"track_id": "a", "played_at": 1704067200},),
            ({"id": "a", "album_id": "album", "artists_ids": ["artist"]},), (), (),
        )
        with patch("streamlit_app.st") as streamlit:
            streamlit.selectbox.return_value = "Todo"
            render_listening_growth(snapshot)
        spec = streamlit.altair_chart.call_args.args[0].to_dict()
        self.assertEqual(spec["resolve"]["scale"]["y"], "independent")
        self.assertEqual(spec["layer"][0]["mark"]["type"], "bar")
        self.assertEqual(spec["layer"][0]["mark"]["binSpacing"], 0)
        self.assertEqual(spec["layer"][0]["mark"]["orient"], "vertical")
        self.assertEqual(spec["layer"][0]["encoding"]["x2"]["field"], "FechaFin")
        self.assertEqual(spec["layer"][0]["encoding"]["y"]["scale"], {
            "domain": [0, 1], "nice": False, "padding": 0,
        })
        self.assertEqual(spec["layer"][0]["encoding"]["y2"]["datum"], 0)
        self.assertEqual(spec["layer"][1]["mark"]["type"], "line")
        self.assertEqual(spec["layer"][1]["transform"][0]["fold"], ["Canciones", "Artistas", "Álbumes"])

    def test_scales_use_full_history_for_bars_and_period_for_lines(self):
        events = tuple(
            {"track_id": track, "played_at": int(datetime.fromisoformat(day).timestamp()) + second}
            for track, day, count in [
                ("a", "2024-01-01T00:00:00+00:00", 5),
                ("b", "2024-01-02T00:00:00+00:00", 1),
                ("c", "2024-01-03T00:00:00+00:00", 1),
                ("c", "2024-01-04T00:00:00+00:00", 6),
            ]
            for second in range(count)
        )
        snapshot = SpotySnapshot("commit", events, (
            {"id": "a", "album_id": "one", "artists_ids": ["one"]},
            {"id": "b", "album_id": "two", "artists_ids": ["one"]},
            {"id": "c", "album_id": "three", "artists_ids": ["one", "two"]},
        ), (), ())
        for start, end, expected_domain, expected_ticks in [
            (date(2024, 1, 2), date(2024, 1, 3), [1, 3], []),
            (date(2024, 1, 1), date(2024, 1, 1), [1, 2], []),
            (date(2023, 12, 31), date(2023, 12, 31), [0, 1], [0]),
        ]:
            with self.subTest(start=start):
                with patch("streamlit_app.st") as streamlit, patch(
                    "streamlit_app.growth_period_bounds", return_value=(start, end),
                ):
                    render_listening_growth(snapshot)
                spec = streamlit.altair_chart.call_args.args[0].to_dict()
                bars = spec["layer"][0]["encoding"]["y"]
                lines = spec["layer"][1]["encoding"]["y"]
                self.assertEqual(bars["scale"]["domain"], [0, 6])
                self.assertFalse(bars["axis"]["grid"])
                self.assertEqual(lines["scale"]["domain"], expected_domain)
                self.assertFalse(lines["scale"]["zero"])
                self.assertEqual(lines["axis"]["values"], expected_ticks)
                self.assertTrue(lines["axis"]["grid"])
                streamlit.caption.assert_not_called()

    def test_line_grid_has_quarter_thousand_steps_with_major_labels_only(self):
        snapshot = SpotySnapshot(
            "commit", ({"track_id": "a", "played_at": 1704067200},), (), (), (),
        )
        for lower, upper, ticks in [
            (4608, 6309, [4750, 5000, 5250, 5500, 5750, 6000, 6250]),
            (1000, 3000, [1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000]),
            (5100, 5900, [5250, 5500, 5750]),
        ]:
            with self.subTest(limits=(lower, upper)):
                data = listening_growth(snapshot, date(2024, 1, 1), date(2024, 1, 1))
                data["Canciones"] = upper
                data["Artistas"] = lower
                data["Álbumes"] = lower
                with patch("streamlit_app.st") as streamlit, patch(
                    "streamlit_app.listening_growth", return_value=data,
                ):
                    streamlit.selectbox.return_value = "Todo"
                    render_listening_growth(snapshot)
                spec = streamlit.altair_chart.call_args.args[0].to_dict()
                y = spec["layer"][1]["encoding"]["y"]
                self.assertEqual(y["scale"]["domain"], [lower, upper])
                self.assertEqual(y["axis"]["values"], ticks)
                self.assertEqual(
                    y["axis"]["labelExpr"],
                    "datum.value % 1000 === 0 ? format(datum.value, 'd') : ''",
                )
                self.assertEqual(y["axis"]["gridOpacity"]["expr"], "datum.value % 1000 === 0 ? 0.5 : 0.15")
                self.assertEqual(y["axis"]["gridWidth"]["expr"], "datum.value % 1000 === 0 ? 1 : 0.5")


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
        self.assertEqual(len(app.get("vega_lite_chart")), 3)

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
        self.assertEqual(len(app.get("vega_lite_chart")), 3)


if __name__ == "__main__":
    unittest.main()
