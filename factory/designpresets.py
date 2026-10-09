"""The design libraries that ship with the factory (read-only; a person duplicates one to make it their own). See designlib.py."""

_TYPE = {"display": {"size": 40, "weight": 700}, "title": {"size": 28, "weight": 700}, "heading": {"size": 20, "weight": 600},
         "body": {"size": 16, "weight": 400}, "small": {"size": 13, "weight": 400}}
_SPACING = [4, 8, 12, 16, 24, 32, 48]

PRESETS = {
    "neutral": {
        "name": "Neutral",
        "blurb": "Plain and quiet: a light page, one blue accent, even spacing. Used when nothing else is picked.",
        "tokens": {
            "colors": {"page": "#ffffff", "surface": "#f6f8fa", "ink": "#1f2328", "muted": "#59636e", "line": "#d1d9e0",
                       "primary": "#0969da", "on-primary": "#ffffff", "accent": "#8250df", "error": "#cf222e", "success": "#1a7f37"},
            "fonts": {"heading": "IBM Plex Sans", "body": "IBM Plex Sans", "mono": "IBM Plex Mono"},
            "type": _TYPE, "spacing": _SPACING, "radius": {"sm": 4, "md": 6, "lg": 12, "full": 999},
            "shadow": {"card": "0 1px 3px rgba(31,35,40,0.12)"}},
        "rules": "One primary button per screen; other actions are secondary buttons.\n"
                 "Sentence case for labels and headings.\n"
                 "Errors say what went wrong and how to fix it, under the field they belong to.\n"
                 "Keep to the spacing scale; never mix two spacings for the same kind of gap.",
    },
    "shikumi": {
        "name": "Shikumi",
        "blurb": "This factory's own look: dark panels, an amber accent, square corners.",
        "tokens": {
            "colors": {"page": "#12161a", "surface": "#1b2126", "ink": "#e8edf0", "muted": "#a9b5bd", "line": "#2d363d",
                       "primary": "#8fa8ff", "on-primary": "#0e1330", "accent": "#f2a93b", "error": "#ff6b5e", "success": "#52c7a1"},
            "fonts": {"heading": "Space Grotesk", "body": "Space Grotesk", "mono": "JetBrains Mono"},
            "type": {**_TYPE, "title": {"size": 32, "weight": 700}}, "spacing": _SPACING, "radius": {"sm": 0, "md": 0, "lg": 10, "full": 999}},
        "rules": "Dark surfaces only; panels sit on the page with a one-pixel line, no shadows.\n"
                 "Amber marks what is active or needs attention; blue is for buttons.\n"
                 "Square corners on buttons and inputs; round only badges and pills.\n"
                 "Touch targets are at least 44 px high.",
    },
    "material": {
        "name": "Material-style",
        "blurb": "Tonal surfaces, pill buttons and generous touch targets, for Android-first apps.",
        "tokens": {
            "colors": {"page": "#fffbfe", "surface": "#f3edf7", "ink": "#1c1b1f", "muted": "#49454f", "line": "#cac4d0",
                       "primary": "#6750a4", "on-primary": "#ffffff", "accent": "#7d5260", "error": "#b3261e", "success": "#146c2e"},
            "fonts": {"heading": "Roboto Flex", "body": "Roboto Flex", "mono": "Roboto Mono"},
            "type": {**_TYPE, "display": {"size": 36, "weight": 400}, "title": {"size": 28, "weight": 400}, "heading": {"size": 22, "weight": 500}},
            "spacing": _SPACING, "radius": {"sm": 8, "md": 12, "lg": 28, "full": 999},
            "shadow": {"card": "0 1px 2px rgba(0,0,0,0.3)"}},
        "rules": "Buttons are pills (full radius); cards use the md radius.\n"
                 "Tonal surfaces separate areas instead of lines where possible.\n"
                 "Touch targets are at least 48 px.\n"
                 "One floating action at most per screen.",
    },
    "ios": {
        "name": "iOS-style",
        "blurb": "Grouped lists, system blue and large titles, for iPhone-first apps.",
        "tokens": {
            "colors": {"page": "#f2f2f7", "surface": "#ffffff", "ink": "#000000", "muted": "#6c6c70", "line": "#d1d1d6",
                       "primary": "#0a64d6", "on-primary": "#ffffff", "accent": "#af52de", "error": "#d70015", "success": "#248a3d"},
            "fonts": {"heading": "system-ui", "body": "system-ui", "mono": "ui-monospace"},
            "type": {"display": {"size": 34, "weight": 700}, "title": {"size": 28, "weight": 700}, "heading": {"size": 17, "weight": 600},
                     "body": {"size": 17, "weight": 400}, "small": {"size": 13, "weight": 400}},
            "spacing": [4, 8, 12, 16, 20, 32, 44], "radius": {"sm": 6, "md": 10, "lg": 14, "full": 999}},
        "rules": "Large titles on top-level screens; inline titles once scrolled.\n"
                 "Settings-like content is grouped lists on the grey page.\n"
                 "Destructive actions are red text, never red buttons.\n"
                 "Touch targets are at least 44 points.",
    },
    "dense": {
        "name": "Dense dashboard",
        "blurb": "Dark, compact and data-heavy: small type, tight rows and tabular numbers.",
        "tokens": {
            "colors": {"page": "#0f172a", "surface": "#1e293b", "ink": "#e2e8f0", "muted": "#94a3b8", "line": "#334155",
                       "primary": "#38bdf8", "on-primary": "#0f172a", "accent": "#a78bfa", "error": "#f87171", "success": "#4ade80"},
            "fonts": {"heading": "IBM Plex Sans Condensed", "body": "IBM Plex Sans Condensed", "mono": "IBM Plex Mono"},
            "type": {"display": {"size": 28, "weight": 600}, "title": {"size": 20, "weight": 600}, "heading": {"size": 16, "weight": 600},
                     "body": {"size": 14, "weight": 400}, "small": {"size": 12, "weight": 400}},
            "spacing": [2, 4, 8, 12, 16, 24, 32], "radius": {"sm": 2, "md": 4, "lg": 6, "full": 999}},
        "rules": "Numbers use tabular figures and line up on the right.\n"
                 "Tables are the default way to show many records; rows are 32 px.\n"
                 "Colour carries meaning (good, bad, selected), never decoration.\n"
                 "Keep to one accent per chart.",
    },
    "friendly": {
        "name": "Friendly",
        "blurb": "Warm and rounded, for consumer apps: soft colours and big friendly buttons.",
        "tokens": {
            "colors": {"page": "#fff8f0", "surface": "#ffffff", "ink": "#3d2c29", "muted": "#7a625c", "line": "#f0d9c8",
                       "primary": "#c2405a", "on-primary": "#ffffff", "accent": "#f4a259", "error": "#b42318", "success": "#2e7d4f"},
            "fonts": {"heading": "Nunito", "body": "Nunito", "mono": "JetBrains Mono"},
            "type": {**_TYPE, "display": {"size": 40, "weight": 800}, "title": {"size": 28, "weight": 800}},
            "spacing": _SPACING, "radius": {"sm": 8, "md": 16, "lg": 24, "full": 999},
            "shadow": {"card": "0 4px 16px rgba(61,44,41,0.08)"}},
        "rules": "Write like a person: short, warm sentences, second person.\n"
                 "Big rounded buttons (md radius), at least 48 px high.\n"
                 "The accent is for highlights and illustrations, never for text.\n"
                 "Empty states always say what to do next.",
    },
}
