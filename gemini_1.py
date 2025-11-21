import google.generativeai as genai
import re

# ------------ CONFIG ------------
GEMINI_API_KEY = "AIzaSyANlEs76iicpzEfv4EUo3WQF5zmJRzcya8"
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


# ------------ 1. AUDIO → ENGLISH ------------
def audio_to_english(audio_path):
    if not audio_path:
        raise ValueError("audio_path is missing")

    uploaded = genai.upload_file(audio_path)

    response = model.generate_content([
        uploaded,
        "Transcribe deeply and translate to English. Output only clean English text."
    ])

    return (response.text or "").strip()


# ------------ 2. TANGLISH → ENGLISH ------------
def tanglish_to_english(text):
    response = model.generate_content(
        f"Convert this Tanglish to proper English:\n{text}"
    )
    return (response.text or "").strip()


# ------------ 3. MERGE TEXTS ------------
def deduplicate_and_label(text_comment, audio_text):
    t1 = set([s.strip() for s in (text_comment or "").split(".") if s.strip()])
    t2 = set([s.strip() for s in (audio_text or "").split(".") if s.strip()])

    return (
        "Text Comment:\n" + ". ".join(t1) +
        "\n\nAudio Transcript:\n" + ". ".join(t2)
    )


# ------------ 4. SUMMARY (5 POINTS) ------------
def generate_selection_summary(combined_text):
    prompt = (
        "Summarize in EXACTLY 5 bullet points. One line each. "
        "No title, no numbering.\n\n" + combined_text
    )

    response = model.generate_content(prompt)
    raw = (response.text or "").strip().split("\n")

    points = []
    for line in raw:
        line = line.strip("-•* ").strip()
        if line:
            points.append(line)

    while len(points) < 5:
        points.append("Additional detail not provided.")

    return points[:5]


# ------------ 5. SELECTION PREDICTION ------------
def predict_selection(combined_text):
    prompt = f"""
Rule:
- If the student is poor → SELECT
- If financially stable → DO NOT SELECT
- If unclear → ON HOLD

Return in this exact format:

Selection Decision: SELECT / DO NOT SELECT / ON HOLD
Reason: <one line>

Text:
{combined_text}
    """

    response = model.generate_content(prompt)
    txt = (response.text or "").lower()

    if "select" in txt and "do not" not in txt:
        return "SELECT"
    if "do not select" in txt or "not select" in txt:
        return "DO NOT SELECT"
    if "hold" in txt:
        return "ON HOLD"

    return "ON HOLD"


# ------------ 6. SENTIMENT SCORE ------------
def sentiment_score(combined_text):
    prompt = (
        "Give sentiment score 0–1 for this text. Return ONLY the number.\n\n"
        + combined_text
    )

    response = model.generate_content(prompt)
    raw = (response.text or "").strip()

    m = re.search(r"0\.\d+|1\.0+|1", raw)
    return float(m.group(0)) if m else 0.5

