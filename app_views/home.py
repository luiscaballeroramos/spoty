import streamlit as st


def render_home_page(on_reproduction_click, on_summary_click):
    st.markdown(
        """
        <style>
        div[data-testid=\"stButton\"] > button[kind=\"primary\"] {
            width: 100%;
            min-height: 9rem;
            font-size: rem;
            font-weight: 700;
            border-radius: 1rem;
            border: 1px solid rgba(49, 51, 63, 0.25);
        }

        @media (max-width: 768px) {
            div[data-testid="stHorizontalBlock"]:has(button[kind="primary"]) {
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                gap: 0.6rem !important;
            }

            div[data-testid="stHorizontalBlock"]:has(button[kind="primary"]) > div {
                flex: 1 1 0 !important;
                min-width: 0 !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("SPOTY")
    st.subheader("Funcionalidades principales")
    st.write("")

    col_reproduction, col_summary = st.columns(2, gap="small")

    with col_reproduction:
        if st.button(
            "⏯️",
            key="home_reproduction",
            type="primary",
            use_container_width=True,
        ):
            on_reproduction_click()

    with col_summary:
        if st.button(
            "📊",
            key="home_summary",
            type="primary",
            use_container_width=True,
        ):
            on_summary_click()
