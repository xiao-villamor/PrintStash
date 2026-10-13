"""GPU adapter contracts through a native API fake; no hardware quality claims."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pytest

from scripts.gpu_render_backend import GpuContext, GpuError, GpuFailure, GpuFrame


class Stage(StrEnum):
    DEPTH = "depth"
    BUFFER = "buffer"
    PROGRAM = "program"
    WRITE = "write"
    DRAW = "draw"
    READ = "read"


class Resource:
    def __init__(self, native: NativeContext, kind: str, size: int = 0):
        self.kind, self.size = kind, size
        self.release_count = 0
        native.resources.append(self)

    def release(self) -> None:
        self.release_count += 1


@dataclass
class Uniform:
    value: object = 0.0


class Program(Resource):
    def __init__(self, native: NativeContext):
        super().__init__(native, "program")
        self.uniforms: dict[str, Uniform] = {}

    def __getitem__(self, name: str) -> Uniform:
        return self.uniforms.setdefault(name, Uniform())


class Buffer(Resource):
    def __init__(self, native: NativeContext, reserve: int):
        super().__init__(native, "buffer", reserve)
        self.native = native
        self.payloads: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.native.check_failure(Stage.WRITE)
        if len(data) > self.size:
            raise RuntimeError("native buffer overflow")
        self.payloads.append(data)


class VertexArray(Resource):
    def __init__(self, native: NativeContext, buffer: Buffer):
        super().__init__(native, "vertex_array")
        self.native, self.buffer = native, buffer
        self.drawn_vertices: list[int] = []

    def render(self, *, vertices: int) -> None:
        self.native.check_failure(Stage.DRAW)
        if vertices * 24 != len(self.buffer.payloads[-1]):
            raise RuntimeError("incomplete native vertex payload")
        self.drawn_vertices.append(vertices)


class Framebuffer(Resource):
    def __init__(self, native: NativeContext):
        super().__init__(native, "framebuffer")
        self.native = native
        self.viewport = (0, 0, 0, 0)

    def use(self) -> None:
        pass

    def clear(self, red, green, blue, alpha, *, depth):
        assert (red, green, blue, alpha, depth) == (0, 0, 0, 0, 1)

    def read(self, *, components: int, alignment: int) -> bytes:
        self.native.check_failure(Stage.READ)
        assert (components, alignment) == (4, 1)
        self.native.read_count += 1
        return self.native.payload


class NativeContext:
    """Finite recorder of owned resources, uploaded bytes and readback pixels."""

    def __init__(self, *, failure: Stage | None = None, payload: bytes | None = None):
        self.info = {
            "GL_VENDOR": "test-native-api",
            "GL_RENDERER": "fake-not-hardware",
            "GL_VERSION": "3.3 contract",
            "GL_SHADING_LANGUAGE_VERSION": "330 contract",
        }
        self.version_code = 330
        self.depth_func, self.depth_mask = "<", False
        self.resources: list[Resource] = []
        self.failure = failure
        self.error = RuntimeError("native test boundary failure")
        self.release_count = self.read_count = 0
        # Native rows start at the bottom; transparent pixel has zero RGB.
        self.payload = (
            payload
            if payload is not None
            else bytes(
                [
                    255,
                    0,
                    0,
                    255,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    255,
                    255,
                    255,
                    255,
                    255,
                    255,
                ]
            )
        )

    def check_failure(self, stage: Stage) -> None:
        if self.failure is stage:
            raise self.error

    def texture(self, size, components, *, dtype):
        assert components == 4
        assert dtype == "f1"
        return Resource(self, "color", size[0] * size[1] * 4)

    def depth_texture(self, size):
        self.check_failure(Stage.DEPTH)
        return Resource(self, "depth", size[0] * size[1] * 4)

    def framebuffer(self, *, color_attachments, depth_attachment):
        assert len(color_attachments) == 1
        assert depth_attachment.kind == "depth"
        return Framebuffer(self)

    def program(self, *, vertex_shader, fragment_shader):
        self.check_failure(Stage.PROGRAM)
        assert "#version 330" in vertex_shader
        assert "#version 330" in fragment_shader
        return Program(self)

    def buffer(self, *, reserve):
        self.check_failure(Stage.BUFFER)
        return Buffer(self, reserve)

    def vertex_array(self, program, content):
        assert content[0][1:] == ("3f 3f", "position", "normal")
        return VertexArray(self, content[0][0])

    def enable_only(self, flags):
        assert flags == 0x02

    def finish(self):
        pass

    def release(self):
        self.release_count += 1


@pytest.fixture
def native_context():
    native = NativeContext()
    context = GpuContext.create(context_factory=lambda backend: native)
    yield native, context
    context.close()


@pytest.fixture
def draw_inputs():
    return (
        np.zeros((2, 2, 3), dtype=np.uint8),
        np.full((2, 2), np.inf, dtype=np.float64),
        np.array([[[0.1, 0.1, 0], [1.5, 0.1, 0], [0.1, 1.5, 0]]], dtype=np.float64),
        np.broadcast_to(np.array([0, 0, 1], dtype=np.float64), (1, 3, 3)).copy(),
        lambda normals: normals,
        np.full(3, 255, dtype=np.float64),
        2,
        2,
    )


class TestGpuContext:
    def test_reports_native_context_identity(self, native_context):
        native, context = native_context

        assert context.info == {
            "GL_VENDOR": "test-native-api",
            "GL_RENDERER": "fake-not-hardware",
            "GL_VERSION": "3.3 contract",
            "version_code": 330,
        }
        assert context.info["GL_RENDERER"] == "fake-not-hardware"

    def test_closes_context_once(self, native_context, draw_inputs):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)

        frame(*draw_inputs)
        program = next(
            resource for resource in native.resources if resource.kind == "program"
        )

        context.close()
        frame.close()
        context.close()

        assert native.release_count == 1
        assert program.release_count == 1
        assert all(resource.release_count == 1 for resource in native.resources)
        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2)
        assert caught.value.reason is GpuFailure.CLOSED
        assert frame.stats.readback_count == 0
        with pytest.raises(GpuError) as retired:
            frame(*draw_inputs)
        assert retired.value.reason is GpuFailure.CLOSED
        assert native.read_count == 0
        assert all(resource.release_count == 1 for resource in native.resources)

    @pytest.mark.parametrize("key", ["GL_VENDOR", "GL_RENDERER", "GL_VERSION"], ids=str)
    def test_refuses_missing_required_identity(self, key):
        native = NativeContext()
        del native.info[key]

        with pytest.raises(GpuError) as caught:
            GpuContext.create(context_factory=lambda backend: native)

        assert caught.value.reason is GpuFailure.CONTEXT_FAILED
        assert isinstance(caught.value.__cause__, KeyError)
        assert caught.value.__cause__.args == (key,)
        assert native.release_count == 1
        assert native.resources == []

    @pytest.mark.parametrize("key", ["GL_VENDOR", "GL_RENDERER", "GL_VERSION"], ids=str)
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("", id="empty"),
            pytest.param(None, id="null"),
            pytest.param(42, id="integer"),
        ],
    )
    def test_refuses_invalid_required_identity(self, key, value):
        native = NativeContext()
        native.info[key] = value

        with pytest.raises(GpuError) as caught:
            GpuContext.create(context_factory=lambda backend: native)

        assert caught.value.reason is GpuFailure.CONTEXT_FAILED
        assert isinstance(caught.value.__cause__, ValueError)
        assert str(caught.value.__cause__) == "invalid_required_gpu_identity"
        assert native.release_count == 1
        assert native.resources == []

    @pytest.mark.parametrize(
        "operation", ["allocation", "context-close", "draw", "finish"], ids=str
    )
    def test_refuses_foreign_thread_before_native_access(
        self, native_context, draw_inputs, operation
    ):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)
        resources = tuple(native.resources)
        operations = {
            "allocation": lambda: GpuFrame(context, 2, 2, 1, 2),
            "context-close": context.close,
            "draw": lambda: frame(*draw_inputs),
            "finish": lambda: frame.finish(*draw_inputs[:2]),
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(operations[operation])
            with pytest.raises(GpuError) as caught:
                future.result(timeout=3)

        assert caught.value.reason is GpuFailure.CLOSED
        assert tuple(native.resources) == resources
        assert native.release_count == native.read_count == 0
        assert all(resource.release_count == 0 for resource in resources)
        buffer = next(
            resource for resource in resources if isinstance(resource, Buffer)
        )
        vao = next(
            resource for resource in resources if isinstance(resource, VertexArray)
        )
        assert buffer.payloads == []
        assert vao.drawn_vertices == []
        frame(*draw_inputs)
        frame.finish(*draw_inputs[:2])
        assert native.read_count == 1
        np.testing.assert_array_equal(draw_inputs[1], np.array([[0, 0], [0, np.inf]]))

    def test_preserves_context_creation_failure(self):
        error = OSError("driver unavailable")

        def unavailable(backend):
            raise error

        with pytest.raises(GpuError) as caught:
            GpuContext.create(context_factory=unavailable)

        assert caught.value.reason is GpuFailure.CONTEXT_FAILED
        assert caught.value.__cause__ is error

    def test_preserves_missing_dependency_failure(self):
        error = ModuleNotFoundError("optional library absent")

        def unavailable(backend):
            raise error

        with pytest.raises(GpuError) as caught:
            GpuContext.create(context_factory=unavailable)

        assert caught.value.reason is GpuFailure.DEPENDENCY_UNAVAILABLE
        assert caught.value.__cause__ is error


class TestGpuFrame:
    @pytest.mark.parametrize(
        ("width", "height", "chunk", "radius", "allowance"),
        [
            (0, 2, 1, 2, 1000),
            (2, -1, 1, 2, 1000),
            (2, 2, 0, 2, 1000),
            (2, 2, 1, 0, 1000),
            (2, 2, 1, float("nan"), 1000),
            (2, 2, 1, float("inf"), 1000),
            (True, 2, 1, 2, 1000),
            (2, 2, 1, 2, 0),
        ],
        ids=[
            "width",
            "height",
            "chunk",
            "radius",
            "nan",
            "infinity",
            "bool",
            "allowance",
        ],
    )
    def test_rejects_invalid_frame_contract(
        self, native_context, width, height, chunk, radius, allowance
    ):
        native, context = native_context

        with pytest.raises(GpuError) as caught:
            GpuFrame(
                context, width, height, chunk, radius, allocation_limit_bytes=allowance
            )

        assert caught.value.reason is GpuFailure.INVALID_REQUEST
        assert native.resources == []

    def test_refuses_an_over_budget_frame(self, native_context):
        native, context = native_context

        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2, allocation_limit_bytes=1)

        assert caught.value.reason is GpuFailure.ALLOCATION_FAILED
        assert native.resources == []

    def test_defers_readback_until_finalization(self, native_context, draw_inputs):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)
        img, zbuf = draw_inputs[:2]
        original_triangles = draw_inputs[2].copy()

        frame(*draw_inputs)
        frame(*draw_inputs)

        np.testing.assert_array_equal(img, np.zeros((2, 2, 3), dtype=np.uint8))
        assert np.isinf(zbuf).all()
        assert native.read_count == 0
        frame.finish(img, zbuf)
        np.testing.assert_array_equal(
            img,
            np.array(
                [[[0, 0, 255], [255, 255, 255]], [[255, 0, 0], [0, 0, 0]]],
                dtype=np.uint8,
            ),
        )
        np.testing.assert_array_equal(zbuf, np.array([[0, 0], [0, np.inf]]))
        np.testing.assert_array_equal(draw_inputs[2], original_triangles)
        assert frame.stats.upload_bytes == 2 * 3 * 6 * 4
        assert frame.stats.draw_calls == 2
        assert frame.stats.readback_count == native.read_count == 1
        assert frame.stats.requested_allocation_bytes == sum(
            resource.size for resource in native.resources
        )

    def test_releases_frame_resources(self, native_context):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)
        owned = tuple(native.resources)

        frame.close()
        frame.close()
        another = GpuFrame(context, 2, 2, 1, 2)

        assert all(
            resource.release_count == 1
            for resource in owned
            if resource.kind != "program"
        )
        assert native.release_count == 0
        assert another.stats.requested_allocation_bytes > 0

    def test_reuses_compiled_program_across_frames(self, native_context, draw_inputs):
        native, context = native_context
        first = GpuFrame(context, 2, 2, 1, 2)
        first(*draw_inputs)
        first.finish(*draw_inputs[:2])
        expected = draw_inputs[0].copy()
        first.close()

        second = GpuFrame(context, 2, 2, 1, 2)
        second(*draw_inputs)
        second.finish(*draw_inputs[:2])
        second.close()

        np.testing.assert_array_equal(draw_inputs[0], expected)
        programs = [
            resource for resource in native.resources if resource.kind == "program"
        ]
        assert len(programs) == 1
        assert programs[0].release_count == 0
        context.close()
        assert programs[0].release_count == 1
        assert all(resource.release_count == 1 for resource in native.resources)

    def test_refuses_overlapping_frame_before_allocation(
        self, native_context, draw_inputs
    ):
        native, context = native_context
        first = GpuFrame(context, 2, 2, 1, 2)
        existing_resources = tuple(native.resources)

        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2)
        first(*draw_inputs)
        first.finish(*draw_inputs[:2])

        assert caught.value.reason is GpuFailure.INVALID_REQUEST
        assert tuple(native.resources) == existing_resources
        np.testing.assert_array_equal(draw_inputs[1], np.array([[0, 0], [0, np.inf]]))

    def test_recovers_allocation_failure_after_program_cache(
        self, native_context, draw_inputs
    ):
        native, context = native_context
        native.failure = Stage.BUFFER

        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2)
        native.failure = None
        recovered = GpuFrame(context, 2, 2, 1, 2)
        recovered(*draw_inputs)
        recovered.finish(*draw_inputs[:2])
        context.close()

        assert caught.value.reason is GpuFailure.ALLOCATION_FAILED
        assert caught.value.__cause__ is native.error
        assert (
            len(
                [
                    resource
                    for resource in native.resources
                    if resource.kind == "program"
                ]
            )
            == 1
        )
        assert all(resource.release_count == 1 for resource in native.resources)
        np.testing.assert_array_equal(draw_inputs[1], np.array([[0, 0], [0, np.inf]]))

    def test_cleans_partial_allocation_failure(self):
        native = NativeContext(failure=Stage.DEPTH)
        context = GpuContext.create(context_factory=lambda backend: native)

        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2)

        assert caught.value.reason is GpuFailure.ALLOCATION_FAILED
        assert caught.value.__cause__ is native.error
        assert len(native.resources) == 1
        assert native.resources[0].release_count == 1
        context.close()
        assert native.release_count == 1

    def test_preserves_shader_compilation_failure(self, native_context):
        native, context = native_context
        native.failure = Stage.PROGRAM

        with pytest.raises(GpuError) as caught:
            GpuFrame(context, 2, 2, 1, 2)

        assert caught.value.reason is GpuFailure.SHADER_FAILED
        assert caught.value.__cause__ is native.error
        assert [resource.kind for resource in native.resources] == [
            "color",
            "depth",
            "framebuffer",
        ]
        assert all(
            resource.release_count == 1
            for resource in native.resources
            if resource.kind != "program"
        )
        assert native.release_count == 0

    @pytest.mark.parametrize("stage", [Stage.WRITE, Stage.DRAW], ids=["upload", "draw"])
    def test_preserves_draw_failure(self, native_context, draw_inputs, stage):
        native, context = native_context
        native.failure = stage
        frame = GpuFrame(context, 2, 2, 1, 2)

        with pytest.raises(GpuError) as caught:
            frame(*draw_inputs)
        with pytest.raises(GpuError) as repeated:
            frame.finish(*draw_inputs[:2])
        frame.close()

        assert repeated.value is caught.value
        assert caught.value.reason is GpuFailure.DRAW_FAILED
        assert caught.value.__cause__ is native.error
        assert all(
            resource.release_count == 1
            for resource in native.resources
            if resource.kind != "program"
        )
        assert native.read_count == 0
        np.testing.assert_array_equal(
            draw_inputs[0], np.zeros((2, 2, 3), dtype=np.uint8)
        )
        assert np.isinf(draw_inputs[1]).all()

    def test_preserves_readback_failure(self, native_context, draw_inputs):
        native, context = native_context
        native.failure = Stage.READ
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*draw_inputs)

        with pytest.raises(GpuError) as caught:
            frame.finish(*draw_inputs[:2])
        with pytest.raises(GpuError) as repeated:
            frame.finish(*draw_inputs[:2])
        frame.close()

        assert repeated.value is caught.value
        assert caught.value.reason is GpuFailure.READBACK_FAILED
        assert caught.value.__cause__ is native.error
        assert all(
            resource.release_count == 1
            for resource in native.resources
            if resource.kind != "program"
        )
        np.testing.assert_array_equal(
            draw_inputs[0], np.zeros((2, 2, 3), dtype=np.uint8)
        )
        assert np.isinf(draw_inputs[1]).all()

    def test_refuses_truncated_readback(self, native_context, draw_inputs):
        native, context = native_context
        native.payload = native.payload[:-1]
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*draw_inputs)

        with pytest.raises(GpuError) as caught:
            frame.finish(*draw_inputs[:2])
        frame.close()

        assert caught.value.reason is GpuFailure.READBACK_FAILED
        assert isinstance(caught.value.__cause__, ValueError)
        assert native.read_count == 1
        assert all(
            resource.release_count == 1
            for resource in native.resources
            if resource.kind != "program"
        )
        np.testing.assert_array_equal(
            draw_inputs[0], np.zeros((2, 2, 3), dtype=np.uint8)
        )
        assert np.isinf(draw_inputs[1]).all()

    def test_rejects_repeated_finalization(self, native_context, draw_inputs):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*draw_inputs)
        frame.finish(*draw_inputs[:2])
        completed = draw_inputs[0].copy()

        with pytest.raises(GpuError) as caught:
            frame.finish(*draw_inputs[:2])

        assert caught.value.reason is GpuFailure.CLOSED
        assert native.read_count == 1
        np.testing.assert_array_equal(draw_inputs[0], completed)

    @pytest.mark.parametrize("operation", ["draw", "finish"], ids=["draw", "finish"])
    def test_rejects_closed_frame_operations(
        self, native_context, draw_inputs, operation
    ):
        native, context = native_context
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame.close()
        operations = {
            "draw": lambda: frame(*draw_inputs),
            "finish": lambda: frame.finish(*draw_inputs[:2]),
        }

        with pytest.raises(GpuError) as caught:
            operations[operation]()

        assert caught.value.reason is GpuFailure.CLOSED
        assert native.read_count == 0
        np.testing.assert_array_equal(
            draw_inputs[0], np.zeros((2, 2, 3), dtype=np.uint8)
        )
