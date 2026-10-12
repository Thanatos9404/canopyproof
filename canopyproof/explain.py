"""Farmer-readable field notes in English or Hindi.

The note is written from a small "fact sheet" taken from verified records. By default a fixed
template writes it (offline, deterministic). With --llm, an open-weight model on Nebius AI Studio
Token Factory rephrases the same facts; the API key is read at runtime from the environment or from
a .env file named by NEBIUS_ENV_FILE and is never stored or printed. Any failure, or any output that
drifts into diagnosis words, falls back to the template. The fixed disclaimer is always appended by
code, not by the model. Notes are not part of the signed records.
"""
import json
import os
import re
from datetime import datetime, timezone

from .canonical import canonical_bytes, keccak_hex
from .config import EXPLANATIONS_DIR, ROOT

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
DISCLAIMER = {
    "en": ("This note uses satellite and weather-model data only. It cannot detect disease, pests or "
           "nutrient problems and does not predict yield. Walk the field before you act."),
    "hi": ("यह जानकारी केवल उपग्रह और मौसम-मॉडल के आँकड़ों पर आधारित है। इससे रोग, कीट या पोषक तत्वों "
           "की कमी का पता नहीं चलता और उपज का अनुमान नहीं लगता। कोई भी कदम उठाने से पहले खेत ख़ुद देखें।"),
}
BANNED = {
    "en": ["disease", "pest", "fungus", "fertili", "pesticide", "yield", "insurance", "payout", "loan"],
    "hi": ["रोग", "कीट", "उर्वरक", "कीटनाशक", "उपज", "बीमा"],
}


def fact_sheet(series_doc):
    """Facts from the most recent cloud-free record of a plot (series file built by `fieldproof build`)."""
    rows = series_doc["series"]
    clear = [r for r in rows if r["clear"]]
    if not clear:
        return None
    latest = clear[-1]
    earlier = [r for r in clear if r["t"] <= latest["t"] - 25 * 86400]
    prev = earlier[-1] if earlier else None
    return {
        "plot": series_doc["name"],
        "district": series_doc["district"],
        "state": series_doc["state"],
        "date": latest["date"],
        "record_id": latest["record_id"],
        "ndvi": latest["ndvi"],
        "ndvi_expected": latest["ndvi_expected"],
        "ndvi_month_ago": prev["ndvi"] if prev else None,
        "month_ago_date": prev["date"] if prev else None,
        "vci": latest["vci"],
        "rain60_departure_pct": latest["rain60"],
        "rain60_mm": latest["rain60_mm"],
        "rain60_normal_mm": latest["rain60_normal_mm"],
        "stress_level": latest["stress"],
        "reasons": latest["reasons"],
    }


def _f(x):
    return None if x is None else float(x)


def offline_note(facts, lang="en"):
    ndvi, exp, prev = _f(facts["ndvi"]), _f(facts["ndvi_expected"]), _f(facts["ndvi_month_ago"])
    vci, rain = _f(facts["vci"]), _f(facts["rain60_departure_pct"])
    level = facts["stress_level"]
    if lang == "hi":
        parts = [f"{facts['plot']} ({facts['district']}, {facts['state']})। आख़िरी साफ़ उपग्रह चित्र: {facts['date']}।"]
        parts.append(f"फ़सल की हरियाली (NDVI) {ndvi:.2f} है" + (
            f", जबकि इस समय आम तौर पर लगभग {exp:.2f} रहती है।" if exp is not None else "।"))
        if prev is not None:
            trend = "बढ़ी है" if ndvi > prev + 0.05 else "घटी है" if ndvi < prev - 0.05 else "लगभग वैसी ही है"
            parts.append(f"एक महीने पहले ({facts['month_ago_date']}) की तुलना में हरियाली {trend}।")
        if vci is not None:
            parts.append(f"पिछले वर्षों की तुलना में वनस्पति स्थिति सूचकांक (VCI) {vci:.0f} है"
                         + (" (35 से कम, यानी कमज़ोर)।" if vci < 35 else "।"))
        if rain is not None:
            rain_txt = (f"सामान्य से {abs(rain):.0f}% कम" if rain <= -20 else
                        f"सामान्य से {rain:.0f}% अधिक" if rain >= 20 else "सामान्य के आसपास")
            parts.append(f"पिछले 60 दिनों में बारिश {rain_txt} रही ({facts['rain60_mm']} मिमी, सामान्य {facts['rain60_normal_mm']} मिमी)।")
        parts.append({"alert": "संकेत: सूखे के तनाव की आशंका। जल्द खेत में जाकर नमी और पौधों की हालत देखें।",
                      "watch": "संकेत: ध्यान देने लायक बदलाव। अगली बार खेत जाएँ तो इस हिस्से को ज़रूर देखें।",
                      "normal": "संकेत: कोई असामान्य बदलाव नहीं दिखा।"}.get(level, "संकेत: इस बार साफ़ चित्र नहीं मिला।"))
        return " ".join(parts)
    parts = [f"{facts['plot']} ({facts['district']}, {facts['state']}). Latest clear satellite view: {facts['date']}."]
    parts.append(f"Crop greenness (NDVI) is {ndvi:.2f}" + (
        f"; around this date it is usually about {exp:.2f}." if exp is not None else "."))
    if prev is not None:
        trend = "risen" if ndvi > prev + 0.05 else "fallen" if ndvi < prev - 0.05 else "stayed about the same"
        parts.append(f"Since {facts['month_ago_date']} it has {trend}.")
    if vci is not None:
        parts.append(f"Compared with earlier years the Vegetation Condition Index is {vci:.0f} out of 100"
                     + (" (below 35, which signals stress)." if vci < 35 else "."))
    if rain is not None:
        rain_txt = (f"{abs(rain):.0f}% below normal" if rain <= -20 else f"{rain:.0f}% above normal" if rain >= 20
                    else "close to normal")
        parts.append(f"Rain over the last 60 days was {rain_txt} ({facts['rain60_mm']} mm vs a usual "
                     f"{facts['rain60_normal_mm']} mm).")
    parts.append({"alert": "Signal: possible drought stress. Visit the field soon and check soil moisture and the plants.",
                  "watch": "Signal: a change worth watching. Look at this field on your next visit.",
                  "normal": "Signal: nothing unusual compared with this field's history."}.get(
        level, "Signal: no clear satellite view this time."))
    return " ".join(parts)


def _nebius_key():
    if os.environ.get("NEBIUS_API_KEY"):
        return os.environ["NEBIUS_API_KEY"]
    from dotenv import dotenv_values

    local = dotenv_values(ROOT / ".env")
    if local.get("NEBIUS_API_KEY"):
        return local["NEBIUS_API_KEY"]
    env_file = os.environ.get("NEBIUS_ENV_FILE") or local.get("NEBIUS_ENV_FILE")
    if env_file and os.path.exists(env_file):
        return dotenv_values(env_file).get("NEBIUS_API_KEY")
    return None


def llm_note(facts, lang="en", model=None, timeout=60):
    import httpx

    key = _nebius_key()
    if not key:
        raise RuntimeError("NEBIUS_API_KEY not available")
    language = "Hindi (Devanagari script, simple village-level words)" if lang == "hi" else "simple English"
    system = (
        "You write short field notes for smallholder farmers in India. Use ONLY the facts in the JSON. "
        "Do not add numbers that are not in the facts. Never mention disease, pests, fertiliser, pesticide, "
        "yield, insurance, loans or money. Do not add a disclaimer; one is appended separately. "
        f"Write 60 to 100 words in {language}, as one paragraph, ending with what to look at in the field."
    )
    glossary = {
        "ndvi": "crop greenness from satellite, 0 (bare soil) to about 0.9 (dense green crop)",
        "ndvi_expected": "this plot's usual NDVI on this date from earlier years",
        "vci": "Vegetation Condition Index: 0 = worst, 100 = best compared with this plot's earlier years; "
               "below 35 is the usual drought-stress threshold",
        "rain60_departure_pct": "rain in the last 60 days compared with the 1991-2020 normal, in percent",
        "stress_level": "normal, watch or alert, from fixed rules",
    }
    body = {
        "model": model or os.environ.get("FIELDPROOF_LLM_MODEL", DEFAULT_MODEL),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps({"facts": facts, "glossary": glossary}, ensure_ascii=False)}],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    resp = httpx.post(NEBIUS_BASE_URL + "chat/completions", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    lowered = text.lower()
    if not text or any(word in lowered for word in BANNED[lang]):
        raise ValueError("model output failed the safety filter")
    return text, body["model"]


def explain(series_doc, lang="en", use_llm=False, log=print):
    facts = fact_sheet(series_doc)
    if facts is None:
        return None
    generated_by = "offline template"
    text = None
    if use_llm:
        try:
            text, model = llm_note(facts, lang)
            generated_by = f"{model} via Nebius AI Studio (Token Factory)"
        except Exception as exc:  # network, quota, filter: fall back, never fail the pipeline
            log(f"  LLM unavailable ({type(exc).__name__}); using the offline template")
    if text is None:
        text = offline_note(facts, lang)
    note = {
        "plot_id": series_doc["plot_id"],
        "lang": lang,
        "text": text,
        "disclaimer": DISCLAIMER[lang],
        "generated_by": generated_by,
        "ai_generated": generated_by != "offline template",
        "facts": facts,
        "facts_hash": keccak_hex(canonical_bytes(json.loads(json.dumps(facts, ensure_ascii=True)))),
        "source_record": facts["record_id"],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
    (EXPLANATIONS_DIR / f"{series_doc['plot_id']}.{lang}.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return note
