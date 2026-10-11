"""GPU API-boundary fake: commands/lifetimes only, never shader math."""

from types import SimpleNamespace

API = SimpleNamespace(
    BufferUsage=SimpleNamespace(
        STORAGE=128, COPY_DST=8, COPY_SRC=4, MAP_READ=1, MAP_WRITE=2, UNIFORM=64
    ),
    TextureUsage=SimpleNamespace(RENDER_ATTACHMENT=16, COPY_SRC=1, TEXTURE_BINDING=4),
    MapMode=SimpleNamespace(READ=1),
)


class Buffer:
    def __init__(self, size, device, mapped=False):
        self.data = bytearray(size)
        self.device = device
        self.destroyed = False
        self.map_state = "mapped" if mapped else "unmapped"

    def destroy(self):
        assert self.map_state == "unmapped"
        self.destroyed = True

    def write_mapped(self, data):
        assert not self.destroyed and self.map_state == "mapped"
        self.device.mapped_writes += 1
        if self.device.fail_write_mapped:
            self.device.fail_write_mapped = False
            raise RuntimeError("injected mapped upload failure")
        incoming = memoryview(data).cast("B")
        assert incoming.nbytes == len(self.data)
        self.data[:] = incoming

    def map_sync(self, mode):
        assert not self.destroyed
        self.device.queue.maps += 1
        if self.device.fail_map:
            self.device.fail_map = False
            raise RuntimeError("injected map failure")
        self.map_state = "mapped"

    def read_mapped(self, *, size=None, copy=True):
        return memoryview(self.data)[:size]

    def unmap(self):
        assert not self.destroyed
        self.map_state = "unmapped"


class Texture:
    def __init__(self):
        self.destroyed = False

    def create_view(self):
        assert not self.destroyed
        return self

    def destroy(self):
        self.destroyed = True


class Queue:
    def __init__(self):
        self.submissions = self.maps = self.writes = 0
        self.fail_submit = False

    def write_buffer(self, buffer, offset, data):
        assert not buffer.destroyed
        value = memoryview(data).cast("B")
        buffer.data[offset : offset + value.nbytes] = value
        self.writes += 1

    def submit(self, commands):
        self.submissions += 1
        if self.fail_submit:
            self.fail_submit = False
            raise RuntimeError("injected submit failure")
        for encoder in commands:
            for command in encoder.commands:
                command()


class Pipeline:
    def get_bind_group_layout(self, index):
        return index


class Pass:
    def __init__(self, device):
        self.device = device

    def set_pipeline(self, pipeline):
        self.pipeline = pipeline

    def set_bind_group(self, index, group):
        self.group = group

    def dispatch_workgroups(self, *counts):
        self.device.dispatches.append(counts)

    def draw(self, vertices, instances):
        self.device.draws.append((vertices, instances))

    def end(self):
        pass


class Encoder:
    def __init__(self, device):
        self.device = device
        self.commands = []

    def copy_buffer_to_buffer(self, source, source_offset, target, target_offset, size):
        assert source.map_state == target.map_state == "unmapped"

        def copy():
            assert not source.destroyed and not target.destroyed
            target.data[target_offset : target_offset + size] = source.data[
                source_offset : source_offset + size
            ]

        self.commands.append(copy)

    def clear_buffer(self, buffer):
        def clear():
            assert not buffer.destroyed
            buffer.data[:] = bytes(len(buffer.data))

        self.commands.append(clear)

    def begin_compute_pass(self):
        return Pass(self.device)

    def begin_render_pass(self, **kwargs):
        return Pass(self.device)

    def copy_texture_to_buffer(self, source, destination, size):
        assert not source["texture"].destroyed
        self.device.readbacks.append(destination)

    def finish(self):
        return self


class Device:
    def __init__(self):
        self.limits = {
            "max-storage-buffer-binding-size": 128 * 1024**2,
            "max-buffer-size": 256 * 1024**2,
            "max-compute-workgroups-per-dimension": 65535,
        }
        self.queue = Queue()
        self.buffers = []
        self.groups = []
        self.draws = []
        self.dispatches = []
        self.readbacks = []
        self.fail_allocation = None
        self.fail_map = False
        self.fail_write_mapped = False
        self.mapped_writes = 0
        self.destroyed = False

    def destroy(self):
        self.destroyed = True

    def create_shader_module(self, *, code):
        return code

    def create_compute_pipeline(self, **kwargs):
        return Pipeline()

    def create_render_pipeline(self, **kwargs):
        return Pipeline()

    def create_buffer(self, *, size, usage, mapped_at_creation=False):
        if len(self.buffers) == self.fail_allocation:
            raise MemoryError("injected allocation failure")
        buffer = Buffer(size, self, mapped=mapped_at_creation)
        self.buffers.append(buffer)
        return buffer

    def create_buffer_with_data(self, *, data, usage):
        incoming = memoryview(data).cast("B")
        buffer = self.create_buffer(size=incoming.nbytes, usage=usage)
        buffer.data[:] = incoming
        return buffer

    def create_texture(self, **kwargs):
        return Texture()

    def create_bind_group(self, **kwargs):
        self.groups.append(kwargs)
        return kwargs

    def create_command_encoder(self):
        return Encoder(self)
