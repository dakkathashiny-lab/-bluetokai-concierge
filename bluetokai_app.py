import streamlit as st
import pandas as pd
import difflib
import re
import csv
import os
import uuid
import json
import time
import requests
from datetime import datetime

# ---------- CONFIG ----------
GOOGLE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSem3PmBTAEjlNH-VByzJbCh1BbZ-xAq6pSiVDYOC-v-VBE7nA/viewform"
GOOGLE_FORM_SESSION_ENTRY_ID = "entry.934150347"
LOG_FILE = "interaction_log.csv"
RATING_LOG_FILE = "ratings_log.csv"
SESSION_LOG_FILE = "session_log.csv"
SESSION_LOG_COLUMNS = [
    "session_id", "timestamp_start", "timestamp_submit", "interaction_duration_sec",
    "source", "roast_preference", "format_preference", "milk_preference",
    "flavor_keywords", "flavor_tier_preference", "budget_tier_preference",
    "matched_product_name", "price_inr", "price_tier",
    "compatibility_score_sc", "roast_match_flag", "format_match_flag",
    "num_alternatives_shown", "completed_flag",
    "recommendation_method", "llm_model_name", "llm_latency_ms", "rule_based_score_sc",
]

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

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

# Consumer-facing "how do you brew at home" options for the manual filter,
# mapped to one or more real Format substrings in the catalog. "Quick - Just
# Hot Water" covers Easy Pour and Drop - Blue Tokai doesn't sell literal
# instant coffee (confirmed against their own site), so this is labeled
# honestly as "instant-style convenience" rather than "Instant Coffee".
# Question 1 - "How do you brew your coffee?" - maps directly to real Format values.
BREW_QUESTION_TARGETS = {
    "ground": ["ground/whole bean"],
    "whole bean": ["ground/whole bean"],
    "capsule": ["capsule"],
    "easy pour": ["easy pour"],
    "cold brew": ["cold brew bag"],
    "concentrate/drop": ["drop"],
    "ready-to-drink can": ["cold brew can"],
}

# Question 2 - "What flavor do you crave?" - matched against Flavor_Notes text.
# "Plain / Classic" has no specific keyword; it means no flavor filter is applied.
FLAVOR_TIER_KEYWORDS = {
    "plain / classic (no specific flavor)": [],
    "chocolate & cocoa": ["chocolate", "cocoa"],
    "fruity & berry": ["fruit", "berry", "cherry", "plum", "grape", "peach", "apple", "raisin", "fig"],
    "nutty & hazelnut": ["nut", "hazelnut", "almond", "walnut"],
    "floral & citrus": ["floral", "citrus", "orange", "lemon", "jasmine", "flower", "mandarin"],
    "caramel & honey": ["caramel", "honey", "toffee", "butterscotch", "jaggery"],
}

# Question 5 - "Preferred budget tier per bag?" - direct Price_INR ranges, used
# to measure Willingness to Pay (WTP) for the SPSS analysis.
BUDGET_TIER_RANGES = {
    "everyday essential (under ₹500)": (0, 499),
    "classic reserve (₹500 – ₹800)": (500, 799),
    "connoisseur micro-lot (₹800+)": (800, 999999),
}

BREWING_METHOD_TARGETS = {
    "filter coffee": ["ground/whole bean", "easy pour"],
    "french press": ["ground/whole bean"],
    "moka pot / stovetop": ["ground/whole bean"],
    "espresso machine / capsule": ["capsule"],
    "quick - just hot water": ["easy pour", "drop"],
    "cold brew": ["cold brew bag", "cold brew can"],
}

# Real Blue Tokai product page slugs, verified by visiting every collection
# page directly (not guessed) - used to build a "Buy on Blue Tokai" link for
# each recommendation. Falls back to the general site if a product is ever
# added to the catalog without a matching entry here.
PRODUCT_URLS = {
    "Attikan Estate": "attikan-estate",
    "Dhak Blend": "dhak-blend",
    "Half-Caff (Yelnoorkhan Estate)": "half-caff",
    "Krishnagiri Estate": "krishnagiri-estate-dark",
    "Elkhill Estates": "elkhill-estate",
    "M.S. Estate": "m-s-estate",
    "St. Joseph Estate": "st-joseph-estate",
    "Monsoon Malabar AA - Hoysala Estate": "monsoon-malabar",
    "Sandalwood Estate": "sandalwood-estate",
    "Kalledevarapura Estate (Pulp Sun Dried)": "kalledevarapura-pulp-sun-dried",
    "Unakki Estate": "unakki-estate-washed",
    "Sampigehoney Estate": "sampigehoney-estate",
    "Salawara Estate": "salawara-estate",
    "Riverdale Estate - Mosto": "riverdale-estate-mosto",
    "Baarbara Estate (Whiskey Barrel Aged)": "baarbara-estate-whiskey-barrel",
    "The Monsoon Trio": "the-monsoon-trio",
    "The Rich & Bold Trio Pack": "the-rich-bold-trio-pack",
    "Customised Sampler Pack": "customised-sampler-pack",
    "5-in-1 Explorer Pack": "5-in-1-explorer-pack",
    "Attikan Estate (Capsules)": "attikan-estate-aluminium-coffee-capsules",
    "Dhak Blend (Capsules)": "dhak-blend-aluminium-coffee-capsules",
    "Vienna Roast (Capsules)": "vienna-roast-aluminium-coffee-capsules",
    "Americano Drop": "americano-drop-specialty-coffee-concentrate",
    "Signature Drop": "signature-drop-specialty-coffee-concentrate",
    "Sea Salt Mocha Drop": "sea-salt-mocha-drop-specialty-coffee-concentrate",
    "Mixed Bag Drop": "mixed-bag-drop-specialty-coffee-concentrate",
    "Chilli Cinnamon Mocha Drop": "chilli-cinnamon-mocha-drop-specialty-coffee-concentrate",
    "Jaggery Drop": "jaggery-drop-specialty-coffee-concentrate",
    "The Sunrise Combo": "the-sunrise-combo",
    "The Mocha Mix": "the-mocha-mix",
    "Attikan Estate Easy Pour": "attikan-estate-easy-pour-coffee-sachets",
    "Vienna Dark Roast Easy Pour": "vienna-dark-roast-easy-pour-coffee-sachets",
    "Mixed Light to Dark Roasts Easy Pour": "mixed-light-to-dark-roasts",
    "Monsoon Malabar Easy Pour": "monsoon-malabar-easy-pour-coffee-sachets",
    "Seethargundu Estate Easy Pour": "seethargundu-estate-easy-pour-coffee-sachets",
    "Jacaranda Blend Easy Pour": "jacaranda-blend-easy-pour-coffee-sachets",
    "French Roast Easy Pour": "french-roast-easy-pour-coffee-sachets",
    "Cold Brew Bags - Kalledeverapura": "kalledeverapura",
    "Cold Brew Bags - Bold": "cold-brew-bag-bold",
    "Cold Brew Bags - Light Blend": "light-blend",
    "Mocha Cold Coffee": "mocha-cold-coffee",
    "Classic Cold Coffee": "classic-cold-coffee",
    "Classic Bold": "classic-bold-cold-brew-cans",
    "Classic Light": "classic-light-cold-brew-cans",
    "Coffee Cascara": "coffee-cherry",
    "Assorted 6-Pack": "assorted-6-pack",
    "Elderflower Cold Brew": "elderflower-cold-brew-can",
    "Orange Mint Cold Brew": "orange-mint-cold-brew-cans",
    "Iced Latte Cans": "iced-latte-cans",
    "New Brewer's Pack": "new-brewers-pack",
    "Silver Oak Cafe Blend": "silver-oak-cafe-blend",
    "Amaltas Blend": "amaltas-blend",
}


def get_product_url(product_name):
    slug = PRODUCT_URLS.get(product_name)
    if slug:
        return f"https://bluetokaicoffee.com/products/{slug}"
    return "https://bluetokaicoffee.com/collections/all-products-collection"


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


def _build_catalog_json():
    """The exact, closed universe of products the LLM is allowed to choose from."""
    cols = ["Product_Name", "Roast_Level", "Format", "Flavor_Notes", "Price_INR"]
    return in_stock[cols].to_dict(orient="records")


def get_llm_recommendation(user_text, prefs):
    """Layer 1: prompt instructs the model to only use the catalog below, no
    outside knowledge. Layer 2: response_format=json_object forces valid JSON.
    Layer 3 (in the caller): the returned product name is checked against the
    real catalog before ever being shown - if it fails on any front (missing
    key, no API key configured, network error, invalid name), this returns
    None and the caller falls back to the rule-based engine. This function
    never lets an untrusted or invalid result reach the user. The specific
    reason for any failure is stashed in session_state for diagnostics."""
    try:
        api_key = st.secrets["GROQ_API_KEY"]
    except Exception:
        st.session_state["last_llm_error"] = "No GROQ_API_KEY found in Streamlit Secrets."
        return None  # no key configured - silently use rule-based engine

    catalog = _build_catalog_json()
    catalog_names = {p["Product_Name"] for p in catalog}

    system_prompt = (
        "You are a coffee recommendation engine for Blue Tokai, an Indian specialty "
        "coffee brand. You must ONLY recommend a product from the exact CATALOG list "
        "given below. Do not use any outside knowledge about coffee, Blue Tokai, or "
        "any other brand or product. Do not invent, rename, resize, or modify any "
        "product name. The 'matched_product_name' field in your response MUST be "
        "copied character-for-character from the 'Product_Name' field of exactly one "
        "item in CATALOG below.\n\n"
        f"CATALOG (JSON array, {len(catalog)} items):\n{json.dumps(catalog)}\n\n"
        "Respond with ONLY a single JSON object, no other text, in this exact shape:\n"
        '{"matched_product_name": "<copied exactly from CATALOG>", '
        '"reason": "<1-2 sentence personalized explanation>", '
        '"compatibility_score": <integer 0-100>}'
    )
    user_prompt = (
        f"User's message: {user_text}\n"
        f"Extracted preferences (JSON): {json.dumps(prefs)}\n"
        "Pick the single best-matching product from CATALOG for this user."
    )

    try:
        start = time.time()
        response = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            timeout=8,
        )
        latency_ms = int((time.time() - start) * 1000)
        if response.status_code != 200:
            st.session_state["last_llm_error"] = f"HTTP {response.status_code}: {response.text[:500]}"
            return None
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        matched_name = str(parsed.get("matched_product_name", "")).strip()
        if matched_name not in catalog_names:
            st.session_state["last_llm_error"] = f"Model returned a product not in the catalog: '{matched_name}'"
            return None  # hallucinated/invalid name - fall back, never shown to user

        score = parsed.get("compatibility_score", 0)
        try:
            score = max(0.0, min(100.0, float(score)))
        except (TypeError, ValueError):
            score = 0.0

        st.session_state["last_llm_error"] = None
        return {
            "matched_product_name": matched_name,
            "reason": str(parsed.get("reason", "")).strip(),
            "compatibility_score": score,
            "llm_model_name": GROQ_MODEL,
            "llm_latency_ms": latency_ms,
        }
    except Exception as e:
        st.session_state["last_llm_error"] = f"{type(e).__name__}: {e}"
        return None  # any network/parsing failure - fall back, no crash


def get_manual_filter_recommendations(roast_choice, brew_choice, milk_choice,
                                       flavor_choice=None, budget_choice=None, top_n=5):
    """Manual-filter selections are exact structured choices, not fuzzy text, so
    Brew format / Roast / Budget are applied as hard filters (guaranteeing e.g.
    Capsule really returns capsules), with progressive relaxation so this never
    returns empty. Milk and Flavor are treated as soft preferences (used to
    rank/sort, not to exclude) since neither is a real product attribute in
    the catalog - hard-filtering on them risked contradicting an exact roast
    choice (e.g. "Medium" roast + "With Milk" is otherwise unsatisfiable)."""
    df = in_stock.copy()

    def apply_hard_filters(source, use_roast, use_brew, use_budget):
        f = source
        if use_roast and roast_choice and roast_choice != "Any" and not f.empty:
            f = f[f["Roast_Level"].str.lower() == roast_choice.lower()]
        if use_brew and brew_choice and brew_choice != "Any" and not f.empty:
            targets = BREW_QUESTION_TARGETS.get(brew_choice.lower(), [brew_choice.lower()])
            f = f[f["Format"].str.lower().apply(lambda x: any(t in x for t in targets))]
        if use_budget and budget_choice and not f.empty:
            lo, hi = BUDGET_TIER_RANGES.get(budget_choice.lower(), (0, 999999))
            f = f[(f["Price_INR"] >= lo) & (f["Price_INR"] <= hi)]
        return f

    # Try with all 3 hard filters, then relax progressively (budget first,
    # then brew format, keeping roast as long as possible) until non-empty.
    relax_order = [
        dict(use_roast=True, use_brew=True, use_budget=True),
        dict(use_roast=True, use_brew=True, use_budget=False),
        dict(use_roast=True, use_brew=False, use_budget=False),
        dict(use_roast=False, use_brew=False, use_budget=False),
    ]
    filtered = df
    for opts in relax_order:
        filtered = apply_hard_filters(df, **opts)
        if not filtered.empty:
            break

    filtered = filtered.copy()

    # Soft preferences: flavor and milk influence ranking, not exclusion.
    def flavor_score(notes):
        if not flavor_choice:
            return 0
        keywords = FLAVOR_TIER_KEYWORDS.get(flavor_choice.lower(), [])
        if not keywords or not isinstance(notes, str):
            return 0
        notes_l = notes.lower()
        return 1 if any(k in notes_l for k in keywords) else 0

    def milk_score(roast_level):
        if not isinstance(roast_level, str):
            return 0
        roast_l = roast_level.lower()
        if milk_choice == "With Milk":
            return 1 if "dark" in roast_l else 0
        elif milk_choice == "Black - No Milk":
            return 1 if "light" in roast_l else 0
        return 0

    filtered["_flavor_score"] = filtered["Flavor_Notes"].apply(flavor_score)
    filtered["_milk_score"] = filtered["Roast_Level"].apply(milk_score)
    filtered["compatibility_score"] = 100.0
    filtered = filtered.sort_values(
        ["_flavor_score", "_milk_score", "Price_INR"], ascending=[False, False, True]
    ).drop(columns=["_flavor_score", "_milk_score"])

    prefs = {}
    if roast_choice and roast_choice != "Any":
        prefs["roast"] = roast_choice.lower()
    if brew_choice and brew_choice != "Any":
        prefs["format"] = brew_choice.lower()
    if milk_choice == "With Milk":
        prefs["milk"] = "with milk"
    elif milk_choice == "Black - No Milk":
        prefs["milk"] = "black"
    if flavor_choice:
        prefs["flavor_tier"] = flavor_choice
        kws = FLAVOR_TIER_KEYWORDS.get(flavor_choice.lower(), [])
        if kws:
            prefs["flavors"] = kws[:2]  # short, readable subset for the reply text
    if budget_choice:
        prefs["budget_tier"] = budget_choice

    return filtered.head(top_n), prefs


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


def get_price_tier(price_inr):
    """Bins price into a simple ordinal tier for SPSS analysis. Thresholds are
    based on the actual spread of Price_INR in blue_tokai_products.csv."""
    if price_inr < 450:
        return "Budget"
    elif price_inr < 700:
        return "Mid"
    elif price_inr < 1000:
        return "Premium"
    return "Luxury"


def log_spss_session(source, prefs, top_row, num_alternatives, completed_flag=1,
                      recommendation_method="rule_based", llm_model_name="",
                      llm_latency_ms=0, rule_based_score_sc=None):
    """SPSS-ready session log: one row per recommendation, capturing the input
    preferences, the matched product's key output variables, and session/timing
    metadata, joinable to the post-chat Google Form survey via session_id.
    Also logs which engine produced the result (llm vs rule_based) and the
    rule-based score for direct comparison, even when the LLM's pick is shown."""
    now = datetime.now()
    timestamp_submit = now.isoformat(timespec="seconds")
    timestamp_start = st.session_state.get("timestamp_start", timestamp_submit)
    try:
        duration = (now - datetime.fromisoformat(timestamp_start)).total_seconds()
    except (ValueError, TypeError):
        duration = 0.0

    roast_match_flag = int(str(prefs.get("roast", "")).strip().lower() == str(top_row["Roast_Level"]).strip().lower())
    pref_format = str(prefs.get("format", "")).strip().lower()
    row_format_l = str(top_row["Format"]).strip().lower()
    if pref_format in BREW_QUESTION_TARGETS:
        format_match_flag = int(any(t in row_format_l for t in BREW_QUESTION_TARGETS[pref_format]))
    elif pref_format in BREWING_METHOD_TARGETS:
        format_match_flag = int(any(t in row_format_l for t in BREWING_METHOD_TARGETS[pref_format]))
    elif "format" in prefs:
        format_match_flag = int(FORMAT_TARGET_MAP.get(pref_format, "") in row_format_l)
    else:
        format_match_flag = 0

    budget_tier_preference = prefs.get("budget_tier", "")
    flavor_tier_preference = prefs.get("flavor_tier", "")

    flavor_keywords_value = ", ".join(prefs.get("flavors", []))
    if not flavor_keywords_value and flavor_tier_preference.lower().startswith("plain"):
        flavor_keywords_value = "plain"

    row = {
        "session_id": st.session_state.get("session_id", ""),
        "timestamp_start": timestamp_start,
        "timestamp_submit": timestamp_submit,
        "interaction_duration_sec": round(duration, 2),
        "source": source,
        "roast_preference": prefs.get("roast", ""),
        "format_preference": prefs.get("format", ""),
        "milk_preference": prefs.get("milk", ""),
        "flavor_keywords": flavor_keywords_value,
        "flavor_tier_preference": flavor_tier_preference,
        "budget_tier_preference": budget_tier_preference,
        "matched_product_name": top_row["Product_Name"],
        "price_inr": top_row["Price_INR"],
        "price_tier": get_price_tier(top_row["Price_INR"]),
        "compatibility_score_sc": top_row["compatibility_score"],
        "roast_match_flag": roast_match_flag,
        "format_match_flag": format_match_flag,
        "num_alternatives_shown": num_alternatives,
        "completed_flag": completed_flag,
        "recommendation_method": recommendation_method,
        "llm_model_name": llm_model_name,
        "llm_latency_ms": llm_latency_ms,
        "rule_based_score_sc": rule_based_score_sc if rule_based_score_sc is not None else top_row["compatibility_score"],
    }

    file_exists = os.path.exists(SESSION_LOG_FILE)
    needs_new_header = not file_exists
    if file_exists:
        try:
            with open(SESSION_LOG_FILE, "r", newline="", encoding="utf-8") as f:
                existing_header = next(csv.reader(f), [])
            if existing_header != SESSION_LOG_COLUMNS:
                # Schema changed since this file was created (e.g. new columns
                # added). Archive the old file instead of silently appending
                # mismatched rows, which corrupts the CSV and crashes any
                # reader (including the admin dashboard).
                archive_name = SESSION_LOG_FILE.replace(".csv", f"_archived_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
                os.rename(SESSION_LOG_FILE, archive_name)
                needs_new_header = True
        except (OSError, StopIteration):
            needs_new_header = True

    try:
        with open(SESSION_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SESSION_LOG_COLUMNS)
            if needs_new_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        pass


def process_message(text):
    st.session_state.setdefault("messages", [])
    st.session_state["messages"].append(("user", text, None))

    prefs = extract_preferences(text)
    rule_based_matches = get_recommendations(prefs, top_n=5)
    rule_based_top = rule_based_matches.iloc[0]

    llm_result = get_llm_recommendation(text, prefs)

    if llm_result is not None:
        method = "llm"
        llm_model_name = llm_result["llm_model_name"]
        llm_latency_ms = llm_result["llm_latency_ms"]
        matched_row = in_stock[in_stock["Product_Name"] == llm_result["matched_product_name"]].iloc[0].copy()
        matched_row["compatibility_score"] = llm_result["compatibility_score"]
        # LLM picks the top product; fill remaining "other option" slots from
        # the rule-based ranking (excluding the LLM's own pick) so the UI's
        # "Our Pick + other options" layout stays exactly the same.
        alt_pool = rule_based_matches[rule_based_matches["Product_Name"] != matched_row["Product_Name"]]
        matches = pd.concat([matched_row.to_frame().T, alt_pool], ignore_index=True).head(5)
        top = matched_row
        reply = llm_result["reason"] or build_reason_text(prefs)
    else:
        method = "rule_based"
        llm_model_name = ""
        llm_latency_ms = 0
        matches = rule_based_matches
        top = rule_based_top
        reason = build_reason_text(prefs)
        reply = f"Matched because you wanted: {reason}. Here's my pick, plus a few other options that fit well too:"

    st.session_state["last_recommended_product"] = f"Blue Tokai — {top['Product_Name']}"
    st.session_state["conversation_rated"] = False
    st.session_state["has_had_response"] = True
    log_interaction(text, prefs, len(matches))
    log_spss_session(
        "chat", prefs, top, len(matches) - 1,
        recommendation_method=method, llm_model_name=llm_model_name,
        llm_latency_ms=llm_latency_ms, rule_based_score_sc=rule_based_top["compatibility_score"],
    )
    st.session_state["messages"].append(("assistant", reply, matches.head(5)))

    st.session_state.setdefault("search_history", [])
    st.session_state["search_history"].append({
        "query": text, "reply": reply, "products": matches.head(5),
    })


# ---------- UI ----------
st.set_page_config(page_title="Blue Tokai Coffee Concierge", page_icon="🦚")

# Hidden admin dashboard
# For production, set ADMIN_SECRET in Streamlit secrets (Settings > Secrets)
# instead of relying on the hardcoded fallback below.
try:
    ADMIN_SECRET = st.secrets["ADMIN_SECRET"]
except Exception:
    ADMIN_SECRET = "bluetokai2026"
query_params = st.query_params
if query_params.get("admin") == ADMIN_SECRET:
    st.title("☕ Blue Tokai Coffee Concierge — Admin Dashboard")
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
            if st.button("🗑️ Clear all ratings", key="clear_ratings_btn"):
                st.session_state["confirm_clear_ratings"] = True
            if st.session_state.get("confirm_clear_ratings"):
                st.warning("This permanently deletes all ratings data. Download a backup first if you want to keep it.")
                cc1, cc2 = st.columns(2)
                if cc1.button("Yes, delete all ratings", key="confirm_clear_ratings_btn"):
                    os.remove(RATING_LOG_FILE)
                    st.session_state["confirm_clear_ratings"] = False
                    st.success("Ratings cleared. Starting fresh from now.")
                    st.rerun()
                if cc2.button("Cancel", key="cancel_clear_ratings_btn"):
                    st.session_state["confirm_clear_ratings"] = False
                    st.rerun()
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
            if st.button("🗑️ Clear all interactions", key="clear_interactions_btn"):
                st.session_state["confirm_clear_interactions"] = True
            if st.session_state.get("confirm_clear_interactions"):
                st.warning("This permanently deletes all interaction data. Download a backup first if you want to keep it.")
                ci1, ci2 = st.columns(2)
                if ci1.button("Yes, delete all interactions", key="confirm_clear_interactions_btn"):
                    os.remove(LOG_FILE)
                    st.session_state["confirm_clear_interactions"] = False
                    st.success("Interactions cleared. Starting fresh from now.")
                    st.rerun()
                if ci2.button("Cancel", key="cancel_clear_interactions_btn"):
                    st.session_state["confirm_clear_interactions"] = False
                    st.rerun()
    else:
        st.info("No interactions yet.")
    st.divider()
    st.subheader("🔧 LLM Diagnostic")
    st.caption("Shows the exact reason the last AI call failed, if it did. Click below to test the AI connection directly, right now.")
    try:
        has_key = bool(st.secrets["GROQ_API_KEY"])
    except Exception:
        has_key = False
    st.write("GROQ_API_KEY configured in Secrets:", "✅ Yes" if has_key else "❌ No")
    if st.button("🧪 Test AI connection now", key="test_llm_btn"):
        test_result = get_llm_recommendation(
            "strong dark roast for espresso",
            {"roast": "dark"},
        )
        if test_result is not None:
            st.success(f"✅ AI is working! Picked: {test_result['matched_product_name']} (model: {test_result['llm_model_name']})")
        else:
            st.error(f"❌ AI call failed. Reason: {st.session_state.get('last_llm_error', 'Unknown - no error captured.')}")
    if st.session_state.get("last_llm_error"):
        st.warning(f"Most recent AI failure reason: {st.session_state['last_llm_error']}")
    st.divider()
    st.subheader("📊 SPSS Session Log")
    st.caption("One row per recommendation, with structured research variables (roast/format/milk preference, matched product, price tier, compatibility score, match flags).")
    if os.path.exists(SESSION_LOG_FILE):
        try:
            session_df = pd.read_csv(SESSION_LOG_FILE)
        except Exception:
            session_df = None
            st.error(
                "session_log.csv appears to be corrupted (this can happen if the file's "
                "columns changed between app updates while old data was still in it). "
                "Click below to archive the broken file and start fresh - no other data is affected."
            )
            if st.button("🗑️ Archive corrupted file and start fresh", key="fix_corrupt_session"):
                archive_name = SESSION_LOG_FILE.replace(".csv", f"_archived_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
                try:
                    os.rename(SESSION_LOG_FILE, archive_name)
                    st.success("Done - session_log.csv will start clean on the next recommendation.")
                    st.rerun()
                except OSError as e:
                    st.error(f"Couldn't archive the file: {e}")
        if session_df is not None and not session_df.empty:
            col1, col2, col3 = st.columns(3)
            col1.metric("Total sessions", len(session_df))
            col2.metric("Chat vs Manual", f"{(session_df['source']=='chat').sum()} / {(session_df['source']=='manual').sum()}")
            col3.metric("Avg compatibility", f"{session_df['compatibility_score_sc'].mean():.1f}%")
            st.dataframe(session_df.sort_values("timestamp_submit", ascending=False), use_container_width=True, hide_index=True)
            st.download_button("Download session log CSV", session_df.to_csv(index=False), "session_log.csv", "text/csv")
            if st.button("🗑️ Clear all session log data", key="clear_session_btn"):
                st.session_state["confirm_clear_session"] = True
            if st.session_state.get("confirm_clear_session"):
                st.warning("This permanently deletes all session log data. Download a backup first if you want to keep it.")
                cs1, cs2 = st.columns(2)
                if cs1.button("Yes, delete all session log data", key="confirm_clear_session_btn"):
                    os.remove(SESSION_LOG_FILE)
                    st.session_state["confirm_clear_session"] = False
                    st.success("Session log cleared. Starting fresh from now.")
                    st.rerun()
                if cs2.button("Cancel", key="cancel_clear_session_btn"):
                    st.session_state["confirm_clear_session"] = False
                    st.rerun()
    else:
        st.info("No session log data yet.")
    st.stop()

st.markdown('<div id="page-top-anchor"></div>', unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .st-key-hero_banner {
        background: linear-gradient(135deg, #3D2B1F, #6F4E37 55%, #C97B3D) !important;
        border-radius: 18px !important;
        padding: 1.1rem 1.4rem !important;
        margin-bottom: 1rem !important;
        border: none !important;
    }
    .st-key-hero_banner h1, .st-key-hero_banner p {
        color: #FFFFFF !important;
    }
    .st-key-hero_banner h1 {
        font-size: 1.5rem !important;
        margin-bottom: 0.3rem !important;
        line-height: 1.25 !important;
    }
    .st-key-hero_banner p {
        font-size: 0.9rem !important;
        opacity: 0.95 !important;
        margin-bottom: 0 !important;
        line-height: 1.35 !important;
    }
    @media (max-width: 480px) {
        .st-key-hero_banner {
            padding: 0.85rem 1.1rem !important;
        }
        .st-key-hero_banner h1 {
            font-size: 1.15rem !important;
        }
        .st-key-hero_banner p {
            font-size: 0.8rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)
with st.container(key="hero_banner"):
    st.markdown("# 🦚 Blue Tokai Coffee Concierge ☕")
    st.markdown("👋 **Hi! I'm your Blue Tokai coffee taste concierge** — here to help you find exactly the right cup, from Blue Tokai's real coffee menu.")

if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
    st.session_state["timestamp_start"] = datetime.now().isoformat(timespec="seconds")
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "conversation_rated" not in st.session_state:
    st.session_state["conversation_rated"] = False
if "last_recommended_product" not in st.session_state:
    st.session_state["last_recommended_product"] = None

st.markdown(
    """
    <style>
    [data-testid="stExpander"] summary {
        background: linear-gradient(135deg, #6F4E37, #C97B3D) !important;
        border-radius: 12px !important;
        padding: 0.85rem 1.1rem !important;
    }
    [data-testid="stExpander"] summary p {
        font-size: 1.2rem !important;
        font-weight: 700 !important;
        color: #FFFFFF !important;
    }
    [data-testid="stExpander"] summary svg {
        fill: #FFFFFF !important;
    }
    .pick-coffee-header {
        background: linear-gradient(135deg, #6F4E37, #C97B3D);
        border-radius: 12px 12px 0 0;
        padding: 0.85rem 1.1rem;
        font-size: 1.2rem;
        font-weight: 700;
        color: #FFFFFF;
        margin-bottom: 0;
    }
    .st-key-pick_coffee_box {
        border: 1px solid #C97B3D33 !important;
        border-top: none !important;
        border-radius: 0 0 12px 12px !important;
        padding: 1rem !important;
        margin-bottom: 1rem !important;
    }
    </style>
    <div class="pick-coffee-header">☕ Pick Your Perfect Coffee</div>
    """,
    unsafe_allow_html=True,
)

# Plain always-visible container (not a collapsible expander) - this can
# never accidentally close on the user, since there's no toggle at all.
with st.container(key="pick_coffee_box"):
    st.markdown(
        """
        <style>
        .st-key-brew_box {
            border-left: 4px solid #2E7D6B !important;
            border-radius: 10px !important;
            padding: 0.75rem 1rem !important;
            background-color: rgba(46, 125, 107, 0.04) !important;
        }
        .st-key-flavor_box {
            border-left: 4px solid #B5533C !important;
            border-radius: 10px !important;
            padding: 0.75rem 1rem !important;
            background-color: rgba(181, 83, 60, 0.04) !important;
        }
        .st-key-milk_box {
            border-left: 4px solid #C97B3D !important;
            border-radius: 10px !important;
            padding: 0.75rem 1rem !important;
            background-color: rgba(201, 123, 61, 0.04) !important;
        }
        .st-key-roast_box {
            border-left: 4px solid #6F4E37 !important;
            border-radius: 10px !important;
            padding: 0.75rem 1rem !important;
            background-color: rgba(111, 78, 55, 0.04) !important;
        }
        .st-key-budget_box {
            border-left: 4px solid #3D6FB5 !important;
            border-radius: 10px !important;
            padding: 0.75rem 1rem !important;
            background-color: rgba(61, 111, 181, 0.04) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True, key="brew_box"):
        st.markdown("**📦 How do you brew your coffee?**")
        sel_format = st.radio("How do you brew your coffee?",
                               ["Ground", "Whole Bean", "Capsule", "Easy Pour", "Cold Brew", "Concentrate/Drop", "Ready-to-Drink Can"],
                               key="filter_format", label_visibility="collapsed", horizontal=True)
    with st.container(border=True, key="flavor_box"):
        st.markdown("**🍫 What flavor do you crave?**")
        sel_flavor = st.radio("What flavor do you crave?",
                               ["Plain / Classic (No Specific Flavor)", "Chocolate & Cocoa", "Fruity & Berry",
                                "Nutty & Hazelnut", "Floral & Citrus", "Caramel & Honey"],
                               key="filter_flavor", label_visibility="collapsed", horizontal=True)
    with st.container(border=True, key="milk_box"):
        st.markdown("**🥛 Black or with milk?**")
        sel_milk = st.radio("Black or with milk?", ["With Milk", "Black - No Milk"],
                             key="filter_milk", label_visibility="collapsed", horizontal=True)
    with st.container(border=True, key="roast_box"):
        st.markdown("**☕ Roast preference?**")
        sel_roast = st.radio("Roast preference?", ["Light", "Medium", "Medium-Dark", "Dark"],
                              key="filter_roast", label_visibility="collapsed", horizontal=True)
    with st.container(border=True, key="budget_box"):
        st.markdown("**💰 Preferred budget tier per bag?**")
        sel_budget = st.radio("Preferred budget tier per bag?",
                               ["Everyday Essential (Under ₹500)", "Classic Reserve (₹500 – ₹800)", "Connoisseur Micro-Lot (₹800+)"],
                               key="filter_budget", label_visibility="collapsed", horizontal=True)

    st.markdown(
        """
        <style>
        .st-key-filter_submit_button button {
            background-color: #1F8A4C !important;
            color: white !important;
            border: none !important;
            font-weight: 700 !important;
            font-size: 1.05rem !important;
            padding: 0.6rem 0 !important;
        }
        .st-key-filter_submit_button button:hover {
            background-color: #17703C !important;
            color: white !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    filter_submitted = st.button("✅ Get Recommendations", key="filter_submit_button",
                                  use_container_width=True, type="primary")
    if filter_submitted:
        rule_based_matches, prefs = get_manual_filter_recommendations(
            sel_roast, sel_format, sel_milk, flavor_choice=sel_flavor, budget_choice=sel_budget, top_n=5
        )
        rule_based_top = rule_based_matches.iloc[0]
        reason = build_reason_text(prefs)

        # Try the AI first, exactly like the chat box - rule-based (the hard-
        # filtered result above) is only used as the fallback if the AI is
        # unavailable, errors out, or returns something outside the catalog.
        selection_text = (
            f"I selected: {sel_format} brew format, {sel_flavor} flavor, "
            f"{sel_milk}, {sel_roast} roast, {sel_budget} budget tier."
        )
        llm_result = get_llm_recommendation(selection_text, prefs)

        if llm_result is not None:
            method = "llm"
            llm_model_name = llm_result["llm_model_name"]
            llm_latency_ms = llm_result["llm_latency_ms"]
            matched_row = in_stock[in_stock["Product_Name"] == llm_result["matched_product_name"]].iloc[0].copy()
            matched_row["compatibility_score"] = llm_result["compatibility_score"]
            alt_pool = rule_based_matches[rule_based_matches["Product_Name"] != matched_row["Product_Name"]]
            matches = pd.concat([matched_row.to_frame().T, alt_pool], ignore_index=True).head(5)
            top = matched_row
            reply = llm_result["reason"] or f"Matched because you wanted: {reason}. Here's my pick, plus a few other options that fit well too:"
        else:
            method = "rule_based"
            llm_model_name = ""
            llm_latency_ms = 0
            matches = rule_based_matches
            top = rule_based_top
            reply = f"Matched because you wanted: {reason}. Here's my pick, plus a few other options that fit well too:"

        st.session_state.setdefault("messages", [])
        st.session_state["messages"].append(("user", f"Manual filter: {reason}", None))
        st.session_state["messages"].append(("assistant", reply, matches.head(5)))
        st.session_state["last_recommended_product"] = f"Blue Tokai — {top['Product_Name']}"
        st.session_state["conversation_rated"] = False
        st.session_state["has_had_response"] = True
        st.session_state["result_source"] = "manual"
        st.session_state["scroll_to_latest"] = True
        log_interaction(f"Manual filter: {reason}", prefs, len(matches))
        log_spss_session(
            "manual", prefs, top, len(matches) - 1,
            recommendation_method=method, llm_model_name=llm_model_name,
            llm_latency_ms=llm_latency_ms, rule_based_score_sc=rule_based_top["compatibility_score"],
        )
        st.session_state.setdefault("search_history", [])
        st.session_state["search_history"].append({
            "query": f"Manual filter: {reason}", "reply": reply, "products": matches.head(5),
        })
        st.rerun()

    # Guaranteed tap-to-jump link, right next to the button you just used -
    # visible in the same screen, no scrolling needed to find it. Unlike
    # JavaScript auto-scroll (which some mobile browsers block), a plain
    # anchor link always works.
    if st.session_state.get("result_source") == "manual" and len(st.session_state.get("messages", [])) > 1:
        st.markdown(
            '<a href="#latest-response-anchor" style="font-size:1.05em;">⬇️ Tap here to see your recommendation</a>',
            unsafe_allow_html=True,
        )


def render_product_cards(product_rows):
    top_row = product_rows.iloc[0]
    other_rows = product_rows.iloc[1:]

    # Highlighted "Our Pick" card - heading shows first (always immediately
    # visible, even on mobile where columns stack vertically), image and
    # details follow right below it.
    with st.container(border=True):
        st.markdown(f"### ⭐ Our Pick: {top_row['Product_Name']}")
        pick_img_col, pick_info_col = st.columns([1, 2])
        with pick_img_col:
            if pd.notna(top_row.get("Image_URL")):
                st.markdown(
                    f'<img src="{top_row["Image_URL"]}" '
                    f'style="max-width:170px; width:100%; display:block; margin:0 auto; '
                    f'border-radius:8px;">',
                    unsafe_allow_html=True,
                )
        with pick_info_col:
            st.markdown(
                f"**{top_row['Roast_Level']} roast** · {top_row['Format'].split('(')[0].strip()}  \n"
                f"Flavor: {top_row['Flavor_Notes']}  \n"
                f"**{format_price(top_row)}** · {top_row['compatibility_score']}% match"
            )
            buy_url = get_product_url(top_row["Product_Name"])
            st.markdown(
                f'<a href="{buy_url}" target="_blank" style="'
                f'display:inline-block; background:linear-gradient(135deg,#6F4E37,#C97B3D); '
                f'color:white !important; font-weight:700; padding:0.45rem 1rem; '
                f'border-radius:8px; text-decoration:none; font-size:0.92rem; margin-top:0.5rem;">'
                f'🛒 Buy on Blue Tokai</a>',
                unsafe_allow_html=True,
            )

    # Plain, smaller cards for the remaining options - no match % shown here,
    # since these come from a different scoring system than the top pick
    # (rule-based vs AI) and the two numbers aren't directly comparable.
    if not other_rows.empty:
        st.caption("Other options:")
        cols = st.columns(min(len(other_rows), 4))
        for col, (_, prow) in zip(cols, other_rows.iterrows()):
            with col:
                if pd.notna(prow.get("Image_URL")):
                    st.image(prow["Image_URL"], use_container_width=True)
                st.caption(f"{prow['Product_Name']}\n{format_price(prow)}")


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
            function getWin() {
                try { if (window.parent && window.parent.document) return window.parent; } catch (e) {}
                return window;
            }
            function tryJump(attemptsLeft) {
                const win = getWin();
                const doc = win.document;
                const anchor = doc.getElementById("latest-response-anchor");
                if (anchor) {
                    // Same native mechanism as a manually clicked <a href="#anchor">
                    // link - more reliable across embedded/iframe contexts than
                    // scrollIntoView, since it's real browser anchor navigation.
                    win.location.hash = "latest-response-anchor";
                    anchor.scrollIntoView({behavior: "smooth", block: "start"});
                    return;
                }
                if (attemptsLeft > 0) {
                    setTimeout(function() { tryJump(attemptsLeft - 1); }, 200);
                }
            }
            tryJump(20);
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
if len(st.session_state["messages"]) == 0:
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

# Guaranteed tap-to-jump link, right next to the Send button - visible in
# the same screen, no scrolling needed to find it.
if st.session_state.get("result_source", "chat") == "chat" and len(st.session_state.get("messages", [])) > 1:
    st.markdown(
        '<a href="#latest-response-anchor" style="font-size:1.05em;">⬇️ Tap here to see your recommendation</a>',
        unsafe_allow_html=True,
    )

# If the chat box (or a quick-start button) produced the most recent result,
# show it right here - directly under the chat box, and auto-scroll to it.
# (No divider shown here when the manual filter was the source, to avoid an
# empty-looking gap with nothing in it.)
if st.session_state.get("result_source", "chat") == "chat":
    st.divider()
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
            st.session_state["just_rated"] = True
            st.session_state["last_rating_stars"] = star_n
            st.rerun()
elif st.session_state["conversation_rated"]:
    stars_given = st.session_state.get("last_rating_stars", 5)
    is_good_rating = stars_given >= 3

    if st.session_state.get("just_rated"):
        if is_good_rating:
            st.balloons()
        else:
            st.markdown(
                """
                <style>
                @keyframes float-up {
                    0%   { transform: translateY(0) scale(1); opacity: 1; }
                    100% { transform: translateY(-160px) scale(1.4); opacity: 0; }
                }
                .floating-thumb {
                    position: fixed;
                    left: 50%;
                    bottom: 80px;
                    font-size: 2.5rem;
                    animation: float-up 1.6s ease-out forwards;
                    z-index: 9999;
                    pointer-events: none;
                }
                </style>
                <div class="floating-thumb">👍</div>
                <div class="floating-thumb" style="left: 44%; animation-delay: 0.15s;">👍</div>
                <div class="floating-thumb" style="left: 56%; animation-delay: 0.3s;">👍</div>
                """,
                unsafe_allow_html=True,
            )
    st.session_state["just_rated"] = False

    if is_good_rating:
        box_color = "#1F8A4C"
        header = "### 🎉 ☕ ✨"
        headline = "**Thanks so much for rating this chat!** 🙏"
        body = (
            f"I really hope *{st.session_state['last_recommended_product']}* "
            f"turns out to be everything you're hoping for."
        )
        footer = "**Enjoy every sip! 💚**"
    else:
        box_color = "#C97B3D"
        header = "### 🙌 ☕ 💫"
        headline = "**Thanks for sharing your feedback!**"
        body = (
            "Your input genuinely helps make every next cup better. "
            "Fancy a fresh pick? Just ask again — I'm ready to find something you'll love!"
        )
        footer = "**Let's find your perfect cup! ☕✨**"

    st.markdown(
        f"""
        <style>
        .st-key-thank_you_box {{
            border: 2px solid {box_color} !important;
            border-radius: 16px !important;
            padding: 1.5rem !important;
            background: linear-gradient(135deg, {box_color}22, {box_color}0D) !important;
            text-align: center !important;
        }}
        .st-key-thank_you_box p, .st-key-thank_you_box li {{
            color: #1a1a1a !important;
            font-size: 1.05rem !important;
            line-height: 1.5 !important;
        }}
        .st-key-thank_you_box h3 {{
            color: #1a1a1a !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True, key="thank_you_box"):
        st.markdown(header)
        st.markdown(headline)
        st.markdown(body)
        if footer:
            st.markdown(footer)
        st.markdown(
            """
            <div style="text-align: right; margin-top: 0.75rem;">
                <a href="#page-top-anchor" style="
                    display: inline-block;
                    background: linear-gradient(135deg, #6F4E37, #C97B3D);
                    color: white !important;
                    font-weight: 700;
                    padding: 0.6rem 1.4rem;
                    border-radius: 10px;
                    text-decoration: none;
                    font-size: 1.02rem;
                ">✨ Explore More Coffee Options</a>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Feedback form embedded directly in the app (not just a link out) so
    # people can respond right here, right after seeing how satisfied they
    # were with their pick. Only shown once a real Google Form URL is
    # configured, and only after rating, with their real session_id already
    # filled in so responses can be joined to session_log.csv later.
    if GOOGLE_FORM_URL and "REPLACE_WITH" not in GOOGLE_FORM_URL:
        st.divider()
        st.markdown("#### 📝 One more thing — quick feedback?")
        st.caption("Takes about a minute, helps us understand how well the matches actually worked.")
        embed_form_url = (
            f"{GOOGLE_FORM_URL}?embedded=true&{GOOGLE_FORM_SESSION_ENTRY_ID}="
            f"{st.session_state.get('session_id', '')}"
        )
        st.markdown(
            f'<iframe src="{embed_form_url}" width="100%" height="900" '
            f'frameborder="0" marginheight="0" marginwidth="0" '
            f'style="border-radius:12px; border:1px solid #C97B3D33;">Loading…</iframe>',
            unsafe_allow_html=True,
        )
