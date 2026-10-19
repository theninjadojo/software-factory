# Review videos

A short narrated walkthrough of each pull request a ticket's build opens: what the change is for, the screens as built, the most
important parts of the diff, and what a reviewer should check. Each video has subtitles and a transcript. It is made after the
automated review, which writes the narration. It never blocks or changes the pull request.

## Turning it on

Everything is set up in the admin UI, on **Settings > Review videos** (also listed on the Settings home under *Before a pull request
is done*):

1. Switch on **Enable review videos** and save. The same page holds the default voice, the ElevenLabs voice id and the limits.
2. Under **Video image**, click **Build video image**. The factory builds it within a poll; it is about 0.5 GB, takes several minutes
   and is not part of a release. After that, `deploy/update-native.sh` rebuilds it with each update. (From a shell, the same is
   `docker compose --profile video build` or `podman build -t factory-video -f sandbox/video/Dockerfile sandbox/video`.)
3. On a ticket's page, open **Review video**, tick **Make a review video for each pull request**, pick a voice and save. Open pull
   requests of the ticket get a video too.

The video appears on the ticket's page (**Watch**, **Transcript**). Its link is also posted on the pull request and the ticket.
When the pull request gets new commits, the video is marked out of date, and **Make again** makes a new one.

## Videos from the chat

With videos on, you can ask for a video about some tickets ("a two-minute tour of this week's board changes") or about the code
itself ("how the login flow works, for new developers"). Describe it in the **Describe a video** box on the **Videos** page, or ask
in any conversation on the **Chat** page.

1. The chat reads the tickets it can see on the board and, while **Let the factory chat read the code** is on (Settings → Ticket
   chat, on by default), a read-only copy of each repository's default branch. It answers with a proposal: a title, a brief, and
   the tickets and files it will use.
2. Pick a voice and click **Make video**, or **Dismiss**. Nothing is made until you click. **Show code in the video** is off
   unless you asked the chat for code; leave it off and the video shows only the app's screens. A request made on the Videos page stays
   there: the reply, the proposal and the **Make video** button appear on the Videos page, above the list of videos.
3. The video is made by the same worker as review videos and counts toward the same daily limit. When it is ready, or if it is
   skipped, the conversation that asked for it says so. Videos are listed on the **Videos** page (`/videos`), with **Watch**,
   **Transcript** and **Make again**.

Reading the code: the factory keeps a shallow clone of each configured repository's default branch in `state/code/` (refreshed at
most every 15 minutes, before a chat reply or a video writer run; a repository over 500 MB is skipped). The runs get the copies
mounted read-only at `/work/code`, still with no network and no GitHub token, and the clone itself holds no credential. A code scene
names a file and a line range; the factory reads those lines from its copy (at most 40, text files only, never through a symlink).
Factory chat replies get more turns and time than a ticket chat for this (Settings → Ticket chat, Advanced).

### Showing a flow in the app

A video can show a flow running in the app, such as signing in or checking out, with a screen after each step. This needs a
[worker](workers.md) that runs the repository's Playwright tests (**Settings → Screens**, the Playwright run of the Screens board)
and a test that walks through the flow.

1. Ask for it like any other video ("demo the login, step by step"). The chat looks for a Playwright test that does the steps
   (`*.spec.ts` or `*.test.ts` in the code copy). It adds that test to the proposal under **Showing these tests running in the
   app**. If no test walks through the flow, it proposes no video and says what is missing (a test that does the flow end to end).
2. When you click **Make video**, the video waits while a worker runs those test files on the default branch (the Videos page says
   *Running the demo tests on a worker first*). The run records a Playwright trace with each action slowed by 400 ms, and the
   worker keeps the screen after each step of each test, with what the step did ("Click Sign in", "Fill Email"). A typed value
   is never kept as text. The worker also notes where each step acted: the point a click landed on, and the field a value was typed
   into (the part of the screen that changed). For a click, a key press or a page load it also keeps up to six of the frames
   taken while the step ran, with their times: a menu opening, a page loading. A step's screen is the one the page settled on
   before the next step started (for the last step, once the test's last check is done), so a "Welcome back" after a short wait is
   shown. It also notes the element each step acted on, the key a press pressed (a named key such as Enter or Control+A, never a
   letter on its own, so a value typed key by key is not shown) and the page's path while it is known.
3. The writer gets those steps in order and narrates the ones that tell the flow.
4. Each step plays out on the real screens like a screen recording: the video starts on the screen before the step, the camera
   pushes in on what the step acts on while a pointer glides there, then it either clicks (a press and a ripple, then the frames
   the run took play in their own time, or the next screen fades in when there are none) or types (the field is outlined and the
   value appears in it letter by letter behind a caret, just as the app showed it: a password stays dots). The camera then eases
   back out to the whole screen. Steps of one test that follow each other cut straight on, with the pointer where the last step
   left it; anything else fades through black. A test's first step, and a step whose screen size changed, is shown as a still.
   The page sits in a browser window whose address bar shows the page's path while it is known (blank after a click that may have
   left it). Just before a step acts, the page around what it acts on dims and a label names it ("Sign in"); a key press shows its
   keycap ("⏎ Enter"). The step waits until its narration is about half said, so the click lands on the words, and waits in the
   frames it plays (a page loading for two seconds) are sped up, while quick changes such as a menu opening keep their speed.

The demo run is not shown on the Screens board and does not count as a result in the Tests register. A video waits at most an hour
for it. A video is never made without the steps it promised: if the tests could not be run (no worker runs that repository's
Playwright tests), did not finish within the hour, failed, or took no step screens, the video is skipped and the conversation says
which. **Make again** runs the tests again, so the video shows the app as it is now. Step screens
are taken at the test's viewport size (a video is recorded only for that, and not kept). Only JavaScript Playwright suites have steps; a Python suite in `tests/e2e` does not.

How it is made:
- **Material.** For each ticket: its build pull requests that are open or merged (up to two per ticket, six in all, with the
  changed files and their diff hunks) and the screenshots its builds took. Also the newest shot of each page on the
  [Screens board](ui.md) (one viewport per page, the app as it looks on the default branch; up to 12), taken from the
  video's repositories, or from every repository when those have none. Keep the board fresh (**Settings → Screens**, *board
  every*) so videos show the app as it is now.
- **Screens, not code.** Unless **Show code in the video** was ticked, the writer is told to show screens only, and any code
  scene it writes is dropped. The diffs and the code copies are still given to it, so it understands what changed.
- **Writer.** A sealed, read-only run with the chat's model and limits (no repository, no GitHub token) gets the brief and the
  material and writes the scenes. Its output is untrusted and checked like the reviewer's block. A code scene must name a pull
  request and file from the material, and the lines shown come from that diff. A screen scene must name a screenshot from the
  material. Anything else is dropped.
- **Nothing missing, no fallback.** The factory never makes a lesser video in place of the one asked for. Before proposing, the
  chat checks the video can show what was asked (it knows which pages the Screens board has and which repositories can run
  demos); if something is missing it says what and how to fix it. When the video is made, it is skipped with a note in the
  conversation (and the reason on the Videos page) when: a demo did not run in full; there are no screens to show (and code was
  not asked for); or the writer's scenes show no screen or code.
- Nothing is posted on GitHub for these videos.

## Voices

| Voice | Where it runs | What leaves the machine |
|---|---|---|
| Piper (default) | Inside the video container, with no network. The voice is `en_US-ljspeech-medium`, trained on public-domain recordings. | Nothing |
| ElevenLabs | The factory calls `api.elevenlabs.io` and passes the sound into the container. | The narration text of each scene |

To use ElevenLabs:
1. Save the key under **ElevenLabs key** on **Settings > Review videos** (or on **Settings > Credentials**). **Test key** checks the key
   and the configured voice.
2. Set **ElevenLabs voice id** on the same page, or give a voice id on the ticket.
3. To make ElevenLabs the default for every ticket, set **Default voice** to elevenlabs.

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

## Chat conversations

The **Chat** page keeps separate conversations. Each reply is written with only its own conversation as context (the newest
`chat.context_turns` turns of it), so a new topic in a new conversation is not coloured by earlier ones. **New conversation**
starts one; the list beside the chat (a menu above it on a phone) switches between them. **Delete this conversation** and
**Delete other conversations** clean up; a conversation waiting for a reply is kept. Videos a conversation asked for stay on the
Videos page when it is deleted. Messages from before conversations existed are kept as one conversation, "Earlier conversation".
