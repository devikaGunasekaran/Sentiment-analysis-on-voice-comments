# pv_graph.py
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional


class PVState(TypedDict, total=False):
    text_comment: str
    audio_path: Optional[str]
    is_tanglish: bool

    english_from_tanglish: str
    english_from_audio: str
    merged_text: str

    summary: list
    decision: str
    reason: str
    score: float


# --------------------- NODES ----------------------
def node_tanglish_to_english(state: PVState):
    from gemini_1 import tanglish_to_english

    text = state.get("text_comment", "")

    # If no text provided, return "no text"
    if not text or text.strip() == "":
        return {"english_from_tanglish": "no text"}

    # Otherwise process normally
    result = tanglish_to_english(text)
    return {"english_from_tanglish": result}


def node_audio_to_english(state: PVState):
    from gemini_1 import audio_to_english

    audio_path = state.get("audio_path")
    if not audio_path:
        print("⚠️ No audio provided — skipping audio node")
        return {"english_from_audio": ""}

    result = audio_to_english(audio_path)
    return {"english_from_audio": result}


def node_merge(state: PVState):
    from gemini_1 import deduplicate_and_label

    merged = deduplicate_and_label(
        state.get("english_from_tanglish", ""),
        state.get("english_from_audio", "")
    )

    return {"merged_text": merged}


def node_summary(state: PVState):
    from gemini_1 import generate_selection_summary

    summary_points = generate_selection_summary(
        state.get("merged_text", "")
    )

    return {"summary": summary_points}


def node_predict(state: PVState):
    from gemini_1 import predict_selection

    result = predict_selection(state["merged_text"])

    # result is a string like:
    # "SELECT"
    # "DO NOT SELECT"
    # "ON HOLD"
    return {"decision": result}


def node_score(state: PVState):
    from gemini_1 import sentiment_score

    score = sentiment_score(state["merged_text"])
    return {"score": score}


# --------------------- BUILD GRAPH ----------------------

builder = StateGraph(PVState)

builder.add_node("Tanglish", node_tanglish_to_english)
builder.add_node("Audio", node_audio_to_english)
builder.add_node("Merge", node_merge)
builder.add_node("Summary", node_summary)
builder.add_node("Predict", node_predict)
builder.add_node("Score", node_score)

builder.set_entry_point("Tanglish")

builder.add_edge("Tanglish", "Audio")
builder.add_edge("Audio", "Merge")
builder.add_edge("Merge", "Summary")
builder.add_edge("Summary", "Predict")
builder.add_edge("Predict", "Score")
builder.add_edge("Score", END)

pv_graph = builder.compile()
