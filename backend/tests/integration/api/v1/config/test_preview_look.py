"""Every signed-in viewer reads the look that generated Model images use."""

from printstash_core.mesh.preview_profile import PreviewLook


class TestPreviewLookRead:
    def test_member_reads_the_configured_look(
        self, client, user_headers, make_system_config
    ):
        make_system_config(preview_look=PreviewLook.GRAPHITE)
        response = client.get("/api/v1/config/preview-look", headers=user_headers())
        assert response.status_code == 200, response.text
        assert response.json() == {"preview_look": "graphite"}

    def test_unconfigured_library_reads_the_default_look(self, client, auth_headers):
        response = client.get("/api/v1/config/preview-look", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"preview_look": "sky"}

    def test_anonymous_cannot_read_the_look(self, client):
        assert client.get("/api/v1/config/preview-look").status_code == 401
