"""Generating the TypeScript the frontend imports from the Python catalog.

The frontend needs to know which providers exist, what each can do, and which
setup options to offer. Rather than duplicate that in TypeScript — where it would
drift the first time a provider changed — it is generated from the same catalog
the backend uses, and CI runs this generator in `--check` mode. A drift is a red
build, not a UI that offers a control the printer cannot perform.

That makes two properties load-bearing.

**The output is byte-for-byte deterministic.** `--check` compares file contents,
so any instability — dict ordering, a set, a timestamp — would make CI fail at
random and train everyone to re-run it.

**`--check` distinguishes missing from stale.** Both are failures, but a file
that was never generated and one that is out of date both have to be caught; a
check that only compared *existing* files would pass on a fresh clone with the
file absent.
"""

from __future__ import annotations

from pathlib import Path

from printstash_core.printers.codegen import (
    HEADER,
    main,
    render_typescript,
    write_typescript,
)


class TestRenderTypescript:
    def test_renders_the_same_output_every_time(self) -> None:
        # `--check` compares file contents, so instability here would make CI
        # fail at random.
        assert render_typescript() == render_typescript()

    def test_marks_the_output_as_generated(self) -> None:
        # Otherwise someone edits it and their change disappears on the next
        # generation.
        assert render_typescript().startswith(HEADER)

    def test_emits_the_contract_as_a_const_assertion(self) -> None:
        # `as const` is what gives the frontend literal types rather than
        # `string`, which is the whole reason for generating instead of fetching.
        assert "as const" in render_typescript()

    def test_exports_a_type_for_each_part_of_the_contract(self) -> None:
        rendered = render_typescript()

        assert "SharedPrinterProviderId" in rendered
        assert "SharedPrinterCapability" in rendered
        assert "SharedPrinterSetupOption" in rendered


class TestWriteTypescript:
    def test_writes_the_rendered_contract(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"

        assert write_typescript(output) is True
        assert output.read_text(encoding="utf-8") == render_typescript()

    def test_creates_the_parent_directory(self, tmp_path: Path) -> None:
        output = tmp_path / "generated" / "printer-contracts.ts"

        write_typescript(output)

        assert output.is_file()

    def test_reports_a_current_file_as_current(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"
        write_typescript(output)

        assert write_typescript(output, check=True) is True

    def test_reports_a_stale_file_as_not_current(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"
        output.write_text("stale\n", encoding="utf-8")

        assert write_typescript(output, check=True) is False

    def test_reports_a_missing_file_as_not_current(self, tmp_path: Path) -> None:
        # A fresh clone has no generated file at all; a check that only compared
        # existing files would pass there and fail on the next real drift.
        assert write_typescript(tmp_path / "absent.ts", check=True) is False

    def test_does_not_write_in_check_mode(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"

        write_typescript(output, check=True)

        # `--check` runs in CI against a checked-out tree; writing there would
        # make the check pass by fixing what it was meant to report.
        assert not output.exists()


class TestMain:
    def test_exits_zero_after_generating(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"

        assert main(["--output", str(output)]) == 0

    def test_exits_zero_when_the_file_is_current(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"
        main(["--output", str(output)])

        assert main(["--output", str(output), "--check"]) == 0

    def test_exits_non_zero_when_the_file_is_missing(self, tmp_path: Path) -> None:
        assert main(["--output", str(tmp_path / "absent.ts"), "--check"]) == 1

    def test_exits_non_zero_when_the_file_is_stale(self, tmp_path: Path) -> None:
        output = tmp_path / "printer-contracts.ts"
        output.write_text("stale\n", encoding="utf-8")

        # The exit code is what fails the CI job, so it is the contract rather
        # than the message.
        assert main(["--output", str(output), "--check"]) == 1
