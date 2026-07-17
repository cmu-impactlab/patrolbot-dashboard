"""Built-in dashboard layout presets.

Widget ids must match frontend/src/widgets/registry.ts. Grid units follow
react-grid-layout with 12 columns; one h unit ≈ 42 px (32 px row + 10 px
margin), so h=9 ≈ 370 px. Heights are sized so widget content fits without
inner scrollbars at default width.
"""

PRESET_LAYOUTS = {
    "Operator": {
        "widgets": ["robotStatus", "navControls", "liveMap", "battery", "alerts"],
        "layouts": {
            "lg": [
                {"i": "robotStatus", "x": 0, "y": 0, "w": 3, "h": 9},
                {"i": "navControls", "x": 0, "y": 9, "w": 3, "h": 9},
                {"i": "liveMap", "x": 3, "y": 0, "w": 6, "h": 22},
                {"i": "battery", "x": 9, "y": 0, "w": 3, "h": 12},
                {"i": "alerts", "x": 9, "y": 12, "w": 3, "h": 10},
            ]
        },
    },
    "Research": {
        "widgets": ["liveMap", "robotStatus", "battery", "piStats", "alerts", "recordings"],
        "layouts": {
            "lg": [
                {"i": "liveMap", "x": 0, "y": 0, "w": 7, "h": 21},
                {"i": "robotStatus", "x": 7, "y": 0, "w": 5, "h": 9},
                {"i": "battery", "x": 7, "y": 9, "w": 5, "h": 12},
                {"i": "piStats", "x": 0, "y": 21, "w": 4, "h": 12},
                {"i": "recordings", "x": 4, "y": 21, "w": 4, "h": 12},
                {"i": "alerts", "x": 8, "y": 21, "w": 4, "h": 12},
            ]
        },
    },
    "Diagnostics": {
        "widgets": ["systemHealth", "alerts", "piStats", "robotStatus", "battery", "liveMap", "bumpers"],
        "layouts": {
            "lg": [
                {"i": "systemHealth", "x": 0, "y": 0, "w": 4, "h": 13},
                {"i": "alerts", "x": 4, "y": 0, "w": 4, "h": 13},
                {"i": "piStats", "x": 8, "y": 0, "w": 4, "h": 10},
                {"i": "robotStatus", "x": 8, "y": 10, "w": 4, "h": 9},
                {"i": "battery", "x": 0, "y": 13, "w": 3, "h": 13},
                {"i": "bumpers", "x": 3, "y": 13, "w": 3, "h": 13},
                {"i": "liveMap", "x": 6, "y": 19, "w": 6, "h": 12},
            ]
        },
    },
}
