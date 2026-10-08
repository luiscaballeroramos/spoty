import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from app_views import interface_layout


class InterfaceLayoutTest(unittest.TestCase):
    def setUp(self):
        self.streamlit = MagicMock()
        self.streamlit.columns.return_value = ("left", "main", "right")
        self.streamlit.container.return_value = nullcontext()
        self.streamlit_patch = patch.object(interface_layout, "st", self.streamlit)
        self.streamlit_patch.start()
        self.addCleanup(self.streamlit_patch.stop)

    def test_proportional_columns_passes_configured_ratios_to_streamlit(self):
        columns = interface_layout.proportional_columns(
            (1, 2, 1), gap="small", vertical_alignment="center"
        )

        self.assertEqual(columns, ["left", "main", "right"])
        self.streamlit.columns.assert_called_once_with(
            (1, 2, 1), gap="small", vertical_alignment="center"
        )

    def test_proportional_columns_rejects_invalid_ratios(self):
        for ratios in ((), (1, 0), (1, -1), (1, float("nan")), (True, 1)):
            with self.subTest(ratios=ratios):
                with self.assertRaises(ValueError):
                    interface_layout.proportional_columns(ratios)

        self.streamlit.columns.assert_not_called()

    def test_layout_styles_are_scoped_to_the_layout_key(self):
        interface_layout.apply_layout_styles("dashboard-layout")

        stylesheet = self.streamlit.markdown.call_args.args[0]
        self.assertIn(".st-key-dashboard-layout", stylesheet)
        self.assertIn("column-gap: 0", stylesheet)

    def test_proportional_columns_accepts_none_gap_by_omitting_the_gap_keyword(self):
        columns = interface_layout.proportional_columns(
            (1, 2, 1), gap=None, vertical_alignment="center"
        )

        self.assertEqual(columns, ["left", "main", "right"])
        self.streamlit.columns.assert_called_once_with(
            (1, 2, 1), vertical_alignment="center"
        )

    def test_layout_styles_preserve_responsive_ratios_and_fill_viewport(self):
        interface_layout.apply_layout_styles(
            "dashboard-layout",
            column_ratios=(1, 2, 1),
            portrait_column_ratios=(1, 2, 1),
            full_block=True,
            full_block_offset="2.6rem",
            center_content=True,
        )

        stylesheet = self.streamlit.markdown.call_args.args[0]
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr)",
            stylesheet,
        )
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr)",
            stylesheet,
        )
        self.assertIn('[data-testid="stColumn"]', stylesheet)
        self.assertIn('[data-testid="stLayoutWrapper"]', stylesheet)
        self.assertIn("height: calc(100dvh - 2.6rem) !important", stylesheet)
        self.assertIn("min-height: calc(100dvh - 2.6rem) !important", stylesheet)
        self.assertIn("align-items: center", stylesheet)
        self.assertIn("justify-content: center", stylesheet)
        self.assertIn("text-align: center", stylesheet)

    def test_layout_styles_reject_mismatched_portrait_ratios(self):
        with self.assertRaises(ValueError):
            interface_layout.apply_layout_styles(
                "dashboard-layout",
                column_ratios=(1, 2, 1),
                portrait_column_ratios=(1, 1),
            )

        self.streamlit.markdown.assert_not_called()

    def test_styled_block_applies_its_independent_style(self):
        style = interface_layout.BlockStyle(
            background="#20242c",
            padding="1rem",
            border="1px solid white",
            border_radius="0.5rem",
        )

        with interface_layout.styled_block("left-panel", style):
            pass

        stylesheet = self.streamlit.markdown.call_args.args[0]
        self.assertIn(".st-key-left-panel", stylesheet)
        self.assertIn("background: #20242c", stylesheet)
        self.assertIn("padding: 1rem", stylesheet)
        self.streamlit.container.assert_called_once_with(key="left-panel")

    def test_styled_block_rejects_invalid_keys(self):
        with self.assertRaises(ValueError):
            with interface_layout.styled_block("invalid key"):
                pass

        self.streamlit.markdown.assert_not_called()
        self.streamlit.container.assert_not_called()

    def test_styled_button_uses_the_shared_style_and_returns_click_state(self):
        self.streamlit.button.return_value = True
        style = interface_layout.BlockStyle(
            background="#1db954",
            color="#ffffff",
            border="none",
            border_radius="0.5rem",
            padding="0.75rem 1rem",
            width="100%",
            aspect_ratio="1 / 1",
            min_height="3rem",
            max_width="20rem",
            max_height="20rem",
            font_size="1rem",
            font_weight="600",
            text_align="center",
            box_shadow="0 2px 4px #000",
        )

        clicked = interface_layout.styled_button(
            "Save",
            key="dashboard-save",
            style=style,
            type="primary",
            help="Save changes",
            icon=":material/save:",
        )

        self.assertTrue(clicked)
        stylesheet = self.streamlit.markdown.call_args.args[0]
        self.assertIn(
            ".st-key-dashboard-save [data-testid=\"stButton\"] button",
            stylesheet,
        )
        self.assertIn(interface_layout._style_css(style), stylesheet)
        self.assertIn("aspect-ratio: 1 / 1;", stylesheet)
        self.assertIn("max-width: 20rem;", stylesheet)
        self.assertIn("max-height: 20rem;", stylesheet)

        with interface_layout.styled_block("dashboard-panel", style):
            pass
        block_stylesheet = self.streamlit.markdown.call_args.args[0]
        self.assertIn(
            f".st-key-dashboard-panel {{ box-sizing: border-box; "
            f"{interface_layout._style_css(style)} }}",
            block_stylesheet,
        )
        self.streamlit.button.assert_called_once_with(
            "Save",
            key="dashboard-save",
            type="primary",
            help="Save changes",
            disabled=False,
            use_container_width=True,
            icon=":material/save:",
        )

    def test_styled_button_rejects_invalid_keys(self):
        with self.assertRaises(ValueError):
            interface_layout.styled_button("Save", key="invalid key")

        self.streamlit.markdown.assert_not_called()
        self.streamlit.button.assert_not_called()


if __name__ == "__main__":
    unittest.main()
