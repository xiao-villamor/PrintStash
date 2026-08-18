from __future__ import annotations

from printstash_core.printers.codegen import HEADER, main, render_typescript


def test_typescript_generation_is_deterministic() -> None:
    first = render_typescript()
    assert first == render_typescript()
    assert first.startswith(HEADER)
    assert "as const" in first
    assert "SharedPrinterProviderId" in first


def test_codegen_check_detects_current_and_stale_output(tmp_path) -> None:
    output = tmp_path / "printer-contracts.ts"
    assert main(["--output", str(output), "--check"]) == 1
    assert main(["--output", str(output)]) == 0
    assert main(["--output", str(output), "--check"]) == 0
    output.write_text("stale\n", encoding="utf-8")
    assert main(["--output", str(output), "--check"]) == 1
