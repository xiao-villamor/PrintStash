"""Mount discovery lists directories without descending into model trees."""

from app.services.library_locations import mounted_directories


class TestMountedDirectories:
    def test_discovers_a_directory_with_an_escaped_space(self, tmp_path):
        directory = tmp_path / "NAS models"
        directory.mkdir()
        table = tmp_path / "mountinfo"
        escaped = str(directory).replace(" ", r"\040")
        table.write_text(f"1 0 1:1 / {escaped} ro - ext4 /dev/test rw\n")
        assert mounted_directories(table) == [directory]

    def test_does_not_offer_operating_system_mounts(self, tmp_path):
        table = tmp_path / "mountinfo"
        table.write_text(
            "1 0 1:1 / / ro - ext4 /dev/test rw\n2 1 1:2 / /proc ro - proc proc rw\n"
        )
        assert mounted_directories(table) == []

    def test_platform_without_mountinfo_has_no_suggestions(self, tmp_path):
        assert mounted_directories(tmp_path / "missing") == []

    def test_does_not_offer_individually_mounted_files(self, tmp_path):
        mounted = tmp_path / "configuration"
        mounted.write_text("private")
        table = tmp_path / "mountinfo"
        table.write_text(f"1 0 1:1 / {mounted} ro - ext4 /dev/test rw\n")
        assert mounted_directories(table) == []

    def test_ignores_invalid_mount_records(self, tmp_path):
        table = tmp_path / "mountinfo"
        table.write_text("incomplete\n1 0 1:1 / relative ro - ext4 /dev/test rw\n")
        assert mounted_directories(table) == []

    def test_limits_mount_table_inspection(self, tmp_path):
        directory = tmp_path / "models"
        directory.mkdir()
        table = tmp_path / "mountinfo"
        table.write_text(
            "incomplete\n" * 2048 + f"1 0 1:1 / {directory} ro - ext4 /dev/test rw\n"
        )
        assert mounted_directories(table) == []
