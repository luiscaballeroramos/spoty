import unittest
from unittest.mock import patch

from spoty_data_reader import SpotySnapshot
from streamlit_app import render_dashboard


class DashboardChartTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
