
from typing import Dict, Any
import os

from .dynprompt.expander import PromptExpander

class DynPromptExpand:
    """
    Expand dynamic prompts + mirrored wildcards for ComfyUI.
    - Inputs: positive text, negative text, seed, wildcard_dir
    - Outputs: expanded positive text, expanded negative text
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "negative": ("STRING", {"multiline": True, "default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "wildcard_dir": ("STRING", {"default": "", "tooltip": "Folder with wildcard .txt files"}),
                "auto_neg_from_mir": ("BOOLEAN", {"default": True, "tooltip": "If negative contains __name__ and name-mir.txt exists, auto-mirror to 'all except chosen'."}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "negative")
    FUNCTION = "expand"
    CATEGORY = "prompt"

    def expand(self, text: str, negative: str, seed: int, wildcard_dir: str, auto_neg_from_mir: bool):
        # Normalize wildcard dir (supports empty -> local /wildcards as fallback)
        wd = wildcard_dir.strip()
        if not wd:
            # default to a 'wildcards' folder inside this custom node
            wd = os.path.join(os.path.dirname(__file__), "wildcards")

        exp_pos = PromptExpander(seed=seed, wildcard_dir=wd).expand_prompt(text or "", phase="pos")

        # If auto_neg is enabled and user left negative empty, derive mirrored negatives
        if auto_neg_from_mir and (not (negative or "").strip()):
            # Build synthetic negative by scanning positive for __tokens__
            # We let the expander handle auto-mirroring in neg-phase.
            import re
            tokens = sorted(set(re.findall(r"__([A-Za-z0-9_\-]+)__", text or "")))
            # Limit to tokens that have a corresponding -mir file or normal file
            cand = []
            for t in tokens:
                if os.path.isfile(os.path.join(wd, f"{t}-mir.txt")) or os.path.isfile(os.path.join(wd, f"{t}.txt")):
                    cand.append(f"__{t}__")
            neg_src = ", ".join(cand)
            exp_neg = PromptExpander(seed=seed, wildcard_dir=wd).expand_prompt(neg_src, phase="neg")
        else:
            exp_neg = PromptExpander(seed=seed, wildcard_dir=wd).expand_prompt(negative or "", phase="neg")

        return (exp_pos, exp_neg)

NODE_CLASS_MAPPINGS = {
    "DynPromptExpand": DynPromptExpand,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DynPromptExpand": "DynPrompt Expand (mirrored wildcards)",
}
