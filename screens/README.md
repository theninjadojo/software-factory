# Screens of the factory's own UI

Static snapshots of the Factory page and the floor layout editor (`pages/`), their baselines (`baselines/<page>-<viewport>.png`) and
the design they were built to (`design/floor-builder-design.png`, the Factory Builder canvas at its default floor).

The UI is a Python server, so the Screens feature cannot shoot it from a checkout directly. `scripts/floor_screens.py` makes these
files from a local environment of its own (a temporary config and database with seeded tickets and three workers, GitHub faked):
it drives the pages with Playwright and checks what they show, saves each one as a static page (scripts stripped, styles inlined,
animations held at one moment, CSRF tokens blanked), renders those with the Screens feature's own sealed shooter
(`factory.screens.capture`, the `factory-screens` image) and checks them with `factory.screens.verify`, as a build would.

    docker build -t factory-screens -f sandbox/screens/Dockerfile sandbox/screens     # once
    .venv/bin/python scripts/floor_screens.py                                          # --engine podman --image localhost/factory-screens:latest on the VM

Run it again after changing the floor or the editor, look at the new baselines, and commit them with the change.

To show them on the Screens board (Screens in the UI), add the pages to the config:

```toml
[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "factory"
path = "screens/pages/factory.html"
journey = "floor"
step = 1

[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "floor-editor"
path = "screens/pages/floor-editor.html"
journey = "floor"
step = 2

[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "floor-editor-scratch"
path = "screens/pages/floor-editor-scratch.html"
viewports = ["desktop"]
journey = "floor"
step = 3

[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "floor-editor-belt"
path = "screens/pages/floor-editor-belt.html"
viewports = ["desktop"]
journey = "floor"
step = 4

[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "floor-editor-terrain"
path = "screens/pages/floor-editor-terrain.html"
viewports = ["desktop"]
journey = "floor"
step = 5

[[screens.pages]]
repo = "theninjadojo/software-factory"
name = "factory-terrain"
path = "screens/pages/factory-terrain.html"
journey = "floor"
step = 6
```

The viewports are the defaults (desktop 1440 by 900, mobile 390 by 844). The design canvas itself is an interactive prototype with a
script, which the board's design renderer refuses, so the design is kept here as a picture instead.
