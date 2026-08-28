# `tests/contract` — our client against a real fake, over a real socket

Every test here drives a **contract-enforcing fake** — a Moonraker/PrusaLink/OctoPrint
emulator, a Bambu MQTT+FTPS server, an OIDC provider — bound to a loopback socket. The
transport is real. The protocol is real. Only the machine is not.

**Never mock inside one of these.** The moment a test patches or overrides a seam to force
a failure, it stops being a contract test on that path: it is a unit test in an
integration costume, and it proves nothing about the real system.

The fault you want decides where the test goes:

- **Reachable against the real fake, deterministically → stays here, as a real fault.**
  The emulators take flags for exactly this: `reject_commands=True`, a wrong
  `expected_access_code`, `PrintSim` driven to `ERROR`, the `/flaky/{key}` webhook target,
  `--auth-mode` on PrusaLink. If the fault you need is not there yet, **add a flag to the
  fake** rather than patching from the test.
- **A dependency misbehaving on cue** — raises once, returns garbage, times out N times
  then succeeds → that is logic over the dependency's *outcome*. It belongs in `unit/` or
  `integration/` with the egress patched.

The smell test: if making a contract test fail requires replacing part of the real system
with a fake, that assertion belongs in an integration test.

The fakes live in `tests/fakes/`, shared with `e2e/`.
