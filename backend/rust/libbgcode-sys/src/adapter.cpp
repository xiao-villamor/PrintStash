#include "adapter.hpp"
#include "printstash-libbgcode/src/lib.rs.h"

#include "libbgcode/core/core.hpp"

extern "C" {
#include "heatshrink/heatshrink_decoder.h"
}
#include <zlib.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace printstash::bgcode {
namespace {
using namespace ::bgcode::core;

constexpr std::size_t kMaxBlocks = 4096;
constexpr std::uint32_t kMaxBlockData = 64 * 1024 * 1024;
constexpr std::uint32_t kMaxMetadataBytes = 8 * 1024 * 1024;
constexpr std::uint32_t kMaxThumbnailBytes = 32 * 1024 * 1024;
constexpr std::size_t kMaxThumbnailTotalBytes = 64 * 1024 * 1024;

struct FileCloser {
    void operator()(FILE* file) const { std::fclose(file); }
};

struct DecoderCloser {
    void operator()(heatshrink_decoder* decoder) const {
        heatshrink_decoder_free(decoder);
    }
};

bool decode_deflate(
    const std::vector<std::uint8_t>& input,
    std::size_t expected,
    std::vector<std::uint8_t>& output) {
    z_stream stream{};
    stream.next_in = const_cast<Bytef*>(input.data());
    stream.avail_in = static_cast<uInt>(input.size());
    if (inflateInit(&stream) != Z_OK) return false;
    std::array<std::uint8_t, 4096> buffer{};
    output.clear();
    output.reserve(expected);
    bool valid = false;
    while (true) {
        const auto before_input = stream.total_in;
        const auto before_output = stream.total_out;
        stream.next_out = buffer.data();
        stream.avail_out = buffer.size();
        const int status = inflate(&stream, Z_NO_FLUSH);
        const std::size_t produced = buffer.size() - stream.avail_out;
        if (produced > expected - std::min(output.size(), expected)) break;
        output.insert(output.end(), buffer.begin(), buffer.begin() + produced);
        if (status == Z_STREAM_END) {
            valid = stream.avail_in == 0 && output.size() == expected;
            break;
        }
        if (status != Z_OK ||
            (stream.total_in == before_input && stream.total_out == before_output)) {
            break;
        }
    }
    inflateEnd(&stream);
    if (!valid) output.clear();
    return valid;
}

bool decode_heatshrink(
    const std::vector<std::uint8_t>& input,
    std::size_t expected,
    std::uint8_t window,
    std::vector<std::uint8_t>& output) {
    std::unique_ptr<heatshrink_decoder, DecoderCloser> decoder(
        heatshrink_decoder_alloc(2048, window, 4));
    if (!decoder) return false;
    std::array<std::uint8_t, 4096> buffer{};
    output.clear();
    output.reserve(expected);
    std::size_t offset = 0;
    while (offset < input.size()) {
        const auto before_offset = offset;
        const auto before_output = output.size();
        std::size_t sunk = 0;
        const auto sink = heatshrink_decoder_sink(
            decoder.get(),
            const_cast<std::uint8_t*>(input.data() + offset),
            input.size() - offset,
            &sunk);
        if (sink < 0) return false;
        offset += sunk;
        HSD_poll_res poll;
        do {
            std::size_t produced = 0;
            poll = heatshrink_decoder_poll(
                decoder.get(), buffer.data(), buffer.size(), &produced);
            if (poll < 0 ||
                produced > expected - std::min(output.size(), expected)) {
                return false;
            }
            output.insert(output.end(), buffer.begin(), buffer.begin() + produced);
        } while (poll == HSDR_POLL_MORE);
        if (offset == before_offset && output.size() == before_output) return false;
    }
    while (true) {
        const auto finish = heatshrink_decoder_finish(decoder.get());
        if (finish < 0) return false;
        if (finish == HSDR_FINISH_DONE) break;
        std::size_t produced = 0;
        const auto poll = heatshrink_decoder_poll(
            decoder.get(), buffer.data(), buffer.size(), &produced);
        if (poll < 0 || produced == 0 ||
            produced > expected - std::min(output.size(), expected)) {
            return false;
        }
        output.insert(output.end(), buffer.begin(), buffer.begin() + produced);
    }
    if (output.size() != expected) {
        output.clear();
        return false;
    }
    return true;
}

bool decode_exact(
    const std::vector<std::uint8_t>& input,
    ECompressionType compression,
    std::size_t expected,
    std::vector<std::uint8_t>& output) {
    if (compression == ECompressionType::None) {
        if (input.size() != expected) return false;
        output = input;
        return true;
    }
    if (compression == ECompressionType::Deflate) {
        return decode_deflate(input, expected, output);
    }
    if (compression == ECompressionType::Heatshrink_11_4) {
        return decode_heatshrink(input, expected, 11, output);
    }
    if (compression == ECompressionType::Heatshrink_12_4) {
        return decode_heatshrink(input, expected, 12, output);
    }
    return false;
}

bool read_block_bytes(
    FILE& file,
    long payload_start,
    EBlockType type,
    const BlockHeader& header,
    std::vector<std::uint8_t>& data) {
    const auto parameter_size = block_parameters_size(type);
    if (std::fseek(&file, payload_start + static_cast<long>(parameter_size), SEEK_SET) != 0) {
        return false;
    }
    const std::size_t data_size =
        header.compression == static_cast<std::uint16_t>(ECompressionType::None)
            ? header.uncompressed_size
            : header.compressed_size;
    if (data_size > kMaxBlockData) return false;
    data.resize(data_size);
    return data.empty() || std::fread(data.data(), 1, data.size(), &file) == data.size();
}

std::string trim(std::string value) {
    const auto first = std::find_if_not(
        value.begin(), value.end(), [](unsigned char character) {
            return std::isspace(character) != 0;
        });
    const auto last = std::find_if_not(
        value.rbegin(), value.rend(), [](unsigned char character) {
            return std::isspace(character) != 0;
        }).base();
    return first < last ? std::string(first, last) : std::string{};
}

void append_ini_metadata(
    std::string& result,
    const std::vector<std::uint8_t>& decoded,
    bool file_metadata) {
    const std::string text(decoded.begin(), decoded.end());
    std::size_t start = 0;
    while (start <= text.size()) {
        const auto end = text.find('\n', start);
        const auto line = trim(text.substr(start, end - start));
        if (!line.empty()) {
            result += "; " + line + "\n";
        }
        const auto separator = line.find('=');
        if (file_metadata && separator != std::string::npos) {
            std::string key = trim(line.substr(0, separator));
            const std::string value = trim(line.substr(separator + 1));
            std::transform(
                key.begin(),
                key.end(),
                key.begin(),
                [](unsigned char character) {
                    return static_cast<char>(std::tolower(character));
                });
            if (key == "producer") {
                result += "; generated by " + value + "\n";
            }
        }
        if (end == std::string::npos) break;
        start = end + 1;
    }
}

bool append_metadata(
    FILE& file,
    long payload_start,
    EBlockType type,
    const std::vector<std::uint8_t>& decoded,
    std::string& result) {
    // libbgcode's INI decoder does not advance past a line without '='. Parse
    // the already bounded bytes here so malformed uploads cannot hang.
    std::uint16_t encoding = 0;
    if (std::fseek(&file, payload_start, SEEK_SET) != 0 ||
        std::fread(&encoding, 1, sizeof(encoding), &file) != sizeof(encoding)) {
        return false;
    }
    if (encoding == static_cast<std::uint16_t>(EMetadataEncodingType::INI)) {
        append_ini_metadata(result, decoded, type == EBlockType::FileMetadata);
        return true;
    }
    if (encoding == static_cast<std::uint16_t>(EMetadataEncodingType::JSON) &&
        type == EBlockType::SlicerMetadata) {
        result += "; prusaslicer_json_config = begin\n; " +
            std::string(decoded.begin(), decoded.end()) +
            "\n; prusaslicer_json_config = end\n";
        return true;
    }
    return false;
}

}

Inspection inspect(
    const std::string& path,
    bool include_thumbnails,
    bool validate_all_blocks) {
    Inspection inspection{};
    inspection.valid = true;
    std::string metadata_text;
    const auto finish = [&]() {
        // Slicer profile names are external bytes. Match the text G-code path's
        // replacement behavior instead of turning one invalid byte into an FFI
        // exception that discards every usable metadata block.
        inspection.metadata_text = rust::String::lossy(metadata_text);
        return inspection;
    };
    std::unique_ptr<FILE, FileCloser> file(std::fopen(path.c_str(), "rb"));
    if (!file) throw std::runtime_error("cannot open BGCODE");
    if (std::fseek(file.get(), 0, SEEK_END) != 0) {
        throw std::runtime_error("cannot seek BGCODE");
    }
    const long file_size = std::ftell(file.get());
    if (file_size < 0) throw std::runtime_error("cannot measure BGCODE");
    std::rewind(file.get());

    FileHeader file_header;
    const std::uint32_t max_version = bgcode_version();
    if (read_header(*file, file_header, &max_version) != EResult::Success) {
        inspection.valid = false;
        return finish();
    }

    std::array<std::byte, 64 * 1024> checksum_buffer{};
    std::size_t metadata_bytes = 0;
    std::size_t thumbnail_bytes = 0;
    for (std::size_t index = 0; index < kMaxBlocks; ++index) {
        const long block_start = std::ftell(file.get());
        if (block_start == file_size) return finish();
        if (block_start < 0 || block_start > file_size) {
            inspection.valid = false;
            return finish();
        }

        BlockHeader header;
        if (read_next_block_header(
                *file,
                file_header,
                header,
                nullptr,
                0) != EResult::Success) {
            inspection.valid = false;
            return finish();
        }
        const long payload_start = std::ftell(file.get());
        const auto type = static_cast<EBlockType>(header.type);
        const auto compression = static_cast<ECompressionType>(header.compression);
        if (header.type > static_cast<std::uint16_t>(EBlockType::Thumbnail) ||
            header.compression >
                static_cast<std::uint16_t>(ECompressionType::Heatshrink_12_4)) {
            // A new block kind may carry an unknown parameter prefix. Guessing
            // its length would desynchronise every following header. Likewise,
            // an unknown codec cannot be validated, even when the caller does
            // not need to decode the printable body.
            inspection.valid = false;
            return finish();
        }
        const auto content_size = block_content_size(file_header, header);
        if (payload_start < 0 ||
            content_size > static_cast<std::size_t>(file_size - payload_start)) {
            inspection.valid = false;
            return finish();
        }
        const long block_end = payload_start + static_cast<long>(content_size);
        inspection.saw_block = true;
        inspection.saw_gcode = inspection.saw_gcode || type == EBlockType::GCode;

        const bool metadata =
            type == EBlockType::FileMetadata || type == EBlockType::SlicerMetadata ||
            type == EBlockType::PrinterMetadata || type == EBlockType::PrintMetadata;
        const bool thumbnail = type == EBlockType::Thumbnail && include_thumbnails;
        const std::size_t data_size =
            compression == ECompressionType::None
                ? header.uncompressed_size
                : header.compressed_size;
        if (metadata &&
            (header.uncompressed_size > kMaxMetadataBytes ||
             data_size > kMaxBlockData ||
             metadata_bytes > kMaxMetadataBytes - header.uncompressed_size)) {
            inspection.valid = false;
            std::fseek(file.get(), block_end, SEEK_SET);
            continue;
        }
        if (thumbnail &&
            (header.uncompressed_size > kMaxThumbnailBytes ||
             data_size > kMaxBlockData ||
             thumbnail_bytes > kMaxThumbnailTotalBytes ||
             header.uncompressed_size > kMaxThumbnailTotalBytes - thumbnail_bytes)) {
            inspection.valid = false;
            std::fseek(file.get(), block_end, SEEK_SET);
            continue;
        }
        // Charge declared output before checksum or decoding. Repeated corrupt
        // blocks must not reset the process-wide work budget.
        if (metadata) metadata_bytes += header.uncompressed_size;
        if (thumbnail) thumbnail_bytes += header.uncompressed_size;

        const bool selected = metadata || thumbnail;
        if ((selected || validate_all_blocks) && verify_block_checksum(
                *file,
                file_header,
                header,
                checksum_buffer.data(),
                checksum_buffer.size()) != EResult::Success) {
            inspection.valid = false;
            std::fseek(file.get(), block_end, SEEK_SET);
            continue;
        }

        if (metadata) {
            std::vector<std::uint8_t> input;
            std::vector<std::uint8_t> decoded;
            if (!read_block_bytes(*file, payload_start, type, header, input) ||
                !decode_exact(input, compression, header.uncompressed_size, decoded)) {
                inspection.valid = false;
                std::fseek(file.get(), block_end, SEEK_SET);
                continue;
            }
            if (!append_metadata(
                    *file,
                    payload_start,
                    type,
                    decoded,
                    metadata_text)) {
                inspection.valid = false;
            }
        } else if (thumbnail) {
            std::fseek(file.get(), payload_start, SEEK_SET);
            ThumbnailParams params{};
            std::vector<std::uint8_t> input;
            std::vector<std::uint8_t> decoded;
            if (params.read(*file) != EResult::Success || params.format > 2 ||
                params.width == 0 || params.height == 0 ||
                !read_block_bytes(*file, payload_start, type, header, input) ||
                !decode_exact(input, compression, header.uncompressed_size, decoded)) {
                inspection.valid = false;
                std::fseek(file.get(), block_end, SEEK_SET);
                continue;
            }
            rust::Vec<std::uint8_t> data;
            data.reserve(decoded.size());
            for (const auto byte : decoded) data.push_back(byte);
            inspection.thumbnails.push_back(
                Thumbnail{params.format, params.width, params.height, std::move(data)});
        }
        std::fseek(file.get(), block_end, SEEK_SET);
    }
    inspection.valid = false;
    return finish();
}
}
