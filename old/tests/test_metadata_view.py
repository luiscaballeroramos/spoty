import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class MetadataViewTest(unittest.TestCase):
    def _overview(self):
        return {"total": 10, "enriched": 4, "sources": [
            {"source": "Spotify", "available": 10, "fields": [{"field": "name", "available": 10}]},
            {"source": "SmarterPlaylists", "available": 4,
             "fields": [{"field": "popularity", "available": 4}]},
            {"source": "Last.fm", "available": 0, "fields": []},
        ]}

    def _app(self):
        return AppTest.from_string(
            "from app_views.metadata import render_metadata_page\nrender_metadata_page()"
        ).run()

    def test_entity_selector_updates_charts_and_metrics(self):
        """Render both charts and request the selected entity without external refreshes."""
        with patch("app_views.metadata._load_metadata_overview", return_value=self._overview()) as load:
            app = self._app()
            for entity in ("tracks", "artists", "albums"):
                app.selectbox(key="metadata_entity_type").select(entity).run()
                self.assertFalse(app.exception)
                self.assertEqual(load.call_args.args, (entity,))
                self.assertEqual(len(app.get("vega_lite_chart")), 2)
                self.assertEqual([metric.value for metric in app.metric], ["10", "4", "40.0%"])

    def test_provider_without_metadata_shows_empty_state(self):
        """Retain source coverage while replacing absent field data with an empty state."""
        with patch("app_views.metadata._load_metadata_overview", return_value=self._overview()):
            app = self._app()
            app.selectbox(key="metadata_source_tracks").select("Last.fm").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("vega_lite_chart")), 1)
            self.assertIn("No hay metadatos", app.info[0].value)

    def test_empty_catalog_does_not_divide_by_zero(self):
        """Show zero coverage and avoid charts when no entities exist."""
        overview = {"total": 0, "enriched": 0, "sources": []}
        with patch("app_views.metadata._load_metadata_overview", return_value=overview):
            app = self._app()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[2].value, "0.0%")
            self.assertEqual(len(app.info), 1)

    def test_database_failure_uses_incident_feedback(self):
        """Report unavailable metadata without rendering an empty success dashboard."""
        with patch("app_views.metadata._load_metadata_overview", side_effect=RuntimeError("offline")), patch(
            "app_views.metadata.render_incident_notice"
        ) as notice:
            app = self._app()
            self.assertFalse(app.exception)
            notice.assert_called_once()
            self.assertEqual(len(app.metric), 0)

    def test_metadata_navigation_is_last_and_calls_its_callback(self):
        """Keep metadata in the rightmost navigation column with its own callback."""
        from app_views.top_navigation import NAVIGATION_GROUP

        self.assertEqual(NAVIGATION_GROUP.layout.columns, 4)
        app = AppTest.from_string('''
import streamlit as st
from app_views.top_navigation import render_top_navigation
def choose_metadata():
    st.session_state["selected_page"] = "metadata"
render_top_navigation("reproduction", lambda: None, lambda: None, choose_metadata)
''').run()
        self.assertFalse(app.exception)
        self.assertEqual(
            [button.key for button in app.button],
            ["top_nav_summary", "top_nav_reproduction", "top_nav_pulse", "top_nav_metadata"],
        )
        app.button(key="top_nav_metadata").click().run()
        self.assertEqual(app.session_state["selected_page"], "metadata")
