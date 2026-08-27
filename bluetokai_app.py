import streamlit as st
import pandas as pd
import difflib
import re
import csv
import os
import random
from datetime import datetime

# ---------- CONFIG ----------
GOOGLE_FORM_URL = "REPLACE_WITH_YOUR_BLUE_TOKAI_SURVEY_FORM_URL"

# Entry IDs below are placeholders - once you build your real Google Form, get
# these from Google Forms' "Get pre-filled link" feature (see instructions).
FORM_ENTRY_IDS = {
    "product": "entry.100001",
    "score": "entry.100002",
    "price": "entry.100003",
}


def build_survey_url(product_name=None, score=None, price=None):
    """Constructs a pre-filled survey URL carrying session metadata, so each
    response can be tied back to exactly what was recommended - useful for
    the SPSS regression stage later."""
    import urllib.parse
    if GOOGLE_FORM_URL.startswith("REPLACE_"):
        return GOOGLE_FORM_URL  # not yet configured, just use as-is
    params = {"usp": "pp_url"}
    if product_name:
        params[FORM_ENTRY_IDS["product"]] = product_name
    if score is not None:
        params[FORM_ENTRY_IDS["score"]] = str(score)
    if price is not None:
        params[FORM_ENTRY_IDS["price"]] = str(price)
    return f"{GOOGLE_FORM_URL}?{urllib.parse.urlencode(params)}"
LOG_FILE = "interaction_log.csv"
RATING_LOG_FILE = "ratings_log.csv"

WELCOME_MESSAGE = (
    "Hi! ☕ I'm your Blue Tokai taste concierge — think of me as a knowledgeable "
    "barista who only works with Blue Tokai coffee, here to help you find exactly "
    "the right cup.\n\n"
    "Tell me things like:\n"
    "- *\"Something fruity and light\"*\n"
    "- *\"I want a strong dark roast for espresso\"*\n"
    "- *\"A capsule that works with milk\"*\n"
    "- *\"Something under ₹500\"*\n\n"
    "Not sure what to ask? Tap one of the examples below."
)

QUICK_START_PROMPTS = [
    "Something fruity and light",
    "Strong dark roast for espresso",
    "A capsule with milk",
    "Something under ₹500",
    "Best seller / most popular",
]

FLAVOR_KEYWORDS = [
    "chocolate", "cocoa", "dark chocolate", "nutty", "hazelnut", "almond",
    "fruity", "berry", "cherry", "grapes", "citrus", "orange", "apple",
    "floral", "jasmine", "caramel", "honey", "malt", "spice", "cinnamon",
    "mocha", "banana", "peach", "plum", "melon", "pomegranate",
]

ROAST_KEYWORDS = {
    "very dark": ["very dark", "french roast", "extra dark"],
    "medium-dark": ["medium-dark", "medium dark"],
    "light": ["light", "delicate", "bright"],
    "medium": ["medium", "balanced"],
    "dark": ["dark", "bold", "strong", "intense"],
}

FORMAT_KEYWORDS = {
    "capsule": ["capsule", "pod", "nespresso"],
    "ground": ["ground", "whole bean", "beans", "powder"],
    "easy pour": ["easy pour", "drip", "sachet", "travel"],
    "cold brew bag": ["cold brew bag"],
    "cold brew can": ["can", "ready to drink", "ready-to-drink", "rtd"],
    "concentrate": ["concentrate", "drop"],
    "sampler": ["sampler", "trio", "explorer", "value pack", "gift"],
}

MILK_KEYWORDS = {
    "with milk": ["with milk", "milk", "latte", "cappuccino"],
    "black": ["black", "no milk", "without milk"],
}

BUDGET_VAGUE_KEYWORDS = ["cheap", "affordable", "budget friendly", "budget-friendly",
                          "low cost", "inexpensive", "pocket friendly", "best seller",
                          "most popular", "popular"]


# ---------- DATA ----------
@st.cache_data
def load_data():
    df = pd.read_csv("blue_tokai_products.csv")
    return df


products = load_data()
in_stock = products[products["Availability"] == "In Stock"].copy()


def _best_fuzzy_match(text, candidates, cutoff=0.72):
    text_l = text.lower()
    words = text_l.split()
    best, best_score = None, 0.0
    for candidate in candidates:
        cand_l = candidate.lower()
        score = difflib.SequenceMatcher(None, text_l, cand_l).ratio()
        if score > best_score:
            best_score, best = score, candidate
        for w in words:
            score2 = difflib.SequenceMatcher(None, w, cand_l.replace(" ", "")).ratio()
            if score2 > best_score:
                best_score, best = score2, candidate
    return (best, best_score) if best_score >= cutoff else (None, 0.0)


def extract_preferences(text):
    text_l = text.lower()
    prefs = {}

    # roast level
    for roast, keywords in ROAST_KEYWORDS.items():
        if any(k in text_l for k in keywords):
            prefs["roast"] = roast
            break

    # flavor
    matched_flavors = [f for f in FLAVOR_KEYWORDS if f in text_l]
    if matched_flavors:
        prefs["flavors"] = matched_flavors
    elif "plain coffee" in text_l or "classic, plain" in text_l:
        prefs["plain"] = True

    # format
    for fmt, keywords in FORMAT_KEYWORDS.items():
        if any(k in text_l for k in keywords):
            prefs["format"] = fmt
            break

    # milk - check black/no-milk FIRST, since "no milk" contains the substring
    # "milk" which would otherwise incorrectly trigger the "with milk" match
    if any(k in text_l for k in MILK_KEYWORDS["black"]):
        prefs["milk"] = "black"
    elif any(k in text_l for k in MILK_KEYWORDS["with milk"]):
        prefs["milk"] = "with milk"

    # budget
    budget_match = re.search(r"(?:under|below|less than|within)\s*(?:rs\.?|₹)?\s*(\d+)", text_l)
    range_match = re.search(r"(\d+)\s*(?:to|-|and)\s*(?:rs\.?|₹)?\s*(\d+)", text_l)
    above_match = re.search(r"above\s*(?:rs\.?|₹)?\s*(\d+)", text_l)
    bare_price = re.search(r"(?:₹\s*(\d+)|(\d+)\s*(?:rs\.?|rupees?))\b", text_l)
    if range_match:
        prefs["budget_min"] = int(range_match.group(1))
        prefs["budget"] = int(range_match.group(2))
    elif above_match:
        prefs["budget_min"] = int(above_match.group(1))
    elif budget_match:
        prefs["budget"] = int(budget_match.group(1))
    elif bare_price:
        prefs["budget"] = int(bare_price.group(1) or bare_price.group(2))
    elif any(k in text_l for k in BUDGET_VAGUE_KEYWORDS):
        prefs["vague_budget"] = True

    # estate/region name match (broad geography)
    estate, score = _best_fuzzy_match(text, products["Estate_Region"].unique().tolist())
    if estate:
        prefs["estate"] = estate

    # specific product name match (e.g. "Attikan Estate", "Sampigehoney Estate")
    product_name, score2 = _best_fuzzy_match(text, products["Product_Name"].unique().tolist(), cutoff=0.75)
    if product_name:
        prefs["product_name"] = product_name

    return prefs


def score_product(row, prefs):
    """Weighted compatibility score (0-100), never a hard dead end - always
    returns a ranked list, following the same 'no dead ends' philosophy the
    reference blueprint used, in place of rigid boolean filtering."""
    score = 0.0
    weight_total = 0.0

    if "roast" in prefs:
        weight_total += 0.30
        if row["Roast_Level"].lower() == prefs["roast"]:
            score += 0.30
        elif prefs["roast"] in row["Roast_Level"].lower() or row["Roast_Level"].lower() in prefs["roast"]:
            score += 0.20

    if "flavors" in prefs:
        weight_total += 0.25
        row_flavor_l = row["Flavor_Notes"].lower()
        matched = sum(1 for f in prefs["flavors"] if f in row_flavor_l)
        if matched:
            score += 0.25 * min(1.0, matched / len(prefs["flavors"]))
    elif prefs.get("plain"):
        # genuinely reward classic, traditional coffee - not just short text.
        # Exclude inherently flavored/novelty formats (flavored cold brew cans,
        # concentrates, variety samplers), which are the OPPOSITE of "plain"
        # even when their descriptions happen to be short.
        weight_total += 0.25
        row_format_l = row["Format"].lower()
        is_traditional_format = ("ground" in row_format_l or "easy pour" in row_format_l
                                  or "capsule" in row_format_l)
        is_novelty = ("cold brew can" in row_format_l or "concentrate" in row_format_l
                      or "sampler" in row_format_l or "value pack" in row_format_l)
        num_descriptors = len(row["Flavor_Notes"].split(","))
        if is_novelty:
            score += 0.0  # flavored/novelty items don't fit a "plain" request at all
        elif is_traditional_format and num_descriptors <= 2:
            score += 0.25
        elif is_traditional_format and num_descriptors == 3:
            score += 0.18
        elif is_traditional_format:
            score += 0.10
        else:
            score += 0.05

    if "format" in prefs:
        weight_total += 0.20
        row_format_l = row["Format"].lower()
        fmt_map = {
            "capsule": "capsule", "ground": "ground/whole bean", "easy pour": "easy pour",
            "cold brew bag": "cold brew bag", "cold brew can": "cold brew can",
            "concentrate": "drop", "sampler": "sampler",
        }
        target = fmt_map.get(prefs["format"], "")
        if target in row_format_l:
            score += 0.20

    if "budget" in prefs:
        weight_total += 0.15
        if row["Price_INR"] <= prefs["budget"]:
            score += 0.15
        else:
            # partial credit if reasonably close, avoiding a hard dead end
            overage = (row["Price_INR"] - prefs["budget"]) / max(prefs["budget"], 1)
            score += max(0, 0.15 * (1 - overage))

    if "budget_min" in prefs:
        weight_total += 0.05
        if row["Price_INR"] >= prefs["budget_min"]:
            score += 0.05

    if "estate" in prefs:
        weight_total += 0.10
        if row["Estate_Region"] == prefs["estate"]:
            score += 0.10

    if "product_name" in prefs:
        weight_total += 0.40
        if row["Product_Name"] == prefs["product_name"]:
            score += 0.40

    if weight_total == 0:
        return 50.0  # neutral score when no specific preference given
    return round((score / weight_total) * 100, 1)


def get_recommendations(prefs, top_n=5):
    df = in_stock.copy()
    df["compatibility_score"] = df.apply(lambda row: score_product(row, prefs), axis=1)
    return df.sort_values("compatibility_score", ascending=False).head(top_n)


def format_price(row):
    return f"₹{int(row['Price_INR'])} for {row['Format'].split('(')[0].strip()}"


def build_reason_text(prefs):
    parts = []
    if "roast" in prefs:
        parts.append(f"{prefs['roast']} roast")
    if "flavors" in prefs:
        parts.append(f"{', '.join(prefs['flavors'])} notes")
    elif prefs.get("plain"):
        parts.append("a plain, classic coffee - no fancy flavor notes")
    if "format" in prefs:
        parts.append(f"{prefs['format']} format")
    if "milk" in prefs:
        parts.append(prefs["milk"])
    if "budget" in prefs:
        parts.append(f"within ₹{prefs['budget']}")
    if "budget_min" in prefs:
        parts.append(f"above ₹{prefs['budget_min']}")
    if "estate" in prefs:
        parts.append(f"from {prefs['estate']}")
    if "product_name" in prefs:
        parts.append(f"specifically {prefs['product_name']}")
    if not parts:
        return "your general taste"
    return ", ".join(parts)


def log_interaction(text, prefs, num_matches):
    is_new = not os.path.exists(LOG_FILE)
    try:
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["timestamp", "message", "preferences", "num_matches"])
            writer.writerow([datetime.now().isoformat(timespec="seconds"), text, str(prefs), num_matches])
    except OSError:
        pass


def log_rating(product_name, stars):
    is_new = not os.path.exists(RATING_LOG_FILE)
    try:
        with open(RATING_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["timestamp", "recommended_product", "stars"])
            writer.writerow([datetime.now().isoformat(timespec="seconds"), product_name, stars])
    except OSError:
        pass


def process_message(text):
    st.session_state["messages"] = []
    st.session_state["messages"].append(("user", text, None, None))

    prefs = extract_preferences(text)
    matches = get_recommendations(prefs, top_n=5)
    top = matches.iloc[0]
    others = matches.iloc[1:5]

    reason = build_reason_text(prefs)
    reply = f"Matched because you wanted: {reason}."

    st.session_state["last_recommended_product"] = f"Blue Tokai — {top['Product_Name']}"
    st.session_state["last_recommended_score"] = top["compatibility_score"]
    st.session_state["last_recommended_price"] = int(top["Price_INR"])
    st.session_state["has_had_response"] = True
    log_interaction(text, prefs, len(matches))
    st.session_state["messages"].append(("assistant", reply, top, others))

    st.session_state.setdefault("search_history", [])
    st.session_state["search_history"].append({
        "query": text, "reply": reply, "top": top, "others": others,
    })


# ---------- UI ----------
st.set_page_config(page_title="Blue Tokai Concierge", page_icon="☕")

# Hidden admin dashboard
ADMIN_SECRET = "bluetokai2026"
query_params = st.query_params
if query_params.get("admin") == ADMIN_SECRET:
    st.title("☕ Blue Tokai Concierge — Admin Dashboard")
    st.caption("Hidden view for capstone data collection - not linked anywhere in the normal chat.")
    st.divider()
    st.subheader("⭐ Ratings")
    if os.path.exists(RATING_LOG_FILE):
        ratings_df = pd.read_csv(RATING_LOG_FILE)
        if not ratings_df.empty:
            col1, col2 = st.columns(2)
            col1.metric("Total ratings", len(ratings_df))
            col2.metric("Average stars", f"{ratings_df['stars'].mean():.1f} ⭐")
            st.dataframe(ratings_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)
            st.download_button("Download ratings CSV", ratings_df.to_csv(index=False), "ratings_log.csv", "text/csv")
    else:
        st.info("No ratings yet.")
    st.divider()
    st.subheader("💬 Interactions")
    if os.path.exists(LOG_FILE):
        interactions_df = pd.read_csv(LOG_FILE)
        if not interactions_df.empty:
            st.metric("Total interactions", len(interactions_df))
            st.dataframe(interactions_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)
            st.download_button("Download interactions CSV", interactions_df.to_csv(index=False), "interaction_log.csv", "text/csv")
    else:
        st.info("No interactions yet.")
    st.stop()

st.title("☕ Blue Tokai Concierge")
st.caption("Your personal Blue Tokai taste concierge — one brand, real recommendations.")

with st.expander("🔍 Or answer 4 quick questions", expanded=True):
    with st.form(key="filter_form"):
        st.markdown("**1. How do you brew your coffee?**")
        sel_format = st.radio(
            "Brew method", ["Any", "Ground/Whole Bean", "Capsule", "Easy Pour", "Cold Brew", "Concentrate/Drop", "Ready-to-Drink Can"],
            key="filter_format", label_visibility="collapsed", horizontal=True)

        st.markdown("**2. What flavor do you crave?**")
        sel_flavor = st.radio(
            "Flavor", ["Any", "Plain / Classic (No Specific Flavor)", "Chocolate & Cocoa", "Fruity & Berry", "Nutty & Hazelnut", "Floral & Citrus", "Caramel & Honey"],
            key="filter_flavor", label_visibility="collapsed", horizontal=True)

        st.markdown("**3. Black or with milk?**")
        sel_milk = st.radio("Milk", ["Any", "With Milk", "Black (No Milk)"], key="filter_milk", label_visibility="collapsed", horizontal=True)

        st.markdown("**4. Roast preference?**")
        sel_roast = st.radio("Roast", ["Any", "Light", "Medium", "Medium-Dark", "Dark"], key="filter_roast", label_visibility="collapsed", horizontal=True)

        filter_submitted = st.form_submit_button("✨ Find my match")
    if filter_submitted:
        parts = []
        format_map = {
            "Ground/Whole Bean": "ground", "Capsule": "capsule", "Easy Pour": "easy pour",
            "Cold Brew": "cold brew bag", "Concentrate/Drop": "concentrate", "Ready-to-Drink Can": "cold brew can",
        }
        if sel_format != "Any":
            parts.append(format_map.get(sel_format, sel_format.lower()))
        flavor_map = {
            "Chocolate & Cocoa": "chocolate", "Fruity & Berry": "fruity", "Nutty & Hazelnut": "nutty",
            "Floral & Citrus": "citrus", "Caramel & Honey": "caramel",
        }
        if sel_flavor not in ("Any", "Plain / Classic (No Specific Flavor)"):
            parts.append(flavor_map.get(sel_flavor, sel_flavor.lower()))
        elif sel_flavor == "Plain / Classic (No Specific Flavor)":
            parts.append("classic, plain coffee")
        if sel_milk == "With Milk":
            parts.append("with milk")
        elif sel_milk == "Black (No Milk)":
            parts.append("black")
        if sel_roast != "Any":
            parts.append(f"{sel_roast.lower()} roast")
        summary_text = "Guided search: " + ", ".join(parts) if parts else "Guided search: any coffee"
        process_message(summary_text)
        st.rerun()

if "messages" not in st.session_state:
    st.session_state["messages"] = [("assistant", WELCOME_MESSAGE, None, None)]
if "conversation_rated" not in st.session_state:
    st.session_state["conversation_rated"] = False
if "last_recommended_product" not in st.session_state:
    st.session_state["last_recommended_product"] = None

for idx, (role, content, top, others) in enumerate(st.session_state["messages"]):
    with st.chat_message(role):
        st.markdown(content)
        if top is not None:
            st.markdown(
                "<div style='border:2px solid #2C1810; border-radius:10px; padding:16px; margin:10px 0;'>",
                unsafe_allow_html=True
            )
            st.markdown("### 🎯 This is your result")
            img_col, info_col = st.columns([1, 2])
            with img_col:
                if pd.notna(top.get("Image_URL")):
                    st.image(top["Image_URL"], use_container_width=True)
            with info_col:
                st.markdown(f"**Blue Tokai — {top['Product_Name']}**")
                st.markdown(
                    f"{top['Roast_Level']} roast, {top['Format'].split('(')[0].strip()}\n\n"
                    f"Flavor: {top['Flavor_Notes']}\n\n"
                    f"**{format_price(top)}**"
                )
                st.success(f"✅ {top['compatibility_score']}% Compatibility Match")
            st.markdown("</div>", unsafe_allow_html=True)
        if others is not None and not others.empty:
            st.caption(f"🔍 {len(others)} other option(s) that also fit well:")
            cols = st.columns(min(len(others), 4))
            for col, (_, prow) in zip(cols, others.iterrows()):
                with col:
                    if pd.notna(prow.get("Image_URL")):
                        st.image(prow["Image_URL"], use_container_width=True)
                    st.caption(f"**{prow['Product_Name']}**\n{format_price(prow)}\n{prow['compatibility_score']}% match")

if len(st.session_state["messages"]) == 1:
    st.write("Try one of these:")
    cols = st.columns(len(QUICK_START_PROMPTS))
    for col, prompt in zip(cols, QUICK_START_PROMPTS):
        if col.button(prompt, use_container_width=True):
            process_message(prompt)
            st.rerun()

with st.form(key="user_message_form", clear_on_submit=True):
    user_input = st.text_input("Ask me anything about Blue Tokai coffee:",
                                placeholder="e.g. Something fruity and light for pour-over")
    submitted = st.form_submit_button("Send")
if submitted and user_input:
    process_message(user_input)
    st.rerun()

history = st.session_state.get("search_history", [])
past_searches = history[:-1] if len(history) > 1 else []
if past_searches:
    with st.expander(f"📜 Search History ({len(past_searches)} earlier search{'es' if len(past_searches) != 1 else ''})"):
        for i, entry in enumerate(reversed(past_searches), start=1):
            st.markdown(f"**{i}. You asked:** {entry['query']}")
            st.markdown(entry["reply"])
            st.divider()

st.divider()

if st.session_state["last_recommended_product"] and not st.session_state["conversation_rated"]:
    st.markdown(f"I hope *{st.session_state['last_recommended_product']}* is exactly what you were looking for! ☕")
    st.caption("Before you go — how helpful was this chat overall?")
    star_cols = st.columns(5)
    for star_n, scol in enumerate(star_cols, start=1):
        if scol.button("⭐" * star_n, key=f"rate_{star_n}"):
            log_rating(st.session_state["last_recommended_product"], star_n)
            st.session_state["conversation_rated"] = True
            st.rerun()
elif st.session_state["conversation_rated"]:
    st.caption("Thanks for rating this chat! 🙏")

survey_url = build_survey_url(
    product_name=st.session_state.get("last_recommended_product"),
    score=st.session_state.get("last_recommended_score"),
    price=st.session_state.get("last_recommended_price"),
)
st.markdown(f"Enjoyed the recommendations? [Share quick feedback here]({survey_url}) — it takes 1 minute.")
