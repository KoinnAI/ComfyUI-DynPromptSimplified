
# ComfyUI-DynPromptSimplified

A minimal **dynamic prompting + mirrored wildcards** node for ComfyUI.

## Features
- `{a|b|{c|d}}` nested choice expansion (deterministic by seed).
- `__name__` loads `wildcards/name.txt`.
- `__name-mir__` strictly loads `wildcards/name-mir.txt`.
- In **negative** phase, if `__name__` is used and `name-mir.txt` exists, it auto-mirrors to **all options except the chosen one**.
- Deterministic selection per seed.

## Install
1. Copy this folder to: `<ComfyUI>/custom_nodes/ComfyUI-DynPromptSimplified`
2. (Optional) Put your wildcard files in `ComfyUI-DynPromptSimplified/wildcards/` or set `wildcard_dir` to any folder.
3. Restart ComfyUI.

## Node
**DynPrompt Expand (mirrored wildcards)**
- Inputs: `text`, `negative`, `seed`, `wildcard_dir`, `auto_neg_from_mir`
- Outputs: expanded `text`, expanded `negative`

Wire the outputs into your usual `CLIP Text Encode` → `KSampler` chain.

## Examples
Wildcards:
```
wildcards/hats.txt
{red hat|blue hat|green hat}

wildcards/hats-mir.txt
{red hat|blue hat|green hat}
```

Prompt:
```
a portrait wearing __hats-mir__, background {studio|outdoor|cyberpunk}
```

- Positive becomes a single chosen hat (by seed).
- Negative becomes the **other** hats, comma-separated.

## Notes
- Missing wildcard files resolve to empty strings.
- Choice/wildcard expansion is capped to prevent runaway recursion.
- If `negative` is blank and `auto_neg_from_mir` is ON, the node scans the positive for `__tokens__`
  and auto-builds a mirrored negative when possible.

License: GPLv3 (same as the original)
