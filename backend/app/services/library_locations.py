"""Discover container-visible mount points without traversing users' files."""

from pathlib import Path


def mounted_directories(mountinfo: Path = Path("/proc/self/mountinfo")) -> list[Path]:
    candidates: set[Path] = set()
    try:
        with mountinfo.open() as mounts:
            for index, line in enumerate(mounts):
                if index >= 2048:
                    break
                fields = line.split()
                if len(fields) < 6:
                    continue
                path = fields[4]
                for escaped, plain in (
                    (r"\040", " "),
                    (r"\011", "\t"),
                    (r"\134", "\\"),
                ):
                    path = path.replace(escaped, plain)
                candidate = Path(path)
                if not candidate.is_absolute() or candidate == Path("/"):
                    continue
                if any(
                    candidate == root or root in candidate.parents
                    for root in (
                        Path("/proc"),
                        Path("/sys"),
                        Path("/dev"),
                        Path("/etc"),
                    )
                ):
                    continue
                if candidate.is_dir():
                    candidates.add(candidate)
    except OSError:
        pass
    return sorted(candidates)
