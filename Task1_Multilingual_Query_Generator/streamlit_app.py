import os
import streamlit as st

from app import generate_queries


st.set_page_config(
    page_title="Multilingual Query Generator",
    page_icon="🔎",
    layout="wide"
)

st.title("Multilingual Query Understanding & Search Query Generation")

st.caption(
    "Task 1 — dynamic Hindi, Roman-Hindi, English and mixed-query processing"
)

st.caption("Backend: " + os.getenv("QUERY_BACKEND", "auto"))


# User input
query = st.text_area(
    "Enter any question",
    placeholder="Example: Green Revolution ka Indian farmers par kya impact hua?",
    height=100,
)

max_queries = 8

st.caption(
    "Automatic selection: up to eight useful Hindi and English queries. "
    "Repetitive or invalid candidates are filtered."
)


# Generate queries
if st.button(
    "Generate search queries",
    type="primary",
    disabled=not query.strip()
):
    try:
        with st.spinner("Generating search queries using the selected backend..."):
            result = generate_queries(query, max_queries)

    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    except Exception as exc:
        st.error(f"Query generation failed: {exc}")
        st.stop()

    st.session_state["last_result"] = result
    st.session_state["last_input"] = (query, max_queries)


# Display generated results
if (
    st.session_state.get("last_input") == (query, max_queries)
    and "last_result" in st.session_state
):
    result = st.session_state["last_result"]

    left, right = st.columns(2)

    with left:
        st.subheader("Understanding")

        st.write("**Detected language:**", result.detected_language)
        st.write("**Normalized:**", result.normalized_query)
        st.write("**Intent:**", result.intent)
        st.write(
            "**Key terms:**",
            ", ".join(result.key_terms) or "None detected"
        )

    with right:
        st.subheader("Language versions")

        st.write(
            "**Devanagari/Hindi:**",
            result.devanagari_query
            or "Unavailable or rejected — see explanation below"
        )

        st.write(
            "**English:**",
            result.english_query
            or "Unavailable or rejected — see explanation below"
        )

    # Ranked queries without Pandas
    st.subheader("Ranked search queries")

    if result.search_queries:
        for index, item in enumerate(result.search_queries, start=1):
            language = {
                "hi": "Hindi",
                "en": "English"
            }.get(item.language, item.language)

            st.markdown(f"**{index}. {language} — {item.kind}**")
            st.write(item.query)
    else:
        st.warning("No valid search queries were generated.")

    # Download JSON for Task 2
    st.download_button(
        label="Download Task 2 JSON",
        data=result.model_dump_json(indent=2),
        file_name="task2_queries.json",
        mime="application/json"
    )

    # Warnings
    if result.warnings:
        st.subheader("Warnings")

        for warning in result.warnings:
            st.warning(warning)

    # Rejected candidates without Pandas
    rejected = result.diagnostics.get("rejected_candidates", [])

    if rejected:
        with st.expander("Rejected queries — diagnostic details"):
            for index, item in enumerate(rejected, start=1):
                st.write(f"**Rejected candidate {index}**")
                st.json(item)

    # Complete API output
    with st.expander("Complete API response"):
        st.json(result.model_dump())
