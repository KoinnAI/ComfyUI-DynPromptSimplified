import os
import re
from typing import List
from .dynprompt.expander import (
    PromptExpander,
    WILDCARD_TOKEN_RE,
)

# -------------------- Small helpers --------------------

def _csv_chunks(s: str) -> List[str]:
    """Split a comma list into items, trimming whitespace and skipping empties."""
    return [p.strip() for p in (s or "").split(",") if p.strip()]

def _merge_csv(*parts: str) -> str:
    """
    Merge multiple comma lists, preserving order of first appearance
    and de-duplicating case-insensitively.
    """
    seen = set()
    out = []
    for part in parts:
        for chunk in _csv_chunks(part):
            key = chunk.lower()
            if key not in seen:
                seen.add(key)
                out.append(chunk)
    return ", ".join(out)

def _collect_mirrors_deep(text: str, base_expander: PromptExpander) -> str:
    """
    Deep mirroring pass with counter-based RNG:
      - Use a fresh expander (same seed/wildcard_dir/variety) so counters start at 0.
      - Collapse braces first (consumes choice counter).
      - Expand wildcards left-to-right; for any token ending with '-mir', add
        "all except chosen" (ignore empties) to the bag.
      - Replace tokens with their chosen values to permit nested discovery.
    """
    mirror = PromptExpander(
        seed=base_expander.seed,
        wildcard_dir=base_expander.wildcard_dir,
        variety=getattr(base_expander, "variety", 0),
    )

    # choices first (consumes mirror's choice counter)
    s = mirror.collapse_choices(text or "")
    bag: List[str] = []

    # wildcard pass (consumes mirror's wild counter in the same sequence as Positive)
    while True:
        m = WILDCARD_TOKEN_RE.search(s)
        if not m:
            break

        token = m.group(1)
        start, end = m.span()

        opts = mirror.read_options_for_token(token)
        if not opts:
            # drop unknown to mirror main expander behavior
            s = s[:start] + s[end:]
            continue

        idx = mirror._pick_wild_index(token, len(opts))
        chosen = opts[idx] if 0 <= idx < len(opts) else ""

        if token.endswith("-mir"):
            others = [o for i, o in enumerate(opts) if i != idx and o.strip()]
            if others:
                bag.extend(others)

        # Replace the token with its chosen text and continue scanning (to handle nesting)
        s = s[:start] + chosen + s[end:]

    # Return as CSV; dedup/merge handled by _merge_csv
    return ", ".join(bag)

# -------------------- ComfyUI Node --------------------

class DynPromptExpand:
    """
    ComfyUI node:
      - Positive output: expands braces & wildcards normally (pick-one).
      - Negative output: expands the user's Negative normally (pick-one), then
        appends deep-mirrored exclusions from ANY __*-mir__ encountered in the
        Positive (including those nested inside wildcard files). Strict '-mir':
        only '<name>-mir.txt' is consulted; no fallback to '<name>.txt'.

    Counter-based RNG means fixed tokens don't influence randomness; only
    decision points (braces/wildcards) advance the counters. Optional 'variety'
    input gives multiple reproducible randomness lanes per seed.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "negative": ("STRING", {"multiline": True, "default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "variety": ("INT", {"default": 0, "min": 0, "max": 100,
                                    "tooltip": "Extra randomness lane. 0 = off; same seed+variety reproduce."}),
                "wildcard_dir": ("STRING", {
                    "default": "",
                    "tooltip": "Folder with wildcard .txt files (supports subfolders like clothes/tops.txt)"
                }),
                "auto_neg_from_mir": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Append deep mirrored exclusions for any __*-mir__ found in Positive (strict lookup)."
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "negative")
    FUNCTION = "expand"
    CATEGORY = "prompt"

    def expand(self, text: str, negative: str, seed: int, variety: int, wildcard_dir: str, auto_neg_from_mir: bool):
        # Resolve wildcard dir (absolute, ~ supported). Fallback to ./wildcards next to this file.
        wd = (wildcard_dir or "").strip()
        if wd:
            wd = os.path.abspath(os.path.expanduser(wd))
        if not wd:
            wd = os.path.abspath(os.path.join(os.path.dirname(__file__), "wildcards"))

        expander = PromptExpander(seed=seed, wildcard_dir=wd, variety=variety)

        # A) Positive prompt: full expansion (choices -> wildcards -> choices)
        pos_final = expander.expand_prompt(text or "", phase="pos")

        # B) User Negative: expand normally (pick-one semantics; no mirroring here)
        neg_user_final = expander.expand_prompt(negative or "", phase="pos")

        # C) Deep mirror: collect exclusions for ANY __*-mir__ appearing during Positive expansion
        mirrored_additions = ""
        if auto_neg_from_mir and (text or "").strip():
            mirrored_additions = _collect_mirrors_deep(text, expander)

        # D) Merge user negative + mirrored exclusions (dedup; preserve order)
        neg_final = _merge_csv(neg_user_final, mirrored_additions)

        return (pos_final, neg_final)

# ComfyUI registration
NODE_CLASS_MAPPINGS = {"DynPromptExpand": DynPromptExpand}
NODE_DISPLAY_NAME_MAPPINGS = {"DynPromptExpand": "DynPrompt Expand (deep-mirrored)"}
