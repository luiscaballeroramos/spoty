import unittest
from dataclasses import replace

from blocks import Block, Position, Size, Span
from layout import Group, Layout
from styles import (
    BlockStyle,
    Border,
    ButtonDimensions,
    ButtonStyle,
    Corners,
    Insets,
    PageStyle,
    Shadow,
)


class StyleTests(unittest.TestCase):
    def test_style_css(self):
        from renderer import style_css

        css = style_css(BlockStyle(
            padding=Insets(1, 2, 3, 4), radius=Corners(1, 2, 3, 4),
            border=Border(2, "#123456", "dashed"), shadow=Shadow(),
            content_horizontal="center", content_vertical="end", background_opacity=0.5,
        ), "#ffffff")
        for expected in ("padding: 1px 2px 3px 4px", "border-radius: 1px 2px 3px 4px", "border-width: 2px", "border-style: dashed", "justify-content: flex-end", "text-align: center", "--lab-background-opacity: 0.5"):
            self.assertIn(expected, css)

    def test_style_values(self):
        self.assertEqual(Insets(1, 2, 3, 4).css(), "1px 2px 3px 4px")
        self.assertEqual(Corners(1, 2, 3, 4).css(), "1px 2px 3px 4px")
        self.assertIn("rgba(0, 0, 0, 0.15)", Shadow().css())
        style = BlockStyle(padding=Insets(left=8), radius=Corners(top_left=6), border=Border(2))
        self.assertEqual(Block("styled", style=style).active_style(), style)
        self.assertEqual(Block("selected", selected_style=style, state="selected").active_style(), style)

    def test_invalid_styles(self):
        factories = [
            lambda: Insets(top=-1), lambda: Corners(top_left=float("nan")),
            lambda: Border(style="invalid"), lambda: Border(color="red"),
            lambda: Shadow(blur_px=-1), lambda: Shadow(opacity=2),
            lambda: BlockStyle(padding=-1), lambda: BlockStyle(radius=float("inf")),
            lambda: BlockStyle(background_image="https://example.com/</style>"),
            lambda: BlockStyle(background_image="javascript:alert(1)"),
            lambda: BlockStyle(content_horizontal="invalid"), lambda: BlockStyle(overflow="invalid"),
            lambda: ButtonStyle(portrait="invalid"),
            lambda: PageStyle(caption_margin_px=-1),
            lambda: Block("ratio", aspect_ratio=2), lambda: Block("ratio", height=None, aspect_ratio=0),
            lambda: Layout(column_weights=(1, 2)),
            lambda: Layout(columns=2, portrait_row_weights=(1,)),
            lambda: Layout(columns=2, portrait_row_weights=(1, 0)),
            lambda: Layout(row_gap_px=-1),
            lambda: Layout(parent_gap_px=-1),
        ]
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()

    def test_page_style_renders_shared_streamlit_page_rules(self):
        from unittest.mock import patch

        from renderer import render_page_style

        with patch("renderer.st.html") as html:
            render_page_style(PageStyle(
                hide_header=True,
                content_padding=0,
                content_overflow="visible",
                app_overflow_y="auto",
                vertical_gap_px=0,
                horizontal_gap_px=0,
                element_margin_px=0,
                caption_margin_px=Insets(top=0, bottom=2),
            ))

        css = html.call_args.args[0]
        self.assertIn('[data-testid="stHeader"]', css)
        self.assertIn("padding: 0px !important", css)
        self.assertIn("overflow-y: auto !important", css)
        self.assertIn("gap: 0px !important", css)
        self.assertIn('[data-testid="stCaptionContainer"]', css)
        self.assertIn("margin: 0px 0px 2px 0px !important", css)

    def test_render_button_applies_style_and_background_image(self):
        from unittest.mock import patch

        from renderer import render_button

        with patch("renderer.st.html") as html, patch(
            "renderer.st.button", return_value=True
        ) as button:
            clicked = render_button(
                "Play",
                key="play-control",
                style=ButtonStyle(
                    background_image="https://example.com/cover.jpg",
                    background_fit="contain",
                    aspect_ratio="1 / 1",
                    fill_container=True,
                    portrait=ButtonDimensions("2rem", "2rem", "0", "1"),
                ),
            )

        self.assertTrue(clicked)
        self.assertIn(
            'background-image: url("https://example.com/cover.jpg")',
            html.call_args.args[0],
        )
        self.assertIn("background-size: contain", html.call_args.args[0])
        self.assertIn("height: 100% !important", html.call_args.args[0])
        self.assertIn("@media (orientation: portrait)", html.call_args.args[0])
        self.assertIn("min-height: 0 !important", html.call_args.args[0])
        self.assertIn("aspect-ratio: 1 !important", html.call_args.args[0])
        button.assert_called_once_with(
            "Play",
            key="play-control",
            type="secondary",
            help=None,
            disabled=False,
            use_container_width=True,
            icon=None,
        )
        with self.assertRaises(ValueError):
            ButtonStyle(background_image="javascript:alert(1)")

    def test_layout_overrides_and_declared_order(self):
        layout = Layout(rows=1, columns=2, gap_px=16, row_gap_px=0, column_gap_px=24, column_weights=(2, 1))
        self.assertEqual((layout.row_gap, layout.column_gap), (0, 24))
        blocks = [Block("last"), Block("hidden", visible=False), Block("first")]
        self.assertEqual(layout.place(blocks), {"last": Position(1, 1), "first": Position(1, 2)})


class BlockTests(unittest.TestCase):
    def test_size_rules(self):
        self.assertEqual(Size(65, "%", 280, 900).css(), "clamp(280px, 65%, 900px)")
        self.assertEqual(Size(24, "vh", 160).css(), "max(160px, 24vh)")
        self.assertEqual(Size(180).css(), "max(0px, 180px)")
        self.assertEqual(Size(30, "dvh", offset_px=20).css(), "max(0px, calc(30dvh - 20px))")

    def test_invalid_sizes(self):
        for arguments in ((-1,), (float("nan"),), (float("inf"),), (1, "rem"), (10, "px", 20, 10), (10, "px", 0, None, -1)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Size(*arguments)

    def test_invalid_blocks(self):
        for arguments in ({"key": "bad key"}, {"key": "ok", "color": "red"}, {"key": "ok", "height": Size(50, "%")}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Block(**arguments)

    def test_portrait_height_is_rendered_only_when_configured(self):
        from unittest.mock import patch
        from renderer import render_block

        for size in (None, Size(30, "dvh", offset_px=20)):
            with self.subTest(size=size), patch("renderer.st.html") as html:
                with patch("renderer.st.container"):
                    render_block(Block("responsive", portrait_height=size))
                css = html.call_args.args[0]
                self.assertEqual("@media (orientation: portrait)" in css, size is not None)
                if size is not None:
                    self.assertIn("height: max(0px, calc(30dvh - 20px)) !important", css)

    def test_app_and_size_edit(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        self.assertFalse(app.exception)
        initial_height = app.session_state.blocks[0].height
        app.number_input(key="principal_width_value").set_value(75).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.blocks[0].width.value, 75)
        app.checkbox(key="principal_height_limited").check().run()
        maximum = app.number_input(key="principal_height_max").value
        app.number_input(key="principal_height_min").set_value(maximum + 1).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.error), 1)
        self.assertEqual(app.session_state.blocks[0].height.min_px, initial_height.min_px)


class LayoutTests(unittest.TestCase):
    def test_auto_placement_reserves_fixed_cells(self):
        blocks = [Block("first"), Block("fixed", position=Position(1, 1)), Block("last")]
        self.assertEqual(Layout(rows=2, columns=2).place(blocks), {
            "fixed": Position(1, 1), "first": Position(1, 2), "last": Position(2, 1),
        })

    def test_row_and_column(self):
        blocks = [Block("first"), Block("last")]
        self.assertEqual(Layout(rows=1, columns=2).place(blocks)["last"], Position(1, 2))
        self.assertEqual(Layout(rows=2, columns=1).place(blocks)["last"], Position(2, 1))

    def test_flow_grows_along_selected_axis(self):
        blocks = [Block("first", height=None), Block("second", height=None), Block("third", height=None)]
        self.assertEqual(Layout(rows=1, columns=2, flow="row").place(blocks), {
            "first": Position(1, 1), "second": Position(1, 2), "third": Position(2, 1),
        })
        self.assertEqual(Layout(rows=2, columns=1, flow="column").place(blocks), {
            "first": Position(1, 1), "second": Position(2, 1), "third": Position(1, 2),
        })

    def test_invalid_layouts(self):
        for arguments in ({"rows": 0}, {"columns": 1.5}, {"columns": True}, {"flow": "invalid"}, {"horizontal": "invalid"}, {"vertical": "invalid"}, {"gap_px": -1}, {"gap_px": float("nan")}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Layout(**arguments)
        for coordinates in ((0, 1), (1, -1), (1.5, 1)):
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                Position(*coordinates)

    def test_parent_gap_is_rendered_by_the_lab(self):
        from contextlib import nullcontext
        from unittest.mock import patch

        from renderer import render_layout

        with patch("renderer.st.html") as html:
            with patch("renderer.st.container", return_value=nullcontext()):
                render_layout([], Layout(parent_gap_px=0), key="flush")

        css = html.call_args.args[0]
        self.assertIn(
            '[data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] .st-key-flush)',
            css,
        )
        self.assertIn("gap: 0px !important", css)

    def test_invalid_placements(self):
        cases = [
            [Block("same"), Block("same")],
            [Block("outside", position=Position(3, 1))],
            [Block("first", position=Position(1, 1)), Block("last", position=Position(1, 1))],
            [Block("first"), Block("second"), Block("third")],
        ]
        for blocks in cases:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                Layout(rows=2, columns=1).place(blocks)

    def test_portrait_collapse_is_opt_in_and_independent_of_breakpoint(self):
        from unittest.mock import patch
        from renderer import render_layout

        blocks = [Block("first"), Block("second")]
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch("renderer.st.html") as html:
                with patch("renderer.st.container"):
                    render_layout(
                        blocks,
                        Layout(rows=1, columns=2, collapse_on_portrait=enabled),
                        collapse_on_mobile=False,
                    )
                css = html.call_args.args[0]
                self.assertEqual("@media (orientation: portrait)" in css, enabled)
                if enabled:
                    self.assertIn("grid-template-columns: minmax(0, 1fr) !important", css)
                    self.assertIn("grid-row: auto !important", css)

    def test_portrait_collapse_uses_explicit_row_weights(self):
        from unittest.mock import patch
        from renderer import render_layout

        with patch("renderer.st.html") as html:
            with patch("renderer.st.container"):
                render_layout(
                    [Block("first"), Block("middle"), Block("last")],
                    Layout(
                        rows=1,
                        columns=3,
                        column_weights=(1, 2, 1),
                        portrait_row_weights=(1, 2, 1),
                        collapse_on_portrait=True,
                    ),
                )

        css = html.call_args.args[0]
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr) !important",
            css,
        )

    def test_app_layout_controls(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        initial_layout = app.session_state.group.layout
        app.number_input(key="layout_columns").set_value(6).run()
        app.selectbox(key="layout_flow").select("row").run()
        app.selectbox(key="layout_horizontal").select("center").run()
        app.selectbox(key="layout_vertical").select("end").run()
        app.checkbox(key="layout_parent_gap_custom").check().run()
        app.number_input(key="layout_parent_gap").set_value(0).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state.group.layout, replace(initial_layout, columns=6, flow="row", horizontal="center", vertical="end", parent_gap_px=0))
        app.selectbox(key="layout_flow").select("grid").run()
        app.number_input(key="layout_columns").set_value(1).run()
        self.assertTrue(app.error)
        self.assertEqual(app.session_state.group.layout.columns, 6)

    def test_span_validation_and_cells(self):
        for arguments in ((0, 1), (1, -1), (1.5, 1), (True, 1)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Span(*arguments)
        self.assertEqual(Span(2, 2).cells(Position(2, 3)), {
            Position(2, 3), Position(2, 4), Position(3, 3), Position(3, 4),
        })

    def test_spans_reserve_all_cells(self):
        blocks = [
            Block("auto", span=Span(2, 1)),
            Block("fixed", position=Position(1, 1), span=Span(2, 2)),
            Block("last"),
        ]
        self.assertEqual(Layout(3, 3).place(blocks), {
            "fixed": Position(1, 1), "auto": Position(1, 3), "last": Position(3, 1),
        })

    def test_spans_overlap_and_bounds(self):
        cases = [
            [Block("wide", position=Position(1, 1), span=Span(2, 2)), Block("overlap", position=Position(2, 2))],
            [Block("wide", position=Position(1, 3), span=Span(1, 2))],
            [Block("tall", position=Position(3, 1), span=Span(2, 1))],
            [Block("auto", span=Span(1, 4))],
        ]
        for blocks in cases:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                Layout(3, 3).place(blocks)

    def test_fragmented_space_is_rejected(self):
        blocks = [Block("middle", position=Position(1, 2), span=Span(2, 1)), Block("wide", span=Span(1, 2))]
        with self.assertRaisesRegex(ValueError, "rectangular"):
            Layout(2, 3).place(blocks)

    def test_nested_group_example(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state.blocks), 5)
        root = app.session_state.group
        self.assertEqual(root.children[0].children, ("principal", "resumen"))
        self.assertEqual(root.children[1].children, ("secundario", "detalle", "actividad"))
        blocks = {block.key: block for block in app.session_state.blocks}
        self.assertEqual((blocks["principal"].position, blocks["principal"].span), (Position(1, 1), Span(2, 2)))
        self.assertEqual((blocks["resumen"].position, blocks["resumen"].span), (Position(3, 1), Span(1, 2)))
        self.assertEqual((blocks["secundario"].position, blocks["secundario"].span), (Position(1, 1), Span(1, 2)))
        self.assertEqual((blocks["detalle"].position, blocks["actividad"].position), (Position(3, 1), Position(3, 2)))
        root.validate({block.key: block for block in app.session_state.blocks})
        app.button(key="reset_example").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state.group.children[0].children, ("principal", "resumen"))

    def test_style_controls_preserve_other_properties(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        initial = app.session_state.blocks[0]
        app.number_input(key="principal_style_radius_all").set_value(8).run()
        app.number_input(key="principal_style_border_width_all").set_value(2).run()
        app.checkbox(key="layout_independent_gap").check().run()
        app.number_input(key="layout_row_gap").set_value(12).run()
        app.checkbox(key="layout_responsive").check().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state.blocks[0], replace(initial, style=replace(initial.style, radius=8, border=Border(2))))
        self.assertEqual(app.session_state.group.layout.row_gap, 12)
        self.assertEqual(app.session_state.group.layout.mobile_breakpoint_px, 640)
        app.checkbox(key="principal_auto_height").check().run()
        app.checkbox(key="principal_use_ratio").check().run()
        app.number_input(key="principal_ratio").set_value(2).run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state.blocks[0].height)
        self.assertEqual(app.session_state.blocks[0].aspect_ratio, 2)


class GroupTests(unittest.TestCase):
    def test_groups_use_layout_vertical_alignment_in_available_height(self):
        from unittest.mock import patch
        from renderer import render_layout

        group = Group("track", ("image", "title"), Layout(rows=2, columns=1))
        layout = Layout(
            rows=1, columns=1, vertical="center",
            height=Size(100, "dvh", offset_px=64),
        )
        with patch("renderer.st.html") as html, patch("renderer.st.container"):
            render_layout([group], layout)
        css = html.call_args.args[0]
        self.assertIn("justify-content: center", css)
        self.assertIn("flex: 0 0 auto !important", css)
        self.assertIn(f"height: {layout.height.css()} !important", css)

    def test_nested_groups_have_independent_layouts(self):
        blocks = {
            "principal": Block("principal", position=Position(1, 1), span=Span(2, 2)),
            "resumen": Block("resumen", position=Position(3, 1), span=Span(1, 2)),
            "secundario": Block("secundario", position=Position(1, 1), span=Span(1, 2)),
            "detalle": Block("detalle", position=Position(2, 1)),
            "actividad": Block("actividad", position=Position(2, 2)),
        }
        content = Group("contenido", ("principal", "resumen"), Layout(rows=3, columns=2))
        sidebar = Group("lateral", ("secundario", "detalle", "actividad"), Layout(rows=2, columns=2))
        dashboard = Group("dashboard", (content, sidebar), Layout(rows=1, columns=2, mobile_breakpoint_px=640))

        dashboard.validate(blocks)
        self.assertEqual(dashboard.layout.place(dashboard.resolve_children(blocks)), {
            "contenido": Position(1, 1), "lateral": Position(1, 2),
        })
        self.assertEqual(content.layout.place(content.resolve_children(blocks)), {
            "principal": Position(1, 1), "resumen": Position(3, 1),
        })
        self.assertEqual(sidebar.layout.place(sidebar.resolve_children(blocks)), {
            "secundario": Position(1, 1), "detalle": Position(2, 1), "actividad": Position(2, 2),
        })
        self.assertFalse(hasattr(dashboard, "style"))
        self.assertFalse(hasattr(dashboard, "order"))

    def test_parent_mobile_breakpoint_does_not_collapse_child_layouts(self):
        from contextlib import nullcontext
        from unittest.mock import patch

        from renderer import render_group

        blocks = [Block("first"), Block("second")]
        child = Group("child", ("first", "second"), Layout(rows=1, columns=2))
        parent = Group("parent", (child,), Layout(rows=1, columns=1, mobile_breakpoint_px=640))

        with patch("renderer.render_layout", return_value=nullcontext()) as render_layout:
            with patch("renderer.render_block", return_value=nullcontext()):
                render_group(parent, blocks, lambda block: None)

        collapse_by_key = {
            call.kwargs["key"]: call.kwargs["collapse_on_mobile"]
            for call in render_layout.call_args_list
        }
        self.assertEqual(collapse_by_key, {
            "lab-group-parent": True,
            "lab-group-child": False,
        })

    def test_groups_reject_duplicate_or_missing_children(self):
        with self.assertRaisesRegex(ValueError, "repetir"):
            Group("duplicate", ("principal", "principal"), Layout())
        with self.assertRaisesRegex(ValueError, "claves de grupo"):
            Group(
                "root",
                (Group("child", ("first",), Layout()), Group("child", ("second",), Layout())),
                Layout(),
            )
        group = Group("missing", ("unknown",), Layout())
        with self.assertRaisesRegex(ValueError, "inexistente"):
            group.validate({})


if __name__ == "__main__":
    unittest.main()
