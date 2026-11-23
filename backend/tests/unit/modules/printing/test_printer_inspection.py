"""The workbench reads Moonraker's stored samples and Klipper's actual probe grid."""

import pytest

from app.modules.printing.printer_inspection import (
    InvalidInspectionResponse,
    read_bed_mesh,
    read_heater_history,
)
from app.schemas.printer_inspection import ActiveBedMesh


def _with_mesh(mesh: object) -> dict:
    return {"result": {"status": {"bed_mesh": mesh}}}


class TestHeaterHistory:
    def test_temperature_history_limits_the_window(self) -> None:
        history = read_heater_history(
            {"result": {"extruder": {"temperatures": list(range(600))}}}
        )

        assert history.sample_interval_ms == 1000
        assert [sample.temperature for sample in history.extruder] == list(
            range(299, 600)
        )
        assert [sample.age_ms for sample in history.extruder] == list(
            range(300_000, -1, -1000)
        )
        assert history.heater_bed == []

    def test_temperature_history_preserves_partial_samples(self) -> None:
        history = read_heater_history(
            {
                "result": {
                    "extruder": {
                        "temperatures": [21, None, 0, "211", True],
                        "targets": [200, 0],
                        "powers": [0, 0, 0, 0, 0],
                    },
                    "heater_bed": {"temperatures": [22, float("inf")]},
                    "heater_generic chamber": {"temperatures": [50]},
                }
            }
        )

        assert [(s.age_ms, s.temperature, s.target) for s in history.extruder] == [
            (4000, 21, None),
            (2000, 0, None),
            (1000, None, 200),
            (0, None, 0),
        ]
        assert [(s.age_ms, s.temperature, s.target) for s in history.heater_bed] == [
            (1000, 22, None)
        ]

    def test_empty_temperature_store_exposes_no_samples(self) -> None:
        history = read_heater_history({"result": {}})

        assert history.extruder == history.heater_bed == []

    @pytest.mark.parametrize(
        "result", [None, [], {"extruder": True}, {"extruder": {"targets": 0}}]
    )
    def test_malformed_temperature_store_is_rejected(self, result: object) -> None:
        with pytest.raises(InvalidInspectionResponse):
            read_heater_history({"result": result})


class TestBedMesh:
    def test_active_mesh_uses_probed_values(self) -> None:
        mesh = read_bed_mesh(
            _with_mesh(
                {
                    "profile_name": "first-layer",
                    "mesh_min": [10, 15],
                    "mesh_max": [210, 215],
                    "probed_matrix": [[-0.125, 0, 0.125], [0.25, 0.375, 0.125]],
                    "mesh_matrix": [[0, 0, 9, 9, 9], [0, 0, 9, 9, 9]],
                }
            )
        )

        assert isinstance(mesh, ActiveBedMesh)
        assert mesh.profile_name == "first-layer"
        assert mesh.mesh_min == (10, 15)
        assert mesh.mesh_max == (210, 215)
        assert mesh.probed_matrix == [[-0.125, 0, 0.125], [0.25, 0.375, 0.125]]
        assert mesh.variation_mm == 0.5

    def test_flat_mesh_with_real_extents_is_active(self) -> None:
        mesh = read_bed_mesh(
            _with_mesh(
                {
                    "profile_name": "",
                    "mesh_min": [0, 0],
                    "mesh_max": [100, 100],
                    "probed_matrix": [[0, 0], [0, 0]],
                }
            )
        )

        assert isinstance(mesh, ActiveBedMesh)
        assert mesh.variation_mm == 0
        assert mesh.profile_name is None

    @pytest.mark.parametrize("matrix", [[], [[]], [[0]]])
    def test_unprobed_sentinel_is_inactive(self, matrix: list[list[int]]) -> None:
        mesh = read_bed_mesh(
            _with_mesh(
                {"probed_matrix": matrix, "mesh_min": [0, 0], "mesh_max": [0, 0]}
            )
        )

        assert mesh.state == "inactive"

    @pytest.mark.parametrize("status", [{}, {"bed_mesh": {}}])
    def test_missing_bed_mesh_is_unconfigured(self, status: dict) -> None:
        mesh = read_bed_mesh({"result": {"status": status}})

        assert mesh.state == "unconfigured"

    @pytest.mark.parametrize(
        "invalid_fields",
        [
            {"probed_matrix": [[0, 0], [0, 0, 0]]},
            {"probed_matrix": [[0, 0], [0]]},
            {"probed_matrix": [[0, 0], ["0", 0]]},
            {"probed_matrix": [[0, 0], [float("nan"), 0]]},
            {"probed_matrix": [[0, 0], [10**1000, 0]]},
            {"probed_matrix": [[-1e308, 1e308], [0, 0]]},
            {"probed_matrix": [[1]]},
            {"mesh_min": [0]},
            {"mesh_max": [0, 100]},
            {"mesh_max": [100, 0]},
            {"profile_name": 3},
        ],
    )
    def test_malformed_active_mesh_is_rejected(self, invalid_fields: dict) -> None:
        data = {
            "mesh_min": [0, 0],
            "mesh_max": [100, 100],
            "probed_matrix": [[0, 0], [0, 0]],
            **invalid_fields,
        }
        with pytest.raises(InvalidInspectionResponse):
            read_bed_mesh(_with_mesh(data))
