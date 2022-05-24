#!/usr/bin/env python3
#
# Copyright 2011-2022 The Rust Project Developers. See the COPYRIGHT
# file at the top-level directory of this distribution and at
# http://rust-lang.org/COPYRIGHT.
#
# Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
# http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
# <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
# option. This file may not be copied, modified, or distributed
# except according to those terms.

# This script uses the following Unicode tables:
# - EastAsianWidth.txt
# - ReadMe.txt
# - UnicodeData.txt
#
# Since this should not require frequent updates, we just store this
# out-of-line and check the generated.rs file into git.

import re, os, sys, enum, math

# Unicode codespace is 0..=0x10FFFF
NUM_CODEPOINTS = 0x110000

def fetch_open(f):
    if not os.path.exists(os.path.basename(f)):
        os.system("curl -O http://www.unicode.org/Public/UNIDATA/%s"
                  % f)
    try:
        return open(f)
    except OSError:
        sys.stderr.write("cannot load %s" % f)
        exit(1)
    

def load_unicode_version() -> "tuple[int, int, int]":
    with fetch_open("ReadMe.txt") as readme:
        pattern = "for Version (\d+)\.(\d+)\.(\d+) of the Unicode"
        return tuple(map(int, re.search(pattern, readme.read()).groups()))

class CondensedWidth(enum.IntEnum):
    ZERO = 0
    NARROW = 1
    WIDE = 2
    AMBIGUOUS = 3

def load_condensed_widths() -> "list[CondensedWidth]":
    """Return a list of condensed widths, indexed by codepoint. 
    
    `Neutral`, `Narrow`, and `Halfwidth` characters are assigned 
    `CondensedWidth.NARROW`.
    
    `Wide` and `Fullwidth` characters are assigned `CondensedWidth.WIDE`. 
    
    `Ambiguous` chracters are assigned `CondensedWidth.AMBIGUOUS`. 

    Note that this *EXCLUSIVELY* assigns widths according to `EastAsianWidth.txt`.
    """
    with fetch_open("EastAsianWidth.txt") as eaw:
        re1 = re.compile("^([0-9A-F]+);(\w+) +# (\w+)")
        re2 = re.compile("^([0-9A-F]+)\.\.([0-9A-F]+);(\w+) +# (\w+)")
        # map between width category code and condensed width
        width_codes = { **{c: CondensedWidth.NARROW for c in ["N", "Na", "H"]}, \
                        **{c: CondensedWidth.WIDE for c in ["W", "F"]}, \
                         "A": CondensedWidth.AMBIGUOUS }

        width_map = []
        current = 0
        for line in eaw.readlines():
            raw_data = () # (low, high, width)
            if m := re1.match(line):
                raw_data = (m.group(1), m.group(1), m.group(2))
            elif m := re2.match(line):
                raw_data = (m.group(1), m.group(2), m.group(3))
            else:
                continue
            low = int(raw_data[0], 16)
            high = int(raw_data[1], 16)
            width = width_codes[raw_data[2]]

            assert current <= high
            while current <= high:
                # Some codepoints don't fall into any of the ranges in EastAsianWidth.txt.
                # All such codepoints are implicitly given Neural width.
                width_map.append(CondensedWidth.NARROW if current < low else width)
                current += 1

        while len(width_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints and assign them implicit narrow width.
            width_map.append(CondensedWidth.NARROW)

        return width_map

class CondensedCategory(enum.IntEnum):
    ZERO_WIDTH = 0,
    OTHER = 1,

def load_condensed_categories() -> "list[CondensedCategory]":
    """Returns a list of condensed categories, indexed by codepoint."""
    with fetch_open("UnicodeData.txt") as categories:

        cat_map = []
        current = 0
        for line in categories.readlines():
            raw_data = line.split(';')
            if len(raw_data) != 15:
                continue
            [codepoint, name, cat_code] = [int(raw_data[0], 16), raw_data[1], raw_data[2]]
            cat = CondensedCategory.ZERO_WIDTH if cat_code in ["Cf", "Mn", "Me"] \
                else CondensedCategory.OTHER

            assert current <= codepoint
            while current <= codepoint:
                if name.endswith(", Last>") or current == codepoint:
                    cat_map.append(cat) # either the specified char, or we're filling in a range
                else:
                    cat_map.append(CondensedCategory.OTHER) # unassigned
                current += 1

        while len(cat_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints. They must be unassigned
            cat_map.append(CondensedCategory.OTHER)

        return cat_map

def merge_properties(props: "tuple[CondensedWidth, CondensedCategory]") -> "CondensedWidth":
    (eaw, cat) = props
    return CondensedWidth.ZERO if cat == CondensedCategory.ZERO_WIDTH else eaw

def make_key(codepoint: int, width: CondensedWidth) -> int:
    return (codepoint << 4) | int(width)

def compress_properties(props: "list[CondensedWidth]") -> "list[int]":
    compressed_list = []
    last_width = None

    for codepoint, width in enumerate(props):
        if width != last_width:
            last_width = width
            compressed_list.append(make_key(codepoint, width))
    
    return compressed_list

def emit_module(out_name: str, version: "tuple[int, int, int]", compressed_props: "list[int]"):
    if os.path.exists(out_name):
        os.remove(out_name)
    with open(out_name, "w") as of:
        # Write the file's preamble
        of.write("""// Copyright 2012-2022 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

// NOTE: The following code was generated by "scripts/unicode.py", do not edit directly

#![allow(missing_docs)]
""")
        # Write the Unicode version & doc comment
        of.write("""
/// The version of [Unicode](http://www.unicode.org/)
/// that this version of unicode-width is based on.
pub const UNICODE_VERSION: (u8, u8, u8) = (%s, %s, %s);
""" % version)
        # Write the data
        of.write("""
pub const CODEPOINT_PROPERTIES: &[u32] = &[
""")
        cur = 0
        for prop in compressed_props: 
            of.write(f"0x{prop:06X}, ")
            # cur is used to add some line breaks so the file looks a bit nicer to the human eye
            if cur == 9:
                of.write("\n")
                cur = 0
            else:
                cur += 1
        of.write("""
];
""")

if __name__ == "__main__":
    version = load_unicode_version()
    print("Generating module for Unicode %s.%s.%s" % version)
    eaw_map = load_condensed_widths()
    cat_map = load_condensed_categories()
    property_map = list(map(merge_properties, zip(eaw_map, cat_map)))
    # Override for Hangul Jamo medial vowels & final consonants
    for i in range(0x1160, 0x11FF + 1):
        property_map[i] = CondensedWidth.ZERO
    # Override for soft hyphen
    property_map[0x00AD] = CondensedWidth.NARROW
    compressed_map = compress_properties(property_map)
    print(f"Compressed partition has {len(compressed_map)} keys")
    print(f"(expect ~{math.ceil(math.log(len(compressed_map), 17))} worst-case cache misses)")
    emit_module("generated.rs", version, compressed_map)