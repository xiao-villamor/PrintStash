"""Copy individual files under their exact emoji names and write a ZIP."""

from pathlib import Path
from shutil import copyfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

if __package__:
    from .common import cli, output_path, pack_entries
else:
    from common import cli, output_path, pack_entries


def configure(parser):
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path, help="new distribution folder")
    parser.add_argument("--zip", type=Path, help="defaults to a sibling of the output folder")


def bundle(args):
    entries = list(pack_entries(args.manifest))
    archive = args.zip or args.output.with_name(args.output.name + ".zip")
    if archive.exists() or archive.resolve().is_relative_to(args.output.resolve()):
        raise ValueError("ZIP must be a new path outside the distribution folder")
    for _, path in entries:
        if not path.is_file() or path.resolve() == archive.resolve():
            raise ValueError(f"invalid pack source: {path}")
    args.output.mkdir(parents=True)
    names = ["# Emoji names", "", "| Order | Emoji | When to use | Size |", "| ---: | --- | --- | ---: |"]
    files = []
    for index, (entry, source) in enumerate(entries, 1):
        target = args.output / (entry["name"] + source.suffix.lower())
        copyfile(source, target)
        usage = " ".join(entry.get("use", "").split()).replace("|", "&#124;")
        names.append(f"| {index} | `{entry['name']}` | {usage} | {target.stat().st_size:,} B |")
        files.append(target)
    manifest = args.output / "NAMES.md"
    manifest.write_text("\n".join(names) + "\n", encoding="utf-8")
    with ZipFile(output_path(archive), "x") as zipped:
        for path in [manifest, *files]:
            info = ZipInfo(path.name)
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED)


if __name__ == "__main__":
    cli(__doc__, configure, bundle)
