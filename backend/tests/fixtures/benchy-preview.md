# Benchy preview reference

`benchy-preview.png` was rendered from the existing
`testdata/benchy/3dbenchy.stl` using `printstash_core.mesh.rasterizer` at
commit `9663e77a60c75a1e21f2bb4df9fbc7e5b67ebe7b`, with the filename hint
`benchy` and default 640 × 480 output. This is a regression reference for
complete mesh rendering, not an input model or a substitute for its geometry.

The assertion preserves the alpha silhouette exactly and compares displayed RGB
on black and white backgrounds within one 8-bit channel level. This accounts
for native rounding in Lanczos's unpremultiplied colors at nearly transparent
edges without allowing visible color or geometry drift.
