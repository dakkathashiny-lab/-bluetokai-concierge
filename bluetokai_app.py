import streamlit as st
import pandas as pd
import difflib
import re
import csv
import os
from datetime import datetime

# ---------- CONFIG ----------
GOOGLE_FORM_URL = "REPLACE_WITH_YOUR_BLUE_TOKAI_SURVEY_FORM_URL"
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
    "light-medium": ["light-medium", "light medium"],
    "light": ["light", "delicate", "bright"],
    "medium": ["medium", "balanced"],
    "dark": ["dark", "bold", "strong", "intense"],
    "mixed": ["mixed", "medley", "assorted roast"],
}

FORMAT_KEYWORDS = {
    "capsule": ["capsule", "pod", "nespresso"],
    "ground": ["ground", "whole bean", "beans", "powder", "filter"],
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

FORMAT_TARGET_MAP = {
    "capsule": "capsule", "ground": "ground/whole bean", "easy pour": "easy pour",
    "cold brew bag": "cold brew bag", "cold brew can": "cold brew can",
    "concentrate": "drop", "sampler": "sampler",
}


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
        row_roast_l = row["Roast_Level"].lower()
        if row_roast_l == prefs["roast"]:
            score += 0.30
        elif prefs["roast"] != "medium" and prefs["roast"] != "dark" and (
            (prefs["roast"] in row_roast_l and prefs["roast"] not in ("light", "medium", "dark"))
            or (row_roast_l in prefs["roast"] and row_roast_l not in ("light", "medium", "dark"))
        ):
            # Only give partial credit for genuinely related compound roasts
            # (e.g. "medium-dark" vs "very dark"), not plain substring hits like
            # "medium" matching inside "light-medium" or "medium-dark".
            score += 0.20

    if "flavors" in prefs:
        weight_total += 0.25
        row_flavor_l = row["Flavor_Notes"].lower()
        matched = sum(1 for f in prefs["flavors"] if f in row_flavor_l)
        if matched:
            score += 0.25 * min(1.0, matched / len(prefs["flavors"]))

    if "format" in prefs:
        weight_total += 0.20
        row_format_l = row["Format"].lower()
        target = FORMAT_TARGET_MAP.get(prefs["format"], "")
        if target in row_format_l:
            score += 0.20

    if "milk" in prefs:
        # No explicit milk-pairing column exists, so use roast family as the
        # best available proxy: darker roasts are conventionally recommended
        # with milk, lighter roasts are conventionally recommended black.
        weight_total += 0.10
        roast_l = row["Roast_Level"].lower()
        dark_family = "dark" in roast_l
        light_family = "light" in roast_l
        if prefs["milk"] == "with milk" and dark_family:
            score += 0.10
        elif prefs["milk"] == "black" and light_family:
            score += 0.10
        else:
            score += 0.05  # never a hard dead end

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

    if prefs.get("vague_budget"):
        # "cheap" / "budget friendly" / "best seller" / "most popular" - with no
        # popularity data available, use price as the best available proxy and
        # favor lower-priced items.
        weight_total += 0.15
        max_price = products["Price_INR"].max()
        if max_price > 0:
            score += 0.15 * (1 - (row["Price_INR"] / max_price))

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


def get_manual_filter_recommendations(roast_choice, format_choice, milk_choice, top_n=5):
    """Manual-filter dropdowns are exact structured choices, not fuzzy text, so
    they're applied as hard filters (guaranteeing e.g. Capsule really returns
    capsules) rather than blended into the fuzzy chat-scoring weights. Falls
    back to progressively looser filtering rather than ever returning empty."""
    df = in_stock.copy()
    filtered = df.copy()

    if roast_choice != "Any":
        filtered = filtered[filtered["Roast_Level"].str.lower() == roast_choice.lower()]

    if format_choice != "Any":
        target = FORMAT_TARGET_MAP.get(format_choice.lower(), format_choice.lower())
        filtered = filtered[filtered["Format"].str.lower().str.contains(target, na=False)]

    if milk_choice == "With Milk":
        filtered = filtered[filtered["Roast_Level"].str.lower().str.contains("dark", na=False)]
    elif milk_choice == "Black (No Milk)":
        filtered = filtered[filtered["Roast_Level"].str.lower().str.contains("light", na=False)]

    if filtered.empty:
        # No dead ends: relax milk first, then roast, but keep format if possible
        relaxed = df.copy()
        if format_choice != "Any":
            target = FORMAT_TARGET_MAP.get(format_choice.lower(), format_choice.lower())
            fmt_only = relaxed[relaxed["Format"].str.lower().str.contains(target, na=False)]
            if not fmt_only.empty:
                relaxed = fmt_only
        filtered = relaxed if not relaxed.empty else df.copy()

    filtered = filtered.copy()
    filtered["compatibility_score"] = 100.0

    prefs = {}
    if roast_choice != "Any":
        prefs["roast"] = roast_choice.lower()
    if format_choice != "Any":
        prefs["format"] = format_choice.lower()
    if milk_choice == "With Milk":
        prefs["milk"] = "with milk"
    elif milk_choice == "Black (No Milk)":
        prefs["milk"] = "black"

    return filtered.sort_values("Price_INR").head(top_n), prefs


def format_price(row):
    return f"₹{int(row['Price_INR'])} for {row['Format'].split('(')[0].strip()}"


def build_reason_text(prefs):
    parts = []
    if "roast" in prefs:
        parts.append(f"{prefs['roast']} roast")
    if "flavors" in prefs:
        parts.append(f"{', '.join(prefs['flavors'])} notes")
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
    st.session_state.setdefault("messages", [])
    st.session_state["messages"].append(("user", text, None))

    prefs = extract_preferences(text)
    matches = get_recommendations(prefs, top_n=5)
    top = matches.iloc[0]

    reason = build_reason_text(prefs)
    reply = (
        f"Matched because you wanted: {reason}. Here's my pick, plus a few other options that fit well too:"
    )

    st.session_state["last_recommended_product"] = f"Blue Tokai — {top['Product_Name']}"
    st.session_state["has_had_response"] = True
    log_interaction(text, prefs, len(matches))
    st.session_state["messages"].append(("assistant", reply, matches.head(5)))

    st.session_state.setdefault("search_history", [])
    st.session_state["search_history"].append({
        "query": text, "reply": reply, "products": matches.head(5),
    })


# ---------- UI ----------
st.set_page_config(page_title="Blue Tokai Concierge", page_icon="☕")

# Hidden admin dashboard
# For production, set ADMIN_SECRET in Streamlit secrets (Settings > Secrets)
# instead of relying on the hardcoded fallback below.
try:
    ADMIN_SECRET = st.secrets["ADMIN_SECRET"]
except Exception:
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

if "messages" not in st.session_state:
    st.session_state["messages"] = [("assistant", WELCOME_MESSAGE, None)]
if "conversation_rated" not in st.session_state:
    st.session_state["conversation_rated"] = False
if "last_recommended_product" not in st.session_state:
    st.session_state["last_recommended_product"] = None

with st.expander("🔍 Or filter manually", expanded=True):
    sel_roast = st.selectbox("Roast Level", ["Any"] + sorted(in_stock["Roast_Level"].unique()), key="filter_roast")
    sel_format = st.selectbox("Format", ["Any", "Capsule", "Ground", "Easy Pour", "Cold Brew Bag", "Cold Brew Can", "Concentrate", "Sampler"], key="filter_format")
    sel_milk = st.selectbox("Milk", ["Any", "With Milk", "Black (No Milk)"], key="filter_milk")
    filter_submitted = st.button("Get recommendations", key="filter_submit_button")
    if filter_submitted:
        matches, prefs = get_manual_filter_recommendations(sel_roast, sel_format, sel_milk, top_n=5)
        reason = build_reason_text(prefs)
        top = matches.iloc[0]
        reply = f"Matched because you wanted: {reason}. Here's my pick, plus a few other options that fit well too:"
        st.session_state.setdefault("messages", [])
        st.session_state["messages"].append(("user", f"Manual filter: {reason}", None))
        st.session_state["messages"].append(("assistant", reply, matches.head(5)))
        st.session_state["last_recommended_product"] = f"Blue Tokai — {top['Product_Name']}"
        st.session_state["has_had_response"] = True
        st.session_state["result_source"] = "manual"
        st.session_state["scroll_to_latest"] = True
        log_interaction(f"Manual filter: {reason}", prefs, len(matches))
        st.session_state.setdefault("search_history", [])
        st.session_state["search_history"].append({
            "query": f"Manual filter: {reason}", "reply": reply, "products": matches.head(5),
        })
        st.rerun()


def render_product_cards(product_rows):
    top_row = product_rows.iloc[0]
    other_rows = product_rows.iloc[1:]

    # Highlighted "Our Pick" card - bigger image, clearly set apart
    with st.container(border=True):
        pick_img_col, pick_info_col = st.columns([1, 2])
        with pick_img_col:
            if pd.notna(top_row.get("Image_URL")):
                st.image(top_row["Image_URL"], use_container_width=True)
        with pick_info_col:
            st.markdown(f"### ⭐ Our Pick: {top_row['Product_Name']}")
            st.markdown(
                f"**{top_row['Roast_Level']} roast** · {top_row['Format'].split('(')[0].strip()}  \n"
                f"Flavor: {top_row['Flavor_Notes']}  \n"
                f"**{format_price(top_row)}** · {top_row['compatibility_score']}% match"
            )

    # Plain, smaller cards for the remaining options
    if not other_rows.empty:
        st.caption("Other options:")
        cols = st.columns(min(len(other_rows), 4))
        for col, (_, prow) in zip(cols, other_rows.iterrows()):
            with col:
                if pd.notna(prow.get("Image_URL")):
                    st.image(prow["Image_URL"], use_container_width=True)
                st.caption(f"{prow['Product_Name']}\n{format_price(prow)}\n{prow['compatibility_score']}% match")


def render_latest_result():
    st.markdown('<div id="latest-response-anchor"></div>', unsafe_allow_html=True)
    msgs = st.session_state["messages"]
    latest_msgs = msgs[-2:] if len(msgs) >= 2 else msgs
    for role, content, product_rows in latest_msgs:
        with st.chat_message(role):
            st.markdown(content)
            if product_rows is not None and not product_rows.empty:
                render_product_cards(product_rows)


def trigger_scroll_to_result():
    st.markdown("""
        <script>
        (function() {
            function getDoc() {
                try { if (window.parent && window.parent.document) return window.parent.document; } catch (e) {}
                return document;
            }
            function tryScroll(attemptsLeft) {
                const doc = getDoc();
                const anchor = doc.getElementById("latest-response-anchor");
                if (anchor) {
                    anchor.scrollIntoView({behavior: "smooth", block: "start"});
                    return;
                }
                if (attemptsLeft > 0) {
                    setTimeout(function() { tryScroll(attemptsLeft - 1); }, 200);
                }
            }
            tryScroll(15);
        })();
        </script>
    """, unsafe_allow_html=True)


# If the manual filter produced the most recent result, show it right here -
# directly under the filter you just used, and auto-scroll down to it.
if st.session_state.get("result_source") == "manual":
    st.divider()
    render_latest_result()
    if st.session_state.get("scroll_to_latest"):
        st.session_state["scroll_to_latest"] = False
        trigger_scroll_to_result()

# quick-start buttons only shown before the user has typed anything
if len(st.session_state["messages"]) == 1:
    st.write("Try one of these:")
    cols = st.columns(len(QUICK_START_PROMPTS))
    for col, prompt in zip(cols, QUICK_START_PROMPTS):
        if col.button(prompt, use_container_width=True):
            process_message(prompt)
            st.session_state["result_source"] = "chat"
            st.session_state["scroll_to_latest"] = True
            st.rerun()

# Plain, universally-compatible input box (works on every Streamlit version
# and every device, including mobile) - no special widgets that could fail
# to render if an older Streamlit version got installed on deploy.
with st.form(key="user_message_form", clear_on_submit=True):
    user_input = st.text_input("Ask me anything about Blue Tokai coffee:",
                                placeholder="e.g. Something fruity and light for pour-over")
    submitted = st.form_submit_button("Send")
if submitted and user_input:
    process_message(user_input)
    st.session_state["result_source"] = "chat"
    st.session_state["scroll_to_latest"] = True
    st.rerun()

st.divider()

# If the chat box (or a quick-start button) produced the most recent result,
# show it right here - directly under the chat box, and auto-scroll to it.
if st.session_state.get("result_source", "chat") == "chat":
    render_latest_result()
    if st.session_state.get("scroll_to_latest"):
        st.session_state["scroll_to_latest"] = False
        trigger_scroll_to_result()

# collapsible search history - positioned after the chat history
history = st.session_state.get("search_history", [])
past_searches = history[:-1] if len(history) > 1 else []
if past_searches:
    with st.expander(f"📜 Search History ({len(past_searches)} earlier search{'es' if len(past_searches) != 1 else ''})"):
        for i, entry in enumerate(reversed(past_searches), start=1):
            st.markdown(f"**{i}. You asked:** {entry['query']}")
            st.markdown(entry["reply"])
            if entry["products"] is not None and not entry["products"].empty:
                render_product_cards(entry["products"])
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

st.markdown(f"Enjoyed the recommendations? [Share quick feedback here]({GOOGLE_FORM_URL}) — it takes 1 minute.")
