#pragma once

#include "rust/cxx.h"
#include <string>

namespace printstash::bgcode {
struct Inspection;

Inspection inspect(
    const std::string& path,
    bool include_thumbnails,
    bool validate_all_blocks);
}
