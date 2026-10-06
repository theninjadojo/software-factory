# Scene packs

A scene pack adds your own picture, and a little story for it, to the floor editor. One example is the bench whose old man walks to
the nearest water to feed the ducks. Anyone signed in can upload one under **Floor layout → Scene packs**. Once uploaded it is listed
under **Your scenes** in the build panel and is placed like a park piece.

A pack is **data, not code**. It cannot run anything in the browser or on the server:

- `scene.json` only fills in a fixed set of steps. Anything else in it is refused.
- Every SVG is redrawn from scratch, from an allowlist of shapes and animations. Whatever is not on the list is left out, and the
  upload page says what was left out.
- The pictures are drawn inside fixed, clipped boxes, so a pack cannot draw over the rest of the floor.

## The zip

```
duck-feeder.zip
  scene.json
  bench.svg      the scene's picture
  sit.svg        the actor's poses
  walk.svg
  feed.svg
```

Rules for the zip:

- At most 512 KB, 16 files, and 1 MB unpacked.
- Each SVG is at most 200 KB.
- It may hold only `scene.json` and `.svg` files. Folders inside the zip are ignored, and only the file names are used.

`docs/scene-packs/duck-feeder/` is a complete example. Zip that folder and upload it.

## scene.json

```json
{
  "format": 1,
  "name": "Duck feeder",
  "size": [70, 40],
  "art": "bench.svg",
  "actor": {
    "at": [35, 37],
    "poses": {"sit": "sit.svg", "walk": "walk.svg", "feed": "feed.svg"},
    "rest": "sit",
    "target": "water",
    "reach": 600,
    "steps": [
      {"pose": "sit",  "seconds": 7.3},
      {"pose": "walk", "seconds": 5.6, "walk": "there"},
      {"pose": "feed", "seconds": 8},
      {"pose": "walk", "seconds": 5.6, "walk": "home"},
      {"pose": "sit",  "seconds": 1.5}
    ]
  }
}
```

| Key | What it is |
| --- | --- |
| `format` | Always `1`. |
| `name` | 1 to 40 characters. It also gives the pack its id (`Duck feeder` → `sc-duck-feeder`). Uploading a pack with the same name replaces the old one. |
| `size` | `[width, height]` in floor pixels, 20–400 wide and 20–300 high. This is the scene's hitbox. |
| `art` | Optional. The scene's picture, drawn in the box's own pixels (0,0 is the top left). |
| `actor` | Optional. Someone or something that walks. A scene needs `art`, an `actor`, or both. |
| `actor.at` | `[x, y]` inside the box: where the actor stands at home. |
| `actor.poses` | 1 to 8 names, each an SVG file. A pose is drawn round the actor's feet at (0, 0), facing right. It is clipped to x −60…60 and y −80…20. |
| `actor.rest` | The pose shown when there is nothing to walk to. |
| `actor.target` | `"water"` (the nearest pond, river or water tile) or `"tree"` (the nearest tree, pine or bush). |
| `actor.reach` | How far the actor will walk, from 50 to 1000 pixels. The default is 600. |
| `actor.steps` | 1 to 12 steps, played in a loop. Each step shows `pose` for `seconds` (0.2–120). With `"walk": "there"` the actor walks to the target during the step, and with `"walk": "home"` it walks back. The steps must end with the actor at home. |

The actor faces the way it walks, and it keeps facing the target while it is there. Placing a scene with an odd seed mirrors it, as
with the park pieces.

## What an SVG may contain

The outer `<svg>` element is a wrapper. Its own attributes, `viewBox` included, are ignored. What is inside it is kept only if it is
on these lists.

**Elements:** `g`, `path`, `circle`, `ellipse`, `rect`, `line`, `polyline`, `polygon`, `defs`, `linearGradient`,
`radialGradient`, `stop`, `animate`, `animateTransform`, `animateMotion`.

**Attributes:** geometry (`x y width height rx ry cx cy r x1 y1 x2 y2 points d`), `transform`, `opacity`, the `fill`, `stroke` and
`stop` paint attributes, the gradient attributes, `id`, and the SMIL timing attributes (`attributeName type values from to by dur
begin end repeatCount repeatDur keyTimes keySplines keyPoints calcMode additive accumulate fill path rotate`).

**Values:**

- `fill` and `stroke` may use `url(#id)` only for a gradient defined in the same file. Ids are renamed into the pack's own, so a pack
  cannot reach anything else on the page.
- `begin` and `end` must be plain times such as `-0.3s` or `2s`. Events like `click` are not allowed, and neither is `indefinite`.
- `dur` must be at least 0.1s.
- An animation may change only drawing attributes, never links.

**Refused outright:** a DOCTYPE, entities, a stylesheet instruction, a file that is not UTF-8 or not well-formed, more than 1,500
elements in one file, or more than 3,000 elements in the pack.

**Left out quietly:** everything else. That includes `<script>`, `<foreignObject>`, `<use>`, `<image>`, `<text>`, `<style>`,
`style=` and `class=` attributes, event handlers (`onload` and the like), and `href` / `xlink:href`.

## Removing a pack

Use **Remove** next to the pack under Scene packs. A pack that is still placed on the saved layout cannot be removed: erase it from the
layout and save first.
