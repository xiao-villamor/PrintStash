# Stashy emoji pack

Stashy is a little printer bot drawn from the PrintStash mark. The white P
forms its housing, the counter becomes a face, and the descending stroke
carries the nozzle over a blue print bed. The pack includes the original
PrintStash badge, three still reactions, and four animated reactions.

## Build and share

From the repository root, run this command with [uv](https://docs.astral.sh/uv/).

```sh
uv run --project .agents/skills/emoji-forge --locked forge all \
  branding/emoji/pack.json branding/emoji/out/stashy --allow-scripts
```

Use a fresh output directory for each build. Upload the individual PNG and GIF
files from `out/stashy/pack/` as custom emoji. The same files and their suggested
names are in `out/stashy/pack.zip`. The exports are transparent 128 × 128 px
images and use Emoji Forge's Slack preset. Open `out/stashy/preview.gif` for the
animated preview.

[pack.json](pack.json) defines the names and uses. [stashy.py](stashy.py) draws the
poses and supplies the GIF frame holds. The badge uses the existing
[PrintStash logo](../../frontend/public/logo.png).

The project vendors [Emoji Forge at commit
23a4697](https://github.com/ppazosp/emoji-forge/tree/23a46976c319aa3585ae7b4ff16b8072cc152cf9)
under its [MIT license](../../.agents/skills/emoji-forge/LICENSE).
