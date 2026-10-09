"""Read-only hardware evidence for Mesa D3D12 on WSL, independent of GPU vendor.

The GL backend cannot report an adapter type. Match its renderer to DXCore's
IsHardware property instead of accepting arbitrary Unknown/software adapters.
ABI: microsoft/DirectX-Headers include/directx/dxcore_interface.h.
Only the compute broker loads this optional host library.
"""

import ctypes as c
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Hardware:
    name: str
    vendor_id: int
    device_id: int
    driver_version: int
    integrated: bool


def _guid(value):
    return (c.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)


def _method(pointer, slot, result, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.CFUNCTYPE(result, c.c_void_p, *arguments)(table[slot])


def _check(result):
    if result < 0:
        raise OSError(f"dxcore_hresult_{result & 0xFFFFFFFF:08x}")


@contextmanager
def _owned():
    pointer = c.c_void_p()
    try:
        yield pointer
    finally:
        if pointer.value is not None:
            _method(pointer, 2, c.c_uint32)(pointer)


def _property(adapter, key, value):
    _check(
        _method(adapter, 6, c.c_int32, c.c_uint32, c.c_size_t, c.c_void_p)(
            adapter, key, c.sizeof(value), c.byref(value)
        )
    )
    return value


def hardware() -> tuple[Hardware, ...]:
    """Unavailable/mismatched host libraries provide no physical evidence."""
    try:
        library = c.CDLL("libdxcore.so")
        create = library.DXCoreCreateAdapterFactory
        create.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
        create.restype = c.c_int32
        records = []
        with _owned() as factory, _owned() as adapters:
            _check(
                create(_guid("78ee5945-c36e-4b13-a669-005dd11c0f06"), c.byref(factory))
            )
            _check(
                _method(
                    factory,
                    3,
                    c.c_int32,
                    c.c_uint32,
                    c.c_void_p,
                    c.c_void_p,
                    c.c_void_p,
                )(
                    factory,
                    1,
                    _guid("0c9ece4d-2f6e-4f01-8c96-e89e331b47b1"),
                    _guid("526c7776-40e9-459b-b711-f32ad76dfc28"),
                    c.byref(adapters),
                )
            )
            count = _method(adapters, 4, c.c_uint32)(adapters)
            if count > 64:
                return ()
            for index in range(count):
                with _owned() as adapter:
                    _check(
                        _method(
                            adapters, 3, c.c_int32, c.c_uint32, c.c_void_p, c.c_void_p
                        )(
                            adapters,
                            index,
                            _guid("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e"),
                            c.byref(adapter),
                        )
                    )
                    if not _method(adapter, 3, c.c_bool)(adapter):
                        continue
                    if not _property(adapter, 11, c.c_bool()).value:
                        continue
                    length = c.c_size_t()
                    _check(
                        _method(adapter, 7, c.c_int32, c.c_uint32, c.c_void_p)(
                            adapter, 2, c.byref(length)
                        )
                    )
                    if not 1 <= length.value <= 4096:
                        continue
                    name = _property(
                        adapter, 2, c.create_string_buffer(length.value)
                    ).value.decode("utf-8")
                    ids = _property(adapter, 3, (c.c_uint32 * 4)())
                    records.append(
                        Hardware(
                            name,
                            ids[0],
                            ids[1],
                            _property(adapter, 1, c.c_uint64()).value,
                            _property(adapter, 12, c.c_bool()).value,
                        )
                    )
        return tuple(records)
    except OSError, AttributeError, UnicodeError:
        return ()


def identify(info: dict, records: tuple[Hardware, ...]) -> dict:
    """Return enriched evidence only for an exact, unambiguous hardware match."""
    if info.get("adapter_type") != "Unknown" or info.get("backend_type") != "OpenGL":
        return info
    matches = [
        record for record in records if info.get("device") == f"D3D12 ({record.name})"
    ]
    if len(matches) != 1:
        return info
    match = matches[0]
    return {
        **info,
        "adapter_type": "IntegratedGPU" if match.integrated else "DiscreteGPU",
        "vendor_id": match.vendor_id,
        "device_id": match.device_id,
        "description": f"{info['description']}; DXCore driver {match.driver_version}",
    }
