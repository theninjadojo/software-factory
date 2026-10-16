# Review videos

A short narrated walkthrough of each pull request a ticket's build opens: what the change is for, the screens as built, the most
important parts of the diff, and what a reviewer should check. Each video has subtitles and a transcript. It is made after the
automated review, which writes the narration. It never blocks or changes the pull request.

## Turning it on

1. Build the video image once on the factory's machine. It is about 0.5 GB and is not part of a release:

   ```bash
   docker compose --profile video build                                       # Docker install
   podman build -t factory-video -f sandbox/video/Dockerfile sandbox/video    # native install
   ```

   After that, `deploy/update-native.sh` rebuilds it with each update.
2. Set `[videos] enabled = true` in the configuration. The other settings are described in `config.example.toml`.
3. On a ticket's page in the admin UI, open **Review video**, tick **Make a review video for each pull request**, pick a voice and
   save. Open pull requests of the ticket get a video too.

The video appears on the ticket's page (**Watch**, **Transcript**). Its link is also posted on the pull request and the ticket.
When the pull request gets new commits, the video is marked out of date, and **Make again** makes a new one.

## Voices

| Voice | Where it runs | What leaves the machine |
|---|---|---|
| Piper (default) | Inside the video container, with no network. The voice is `en_US-ljspeech-medium`, trained on public-domain recordings. | Nothing |
| ElevenLabs | The factory calls `api.elevenlabs.io` and passes the sound into the container. | The narration text of each scene |

To use ElevenLabs:
1. Save the key on **Settings > Credentials** (ElevenLabs key). **Test** checks the key and the configured voice.
2. Set `videos.elevenlabs_voice_id`, or give a voice id on the ticket.
3. To make ElevenLabs the default for every ticket, set `voice = "elevenlabs"`.

A video whose voice cannot be made is skipped with a note on the ticket. Common causes are a refused key, an unknown voice, or a used-up quota.

## How a video is made

1. **Narration.** When a ticket has videos turned on, the automated review ends with a `factory-video` block. It holds plain-text
   scenes (title, code, screen, points) for each pull request. The block is untrusted model output and is used only as data:
   - Each scene is checked against fixed kinds and lengths.
   - Text is cleaned (no Markdown, tags or links).
   - A code scene may only name a file the pull request changes. The lines shown come from the pull request's own diff, never from the block.
   - A screen scene may only name a screenshot the build took.

   Without a usable block (reviews are off, or videos were turned on after the review), the narration is fixed wording made from the file list.
2. **Waiting for the review.** A pull request's video waits up to an hour for its automated review, when one is going to run.
   A video already made from fixed wording is made again once the review's narration arrives.
3. **Rendering.** `sandbox/video` holds a Remotion composition with fixed templates. It renders all text as React text, never as HTML.
   - The container runs with no network, a read-only root and no capabilities.
   - It sees only `/in` (`script.json`, the screenshots and any ElevenLabs sound), read-only, and writes `/out`.
   - `render.mjs` checks `script.json` again before rendering.
4. **Checks on the result.** The MP4 is accepted only if it is a real MP4 with one H.264 track and at most one AAC track, within
   `max_seconds` and `max_mb`. It is stored under a name the factory picks in `state/videos/` (not in backups) and is removed after
   `retention_days`. At most `max_per_day` videos are started in any 24 hours.

## Licences

Remotion is free for individuals and for companies of up to three people. Larger companies need a
[Remotion company licence](https://www.remotion.dev/license). Piper is MIT-licensed. The bundled voice is trained on LJSpeech, which
is in the public domain.
