import os
import re
import hashlib
from typing import List, Optional

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Allow subfolders (/) and dotted names (.) plus hyphen/underscore
WILDCARD_TOKEN_RE = re.compile(r"__([A-Za-z0-9_\-/.]+)__")

# Matches a single brace block that does NOT contain nested braces.
# We allow empty branches (e.g., {a| |b|}) via [^{}]*.
INNER_BRACE_RE = re.compile(r"\{([^{}]*)\}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_choices_keep_empty(s: str) -> List[str]:
    """
    Split a non-nested brace's inner text by '|' into options, preserving empty branches.
    Whitespace around options is trimmed, but empty strings are kept as ''.
    """
    parts = s.split("|")
    return [p.strip() for p in parts]  # keep '' for empty branches


def _split_top_level_alts(s: str) -> List[str]:
    """
    Split a possibly nested brace's inner text by top-level '|', honoring nesting.
    Keeps empty alternatives as ''.
    """
    alts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in s:
        if ch == '|' and depth == 0:
            alts.append(''.join(buf).strip())
            buf = []
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        buf.append(ch)
    alts.append(''.join(buf).strip())
    return alts


def _flatten_brace_expr(expr: str) -> List[str]:
    """
    Expand a string containing brace expressions (with possible nesting) into all leaf strings.
    Empty branches are preserved as '' when they reduce to nothing.
    """
    # Find first '{'
    i = expr.find('{')
    if i == -1:
        return [expr.strip()]

    # Find its matching '}'
    depth = 0
    j = i
    while j < len(expr):
        if expr[j] == '{':
            depth += 1
        elif expr[j] == '}':
            depth -= 1
            if depth == 0:
                break
        j += 1

    if j >= len(expr):
        # Malformed braces; return as-is
        return [expr.strip()]

    pre = expr[:i]
    inner = expr[i+1:j]
    post = expr[j+1:]

    results: List[str] = []
    for alt in _split_top_level_alts(inner):
        for alt_exp in _flatten_brace_expr(alt):
            for post_exp in _flatten_brace_expr(post):
                results.append((pre + alt_exp + post_exp).strip())
    return results


def _dedup_preserve(seq: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in seq:
        key = x.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(x.strip())
    return out


def _stable_pick_index(seed: int, salt: str, n: int) -> int:
    """
    Deterministic index picker stable across runs/processes.
    """
    if n <= 0:
        return 0
    h = hashlib.md5(f"{seed}:{salt}".encode("utf-8")).hexdigest()
    return int(h, 16) % n


# ---------------------------------------------------------------------------
# Expander
# ---------------------------------------------------------------------------

class PromptExpander:
    """
    Expands deterministically by seed and counter-based salts:

      1) {a|b|{c|d}|}  --> brace choices (empty branch allowed)
      2) __token__     --> wildcards from <wildcard_dir>/token.txt
                          * STRICT lookup: 'foo-mir' -> 'foo-mir.txt' ONLY (no fallback)
                          * Subfolders & dotted names supported (e.g., clothes/tops.v1)
      3) Runs braces again in case wildcard lines contain {…}

    Negative semantics are controlled by 'phase':
      - phase="pos": pick-one for wildcards (normal)
      - phase="neg": join all except chosen (used for mirroring)
        (empty options are ignored when joining, so they don't add commas)

    Counter-based randomness:
      - Fixed text has no effect on picks.
      - Choices use an incrementing choice counter (choice#0, choice#1, ...).
      - Wildcards use an incrementing wild counter (wild#0, wild#1, ...), salted with token.
      - Optional 'variety' integer allows multiple deterministic lanes per seed.
    """

    def __init__(self, seed: int, wildcard_dir: Optional[str] = None, variety: int = 0):
        self.seed = int(seed) if seed is not None else 0
        self.wildcard_dir = wildcard_dir or ""
        self.variety = int(variety) if variety else 0
        self._salt_extra = f":v{self.variety}" if self.variety else ""
        # runtime counters (reset per expand)
        self._choice_counter = 0
        self._wild_counter = 0

    # -------------------- Counter RNG helpers --------------------

    def _reset_counters(self):
        self._choice_counter = 0
        self._wild_counter = 0

    def _pick_choice_index(self, n: int) -> int:
        idx = _stable_pick_index(self.seed, f"choice#{self._choice_counter}{self._salt_extra}", n)
        self._choice_counter += 1
        return idx

    def _pick_wild_index(self, token: str, n: int) -> int:
        idx = _stable_pick_index(self.seed, f"wild#{self._wild_counter}:{token}{self._salt_extra}", n)
        self._wild_counter += 1
        return idx

    # -------------------- Public helpers --------------------

    def collapse_choices(self, text: str) -> str:
        """Deterministically collapse all {a|b|{c|d}|} blocks. No wildcard expansion."""
        # Ensure counters match the main expand order if used standalone
        self._reset_counters()
        return self._expand_choices_recursively(text or "")

    def read_options_for_token(self, token: str) -> List[str]:
        """
        STRICT lookup:
          - 'foo'     -> 'foo.txt'
          - 'foo-mir' -> 'foo-mir.txt' (NO fallback to 'foo.txt')

        Behavior:
          * Multi-line file: each non-empty, non-comment line is one option.
          * Single-line without braces: the whole line is one option.
          * Single-line WITH braces:
              - For '-mir' tokens: FLATTEN nested braces into leaf options
                                   (preserve empty leaves as '').
              - For non-mir tokens: split by TOP-LEVEL '|' into options
                                    (empty branches kept as '').
        """
        path = self._normalize_token_to_path(token)
        if not path:
            return []

        raw: List[str] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                t = line.rstrip("\n\r").strip()
                if not t:
                    # Ignore blank lines in multi-line files (empties should be explicit via braces)
                    continue
                if t.startswith("#"):
                    continue
                raw.append(t)

        if not raw:
            return []

        # Single-line file
        if len(raw) == 1:
            line = raw[0]
            if "{" in line and "}" in line:
                if token.endswith("-mir"):
                    # Deep flatten for -mir so "all-except-chosen" is well-defined over leaves
                    return _dedup_preserve(_flatten_brace_expr(line))
                else:
                    # Non-mir: choose among top-level alts (empties allowed)
                    m = re.fullmatch(r"\{(.*)\}", line)
                    if m:
                        return _split_top_level_alts(m.group(1))
                    # If unmatched, leave as single option
                    return [line]
            # No braces => single option
            return [line]

        # Multi-line file: return lines as distinct options (braces inside a line
        # are collapsed later by the brace pass).
        return raw

    # -------------------- IO & path safety ----------------------

    def _normalize_token_to_path(self, token: str) -> Optional[str]:
        if not self.wildcard_dir:
            return None
        token = token.replace("\\", "/").lstrip("/.")
        # Block parent traversal
        if ".." in token.split("/"):
            return None
        base = os.path.normpath(self.wildcard_dir)
        p = os.path.normpath(os.path.join(base, f"{token}.txt"))
        if not p.startswith(base):
            return None
        return p if os.path.isfile(p) else None

    # -------------------- Choice expansion ------------------

    def _expand_choices_once(self, s: str) -> str:
        m = INNER_BRACE_RE.search(s)
        if not m:
            return s
        start, end = m.span()
        options = _split_choices_keep_empty(m.group(1))
        if not options:
            # Leave unchanged on malformed {}
            return s
        idx = self._pick_choice_index(len(options))
        chosen = options[idx]  # may be '' (empty branch)
        return s[:start] + chosen + s[end:]

    def _expand_choices_recursively(self, s: str, limit: int = 64) -> str:
        for _ in range(limit):
            if not INNER_BRACE_RE.search(s):
                break
            s = self._expand_choices_once(s)
        return s

    # -------------------- Wildcard expansion ----------------

    def _expand_wildcards_once(self, s: str, phase: str) -> str:
        m = WILDCARD_TOKEN_RE.search(s)
        if not m:
            return s
        token = m.group(1)
        start, end = m.span()

        options = self.read_options_for_token(token)
        if not options:
            # Missing file => drop token silently
            return s[:start] + "" + s[end:]

        idx = self._pick_wild_index(token, len(options))
        chosen = options[idx] if 0 <= idx < len(options) else ""

        if phase == "pos":
            core = chosen  # may be '' => token disappears
        else:
            # phase == "neg": join all except chosen; ignore empty strings
            core = ", ".join(o for i, o in enumerate(options) if i != idx and o.strip())

        return s[:start] + core + s[end:]

    def _expand_wildcards_recursively(self, s: str, phase: str, limit: int = 128) -> str:
        for _ in range(limit):
            if not WILDCARD_TOKEN_RE.search(s):
                break
            s = self._expand_wildcards_once(s, phase=phase)
        return s

    # -------------------- Public API -----------------------

    def expand_prompt(self, text: str, phase: str = "pos") -> str:
        if not text:
            return ""
        # Reset counters so fixed text doesn't affect picks; reproducible per call
        self._reset_counters()
        out = text
        # 1) Collapse braces first (enables optional segments via empty branches)
        out = self._expand_choices_recursively(out)
        # 2) Expand wildcards (phase controls pos/neg semantics)
        out = self._expand_wildcards_recursively(out, phase=phase)
        # 3) Collapse any braces that came from wildcard lines
        out = self._expand_choices_recursively(out)
        # 4) Cleanup duplicated commas/spaces
        out = re.sub(r"\s*,\s*,\s*", ", ", out)
        out = re.sub(r"\s{2,}", " ", out).strip().strip(", ")
        return out
