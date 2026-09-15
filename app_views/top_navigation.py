import streamlit as st


def render_top_navigation(
    current_page: str,
    on_home_click,
    on_summary_click,
    on_reproduction_click,
):
    st.markdown(
        """
        <style>
        div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"]) button[kind="secondary"] {
            font-size: 1.5rem;
            min-height: 2.6rem;
            padding-top: 0.2rem;
            padding-bottom: 0.2rem;
        }

        @media (max-width: 768px) {
            div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"]) {
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                gap: 0.35rem !important;
            }

            div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"]) > div {
                flex: 1 1 0 !important;
                min-width: 0 !important;
            }

            div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"]) button[kind="secondary"] {
                font-size: 1.35rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col_home, col_summary, col_reproduction = st.columns(3, gap=None)

    with col_home:
        if st.button(
            "🏠 Inicio",
            key="top_nav_home",
            help="Inicio",
            type="secondary",
            use_container_width=True,
        ):
            on_home_click()

    with col_summary:
        if st.button(
            "📊 Summary",
            key="top_nav_summary",
            help="Summary",
            type="secondary",
            use_container_width=True,
        ):
            on_summary_click()

    with col_reproduction:
        if st.button(
            "⏯️ Reproducción",
            key="top_nav_reproduction",
            help="Reproduction",
            type="secondary",
            use_container_width=True,
        ):
            on_reproduction_click()

