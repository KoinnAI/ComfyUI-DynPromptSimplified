
import os
import re
import hashlib
from typing import List, Optional, Tuple

WILDCARD_TOKEN_RE = re.compile(r"__([A-Za-z0-9_\-]+)__")
INNER_BRACE_RE = re.compile(r"\{([^{}]+)\}")

def _split_choices(s: str) -> List[str]:
    return [p.strip() for p in s.split("|")]

def _stable_pick_index(seed: int, salt: str, n: int) -> int:
    if n <= 0:
        return 0
    h = hashlib.md5(f"{seed}:{salt}".encode("utf-8")).hexdigest()
    return int(h, 16) % n

class PromptExpander:
    def __init__(self, seed: int, wildcard_dir: Optional[str] = None):
        self.seed = int(seed) if seed is not None else 0
        self.wildcard_dir = wildcard_dir or ""

    # ---- Wildcards --------------------------------------------------------
    def _wildcard_path(self, token: str) -> Optional[str]:
        if not self.wildcard_dir:
            return None
        p = os.path.join(self.wildcard_dir, f"{token}.txt")
        return p if os.path.isfile(p) else None

    def _read_wildcard_lines(self, token: str) -> List[str]:
        path = self._wildcard_path(token)
        if not path:
            return []
        out: List[str] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                t = line.strip()
                if t:
                    out.append(t)
        return out

    # ---- Choice expansion --------------------------------------------------
    def _expand_choices_once(self, s: str) -> str:
        # Expand the first innermost {...} only once
        m = INNER_BRACE_RE.search(s)
        if not m:
            return s
        start, end = m.span()
        body = m.group(1)
        options = _split_choices(body)
        if not options:
            return s  # nothing to do
        idx = _stable_pick_index(self.seed, f"choice:{s}:{start}-{end}", len(options))
        chosen = options[idx]
        return s[:start] + chosen + s[end+1:]

    def _expand_choices_recursively(self, s: str, limit: int = 64) -> str:
        # Safeguard against runaway nesting
        for _ in range(limit):
            if not INNER_BRACE_RE.search(s):
                break
            s = self._expand_choices_once(s)
        return s

    # ---- Wildcard expansion w/ mirrored semantics -------------------------
    def _expand_wildcards_once(self, s: str, phase: str) -> str:
        m = WILDCARD_TOKEN_RE.search(s)
        if not m:
            return s
        token = m.group(1)  # raw token without __
        start, end = m.span()

        # For negative phase: prefer "-mir" file if it exists for auto-mirroring
        token_for_options = token
        if phase == "neg" and not token.endswith("-mir"):
            if self._wildcard_path(token + "-mir"):
                token_for_options = token + "-mir"

        options = self._read_wildcard_lines(token_for_options)
        if not options:
            # Missing wildcard: drop it
            return s[:start] + "" + s[end:]

        idx = _stable_pick_index(self.seed, f"wild:{token}:{s}:{start}-{end}", len(options))

        if phase == "pos":
            core = options[idx]
        else:
            # negative gets all except chosen
            core = ", ".join([o for i, o in enumerate(options) if i != idx])

        return s[:start] + core + s[end:]

    def _expand_wildcards_recursively(self, s: str, phase: str, limit: int = 128) -> str:
        for _ in range(limit):
            if not WILDCARD_TOKEN_RE.search(s):
                break
            s = self._expand_wildcards_once(s, phase=phase)
        return s

    # ---- Public API --------------------------------------------------------
    def expand_prompt(self, text: str, phase: str = "pos") -> str:
        """
        phase: "pos" for positive prompt, "neg" for negative prompt.
        - {a|b|{c|d}} expands deterministically by seed.
        - __name__ loads wildcards/name.txt
        - __name-mir__ loads wildcards/name-mir.txt (strict)
        - In neg-phase, if __name__ is used and name-mir.txt exists, the
          node auto-mirrors by expanding to all options except the chosen one.
        """
        if not text:
            return ""
        out = text
        # 1) Expand braces fully
        out = self._expand_choices_recursively(out)
        # 2) Expand wildcards fully for the phase
        out = self._expand_wildcards_recursively(out, phase=phase)
        # 3) Expand braces again in case wildcard lines contained braces
        out = self._expand_choices_recursively(out)
        # Remove accidental double commas/spaces
        import re as _re
        out = _re.sub(r"\s*,\s*,\s*", ", ", out).strip().strip(", ")
        return out
