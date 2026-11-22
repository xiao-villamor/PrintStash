"""Disabled image generation must not keep readers polling for pending views."""

from app.modules.library.model_views.previews import summaries


class TestSummaries:
    def test_reports_disabled_generation(
        self, db_session, make_model, make_file, make_system_config
    ):
        model = make_model()
        file = make_file(model, filename="part.stl")
        make_system_config(derivatives_previews_enabled=False)

        result = summaries(db_session, [model])[model.id]

        assert result.model_dump() == {
            "kind": "artifact",
            "file_id": file.id,
            "cover_camera": "unknown",
            "state": "disabled",
            "reason": None,
            "published": None,
            "source_images": [],
        }

    def test_projects_gallery_without_a_mesh(
        self, db_session, make_model, make_provenance_source, make_cover
    ):
        model = make_model()
        source = make_provenance_source(model)
        images = [make_cover(source, position=index) for index in range(3)]
        result = summaries(db_session, [model])[model.id]
        assert result.kind == "none"
        assert [image.position for image in result.source_images] == [0, 1, 2]
        assert [image.id for image in result.source_images] == [
            f"source-{image.id}" for image in images
        ]
        assert all(
            image.url.startswith(
                f"/api/v1/models/{model.id}/provenance/{source.id}/cover/content?position="
            )
            for image in result.source_images
        )

    def test_keeps_the_gallery_beside_embedded_archive_images(
        self,
        db_session,
        make_model,
        make_file,
        make_derivative,
        make_provenance_source,
        make_cover,
    ):
        import json

        from app.db.models import DerivativeKind, FileType

        model = make_model()
        archive = make_file(
            model, filename="plate.gcode.3mf", file_type=FileType.THREE_MF
        )
        make_derivative(
            archive,
            DerivativeKind.METADATA,
            output_json=json.dumps(
                {
                    "geometry": "gcode_only",
                    "source_sha256": archive.sha256,
                    "archive_images": [
                        {
                            "member": "Metadata/plate_1.png",
                            "plate": 1,
                            "media_type": "image/png",
                        }
                    ],
                }
            ),
        )
        source = make_provenance_source(model)
        make_cover(source, position=0)
        result = summaries(db_session, [model])[model.id]
        assert [image.position for image in result.source_images] == [0]
        assert [image.plate for image in result.archive_images] == [1]

    def test_does_not_publish_private_gallery_in_shared_previews(
        self, db_session, make_model, make_provenance_source, make_cover
    ):
        model = make_model()
        source = make_provenance_source(model)
        make_cover(source, position=1)
        result = summaries(db_session, [model], root="/api/v1/shares/public")[model.id]
        assert result.source_images == []

    def test_gallery_address_changes_after_replacement(
        self, db_session, make_model, make_provenance_source, make_cover
    ):
        from datetime import timedelta

        model = make_model()
        source = make_provenance_source(model)
        image = make_cover(source, position=1)
        before = summaries(db_session, [model])[model.id].source_images[0].url
        image.updated_at += timedelta(seconds=1)
        db_session.add(image)
        db_session.flush()
        after = summaries(db_session, [model])[model.id].source_images[0].url
        assert before != after

    def test_fresh_install_has_only_the_ordinary_thumbnail(
        self, db_session, make_model, make_file
    ):
        from app.modules.derivatives.activity import preview_activity

        model = make_model()
        make_file(model, filename="part.stl")
        result = summaries(db_session, [model])[model.id]
        assert result.state == "disabled"
        assert result.published is None
        assert preview_activity(db_session).total == 1
