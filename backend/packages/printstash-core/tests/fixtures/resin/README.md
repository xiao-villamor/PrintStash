# Resin header fixtures

These are authored, non-printable binary fixtures, containing metadata and embedded
previews only. Never send them to a printer. The expected values are 120 layers,
0.05 mm layer height, 2.5 s exposure, 600 s print time, and 2.5 ml resin.

- CTB v2: 2×2 red, green, blue, white RGB555 preview; printer Fixture CTB.
  Layout: https://github.com/cbiffle/catibo/blob/master/doc/cbddlp-ctb.adoc
- GOO V3.0: 290×290 red RGB565 preview; printer Fixture GOO, slicer FixtureSlice 1.2.3.
  Layout: https://github.com/elegooofficial/GOO/blob/main/Goo%20Format%20Spec%20V1.2.pdf

The tests mutate copies to exercise version, size, run-length and truncation
boundaries. Real printer output may omit optional CTB fields; those stay null.
