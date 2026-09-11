"""Human-assigned variation roles, independent from machine evidence classes."""

from typing import Literal

MemberRole = Literal["identical", "rescaled", "mirrored", "repaired", "print_variant"]
VariantRole = Literal[
    "canonical", "identical", "rescaled", "mirrored", "repaired", "print_variant"
]
