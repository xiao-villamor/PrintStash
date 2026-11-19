# Benchy preview reference

`benchy-preview.png` was rendered from the existing
`testdata/benchy/3dbenchy.stl` using `printstash_core.mesh.rasterizer` at
studio recipe `qualified-portable-v8` (preview profile 8, Sky with floor grid), with the filename hint
`benchy` and default 640 × 480 output. This is a regression reference for
complete mesh rendering, not an input model or a substitute for its geometry.

The assertion preserves the alpha silhouette exactly and compares displayed RGB
on black and white backgrounds within one 8-bit channel level. This accounts
for native rounding in Lanczos's unpremultiplied colors at nearly transparent
edges without allowing visible color or geometry drift.
