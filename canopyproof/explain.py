"""Plain-language integrity notes for credit buyers and auditors.

A fixed template writes the note from facts in the verified records (offline, deterministic).
With --llm, an open-weight model on Nebius AI Studio (Token Factory) rephrases the same facts; the API
key is read at runtime from the environment or from a .env file named by NEBIUS_ENV_FILE and is never
stored or printed. Any failure, or output that drifts into claims the data cannot support, falls back
to the template. The disclaimer is appended by code. Notes are not part of the signed records.
"""
import json
import os
import re
from datetime import datetime, timezone

from .canonical import canonical_bytes, dec, keccak_hex
from .config import NOTES_DIR, ROOT, VALIDATION_DIR

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
DISCLAIMER = ("Satellite change detection is evidence for review, not proof of carbon loss. It does not "
              "assess additionality, baselines or leakage, and a 1 km cell samples a project rather than measuring it.")
BANNED = ["fraud", "fraudulent", "worthless", "scam", "guarantee", "tonnes of co2", "tco2", "certif"]


def facts_for(series_doc):
    rows = series_doc["series"]
    measured = [r for r in rows if r["status"] != "insufficient_data"]
    if not measured:
        return None
    latest = measured[-1]
    first_reversal = next((r for r in measured if r["status"] == "reversal_suspected"), None)
    validation_path = VALIDATION_DIR / f"{series_doc['site_id']}.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else None
    return {
        "project": series_doc["project"]["name"],
        "project_is_real": series_doc["project"]["real_project"],
        "cell": series_doc["name"],
        "cell_area_ha": series_doc["area_ha"],
        "forest_ha": str(series_doc["forest_ha"]),
        "latest_date": latest["date"],
        "latest_status": latest["status"],
        "confirmed_loss_ha": latest["loss_ha"],
        "confirmed_loss_share": latest["loss"],
        "first_reversal_date": first_reversal["date"] if first_reversal else None,
        "clear_looks": len(measured),
        "first_look": measured[0]["date"],
        "hansen_recall": dec(validation["recall"], 3) if validation else None,
        "hansen_precision": dec(validation["precision"], 3) if validation else None,
        "documented_events": [e["text"] for e in series_doc["project"]["documented_events"]],
        "record_id": latest["record_id"],
    }


def offline_note(f):
    status = {"healthy": "no sign of forest loss", "watch": "a change worth reviewing",
              "reversal_suspected": "a suspected reversal"}[f["latest_status"]]
    parts = [f"{f['cell']} ({f['cell_area_ha']} ha cell, {f['forest_ha']} ha of baseline forest) in "
             f"{f['project']}. Latest clear Sentinel-2 view: {f['latest_date']}, showing {status}."]
    if f["confirmed_loss_ha"] and float(f["confirmed_loss_ha"]) > 0:
        parts.append(f"Confirmed loss since the baseline: {float(f['confirmed_loss_ha']):.1f} ha "
                     f"({float(f['confirmed_loss_share']) * 100:.1f}% of the cell's forest).")
    if f["first_reversal_date"]:
        parts.append(f"The reversal threshold was first crossed on {f['first_reversal_date']}.")
    parts.append(f"{f['clear_looks']} clear looks since {f['first_look']} are anchored on-chain.")
    recall = float(f["hansen_recall"]) if f["hansen_recall"] is not None else None
    precision = float(f["hansen_precision"]) if f["hansen_precision"] is not None else None
    if recall is not None and precision is not None:
        parts.append(f"Against the independent Hansen Global Forest Change map (loss 2019-2024), CanopyProof found "
                     f"{recall:.0%} of Hansen's loss pixels, and {precision:.0%} of its own confirmed pixels agree.")
    elif recall is None and precision is None:
        parts.append("The independent Hansen Global Forest Change map also shows no 2019-2024 loss here.")
    elif recall is None:
        parts.append("Hansen Global Forest Change shows no 2019-2024 loss here; loss confirmed by CanopyProof in that "
                     "period needs review.")
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


def llm_note(facts, model=None, timeout=60):
    import httpx

    key = _nebius_key()
    if not key:
        raise RuntimeError("NEBIUS_API_KEY not available")
    system = ("You write short integrity notes for carbon-credit buyers and auditors. Use ONLY the facts in the "
              "JSON; do not add numbers. Do not call anything fraud, worthless or certified, and do not convert "
              "hectares into tonnes of CO2. Say 'suspected' for reversals. No disclaimer (one is appended). "
              "60 to 100 words, one paragraph, plain English.")
    body = {"model": model or os.environ.get("CANOPYPROOF_LLM_MODEL", DEFAULT_MODEL),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
            "temperature": 0.2, "max_tokens": 400}
    resp = httpx.post(NEBIUS_BASE_URL + "chat/completions", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    text = re.sub(r"<think>.*?</think>", "", resp.json()["choices"][0]["message"]["content"], flags=re.S).strip()
    if not text or any(w in text.lower() for w in BANNED):
        raise ValueError("model output failed the safety filter")
    return text, body["model"]


def explain(series_doc, use_llm=False, log=print):
    facts = facts_for(series_doc)
    if facts is None:
        return None
    text, generated_by = None, "offline template"
    if use_llm:
        try:
            text, model = llm_note(facts)
            generated_by = f"{model} via Nebius AI Studio (Token Factory)"
        except Exception as exc:  # network, quota, filter: fall back, never fail the pipeline
            log(f"  LLM unavailable ({type(exc).__name__}); using the offline template")
    text = text or offline_note(facts)
    note = {"site_id": series_doc["site_id"], "text": text, "disclaimer": DISCLAIMER, "generated_by": generated_by,
            "ai_generated": generated_by != "offline template", "facts": facts,
            "facts_hash": keccak_hex(canonical_bytes(json.loads(json.dumps(facts, ensure_ascii=True)))),
            "source_record": facts["record_id"],
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    (NOTES_DIR / f"{series_doc['site_id']}.json").write_text(json.dumps(note, indent=1) + "\n", encoding="utf-8", newline="
")
    return note
